"""Strategy engine, signal library, portfolio simulator and insider data (2026-11-26)."""
import io
import random
import zipfile
from datetime import date, timedelta

import pytest

from bot.insider_signals import current_signals, parse_dataset, parse_form4
from bot.insider_universe import liquid, signal_symbols, trimmed
from bot.portfolio_sim import System, max_drawdown, metrics, simulate
from bot.strategies import DailyBar, signal_score
from bot.strategy_engine import autopilot_settings, choose_champion, qualifies, research
from bot.system_signals import _prior_max, insider_scores, latest_score, signal_series


def walk(n=400, seed=1, drift=0.0005, start=date(2021, 1, 4), vol=0.015, base=100.0):
    rnd = random.Random(seed)
    out, px, d = [], base, start
    while len(out) < n:
        if d.weekday() < 5:
            o = px
            px = max(1.0, px * (1 + drift + rnd.gauss(0, vol)))
            hi, lo = max(o, px) * (1 + abs(rnd.gauss(0, 0.004))), min(o, px) * (1 - abs(rnd.gauss(0, 0.004)))
            out.append(DailyBar(d, o, hi, lo, px, rnd.uniform(5e5, 2e6)))
        d += timedelta(days=1)
    return out


@pytest.mark.parametrize("family", ["trend_following", "momentum_volume", "high_52_week"])
def test_library_matches_original_strategy_code(family):
    bars = walk(320, seed=7, drift=0.001)
    series = signal_series(family, {}, bars)
    for t in range(len(bars)):
        want = signal_score(family, bars[:t + 1])
        got = series[t]
        assert (want is None) == (got is None), (family, t)
        if want is not None:
            assert got == pytest.approx(want, rel=1e-9)


def test_prior_max_excludes_today():
    assert _prior_max([1, 5, 2, 3, 9], 2) == [None, None, 5, 5, 3]


def test_ma_trend_and_breakout():
    up = walk(260, drift=0.003, vol=0.002)
    assert latest_score("ma_trend", {"ma": 200}, up) > 0
    assert latest_score("breakout", {"lookback": 20}, up) is not None
    down = walk(260, drift=-0.003, vol=0.002)
    assert latest_score("ma_trend", {"ma": 200}, down) is None


def test_insider_scores_window_and_threshold():
    bars = walk(30)
    ev = [{"filed": bars[10].day, "owner": "A", "value": 60_000.0},
          {"filed": bars[11].day, "owner": "B", "value": 40_000.0}]
    s = insider_scores(ev, bars, valid_sessions=3, min_value=50_000)
    assert s[9] is None and s[10] is not None and s[12] > s[10] and s[14] is None


def data_two(seed=3):
    spy = walk(500, seed=seed, drift=0.0006)
    return {"SPY": spy, "UP": walk(500, seed=seed + 1, drift=0.002, vol=0.01),
            "DOWN": walk(500, seed=seed + 2, drift=-0.002, vol=0.01)}


def test_simulation_is_cash_only_and_respects_slots():
    data = data_two()
    res = simulate(System("momentum", (("lookback", 63),), filter_ma=0, slots=1), data)
    assert res.curve and all(e > 0 for _, e in res.curve)
    held = {}
    for t in res.trades:                      # one slot: trades never overlap
        assert all(not (t[0] < e and s < t[1]) for s, e in held.items())
        held[t[0]] = t[1]
    m = metrics(res, data["SPY"])
    assert m["trades"] == len(res.trades) and 0 <= m["max_drawdown"] < 1


def test_stop_exit_and_costs():
    d0 = date(2024, 1, 2)
    days = [d0 + timedelta(days=i) for i in range(300) if (d0 + timedelta(days=i)).weekday() < 5][:120]
    spy = [DailyBar(d, 100 + i * 0.1, 101 + i * 0.1, 99 + i * 0.1, 100 + i * 0.1, 1e6) for i, d in enumerate(days)]
    x = [DailyBar(d, 50 + i * 0.5, 51 + i * 0.5, 49.5 + i * 0.5, 50.4 + i * 0.5, 1e6) for i, d in enumerate(days[:100])]
    crash = days[100]
    x.append(DailyBar(crash, 90, 90, 40, 41, 1e6))
    x += [DailyBar(d, 41, 42, 40, 41, 1e6) for d in days[101:]]
    res = simulate(System("ma_trend", (("ma", 50),), filter_ma=0, slots=1, stop=0.03, trail=0.05,
                          max_sessions=500), {"SPY": spy, "X": x})
    assert res.trades and res.trades[0][4] in ("gap stop", "stop")
    assert res.trades[0][3] < 0.30


def test_max_drawdown():
    assert max_drawdown([1, 2, 1, 3]) == pytest.approx(0.5)


def test_research_and_champion_cycle():
    data = data_two()
    systems = [System("momentum", (("lookback", 63),), filter_ma=0, slots=2),
               System("pullback", (("days", 3), ("ma", 100)), filter_ma=0, slots=2,
                      take_profit=0.04, max_sessions=5)]
    rows = research(data, systems=systems)
    assert len(rows) == 2 and set(rows[0]["checks"]) == {
        "in_sample", "both_halves", "out_of_sample", "high_costs", "without_10_best"}
    state, decision = choose_champion({}, systems, data)
    assert decision["action"] in ("switch", "cash")
    if state["champion"]:
        kept, d2 = choose_champion(state, systems, data)
        assert d2["action"] == "keep" and kept is state          # < 5 sessions held


def test_provisional_champion_when_nothing_qualifies():
    data = data_two()
    systems = [System("momentum", (("lookback", 63),), filter_ma=0, slots=2)]
    from bot import strategy_engine as se
    real = se.qualifies
    se.qualifies = lambda w: False
    try:
        state, d = choose_champion({}, systems, data, oos_rank={systems[0].id: 1.0})
        assert d["action"] == "provisional" and state["provisional"] is True
        again, d2 = choose_champion(state, systems, data, oos_rank={systems[0].id: 1.0})
        assert d2["action"] == "keep provisional"
        none, d3 = choose_champion({}, systems, data)
        assert d3["action"] == "cash" and none["champion"] is None
    finally:
        se.qualifies = real
    st = autopilot_settings(systems[0].to_dict(), provisional=True)
    assert st["size_scale"] == 0.5 and st["system_id"].startswith("PROVISIONAL")


def test_safety_net_halves_size_in_rough_patch():
    from bot.strategy_engine import safety_net
    d = date(2026, 1, 1)
    assert safety_net([(d, 1.0), (d, 1.2), (d, 1.1)])["active"] is False      # -8.3%
    net = safety_net([(d, 1.0), (d, 1.2), (d, 1.05)])                          # -12.5%
    assert net["active"] is True
    sysd = System("momentum", (("lookback", 63),)).to_dict()
    assert autopilot_settings(sysd, net=net)["size_scale"] == 0.5
    assert autopilot_settings(sysd, provisional=True, net=net)["size_scale"] == 0.5   # never stacks
    assert autopilot_settings(sysd, net={"active": False})["size_scale"] == 1.0


def test_brake_uses_shadow_track_and_recovers():
    data = data_two()
    s = System("momentum", (("lookback", 63),), filter_ma=0, slots=2)
    braked = simulate(System("momentum", (("lookback", 63),), filter_ma=0, slots=2,
                             brake_dd=0.0001, brake_scale=0.0), data)
    plain = simulate(s, data)
    assert braked.trades                 # a pause lifts when the shadow track recovers
    assert len(braked.trades) < len(plain.trades)


def test_qualifies_uses_profit_not_just_win_rate():
    w = {"return": 0.06, "drawdown": 0.08, "trades": 12, "win_rate": 0.38, "profit_factor": 1.6}
    assert qualifies(w)
    assert not qualifies({**w, "return": -0.01})
    assert not qualifies({**w, "win_rate": 0.2})


def test_settings_for_cash_and_system():
    assert autopilot_settings(None)["strategy"] == "cash"
    s = autopilot_settings(System("momentum", (("lookback", 126),), slots=10, trail=0.1).to_dict())
    assert s["strategy"] == "momentum" and s["params"] == {"lookback": 126}
    assert s["max_positions"] == 10 and s["trail"] == 0.1 and "SPY" in s["universe"]


FORM4 = """<SEC-DOCUMENT><XML><?xml version="1.0"?>
<ownershipDocument>
 <issuer><issuerTradingSymbol>abcd</issuerTradingSymbol></issuer>
 <reportingOwner><reportingOwnerId><rptOwnerName>Doe Jane</rptOwnerName></reportingOwnerId>
  <reportingOwnerRelationship><isDirector>0</isDirector><isOfficer>1</isOfficer>
   <officerTitle>CEO</officerTitle></reportingOwnerRelationship></reportingOwner>
 <nonDerivativeTable>
  <nonDerivativeTransaction><transactionCoding><transactionCode>P</transactionCode></transactionCoding>
   <transactionAmounts><transactionShares><value>1000</value></transactionShares>
   <transactionPricePerShare><value>25.5</value></transactionPricePerShare>
   <transactionAcquiredDisposedCode><value>A</value></transactionAcquiredDisposedCode></transactionAmounts>
  </nonDerivativeTransaction>
  <nonDerivativeTransaction><transactionCoding><transactionCode>S</transactionCode></transactionCoding>
   <transactionAmounts><transactionShares><value>9000</value></transactionShares>
   <transactionPricePerShare><value>25</value></transactionPricePerShare>
   <transactionAcquiredDisposedCode><value>D</value></transactionAcquiredDisposedCode></transactionAmounts>
  </nonDerivativeTransaction>
 </nonDerivativeTable>
</ownershipDocument></XML></SEC-DOCUMENT>"""


def test_parse_form4_counts_only_open_market_buys():
    ev = parse_form4(FORM4, "acc-1", date(2026, 11, 25))
    assert ev == [{"filed": date(2026, 11, 25), "symbol": "ABCD", "owner": "Doe Jane",
                   "title": "CEO", "value": 25500.0, "accession": "acc-1", "source": "edgar-daily"}]
    assert parse_form4(FORM4.replace("<isOfficer>1", "<isOfficer>0"), "a", date(2026, 1, 1)) == []


def test_parse_dataset(tmp_path):
    def tsv(head, *rows):
        return "\t".join(head) + "\n" + "".join("\t".join(r) + "\n" for r in rows)
    path = tmp_path / "2025q1_form345.zip"
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("SUBMISSION.tsv", tsv(["ACCESSION_NUMBER", "FILING_DATE", "DOCUMENT_TYPE",
                                          "ISSUERTRADINGSYMBOL"],
                                         ["a1", "03-JAN-2025", "4", "xyz"], ["a2", "03-JAN-2025", "4", "QQQQ"]))
        z.writestr("REPORTINGOWNER.tsv", tsv(["ACCESSION_NUMBER", "RPTOWNERNAME", "RPTOWNER_RELATIONSHIP",
                                              "RPTOWNER_TITLE"],
                                             ["a1", "Smith", "Director", ""], ["a2", "Fund", "TenPercentOwner", ""]))
        z.writestr("NONDERIV_TRANS.tsv", tsv(["ACCESSION_NUMBER", "TRANS_CODE", "TRANS_SHARES",
                                              "TRANS_PRICEPERSHARE", "TRANS_ACQUIRED_DISP_CD"],
                                             ["a1", "P", "1000", "20", "A"], ["a2", "P", "99999", "20", "A"]))
    ev = parse_dataset(path)
    assert [(e["symbol"], e["value"], e["filed"]) for e in ev] == [("XYZ", 20000.0, date(2025, 1, 3))]


def test_current_signals_and_insider_universe():
    sessions = [date(2026, 11, d) for d in (19, 20, 23, 24, 25)]
    ev = [{"filed": date(2026, 11, 24), "symbol": "ABC", "owner": "A", "value": 80_000.0},
          {"filed": date(2026, 11, 25), "symbol": "ABC", "owner": "B", "value": 30_000.0},
          {"filed": date(2026, 11, 10), "symbol": "OLD", "owner": "C", "value": 900_000.0}]
    sig = current_signals(ev, sessions)
    assert set(sig) == {"ABC"} and sig["ABC"] > 5
    assert set(signal_symbols(ev, min_value=50_000)) == {"ABC", "OLD"}
    bars = walk(300)
    t = trimmed(bars, [bars[100].day])
    assert t[0].day == bars[90].day and len(t) == 140 + 10
    assert liquid(bars) and not liquid(walk(300, base=2.0))
