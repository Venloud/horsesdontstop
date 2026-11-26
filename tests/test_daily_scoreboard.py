"""Daily market recorder + strategy scoreboard. Offline, synthetic data only."""
from datetime import date, timedelta

from bot.daily_market_recorder import merge_bars, read_history, write_history
from bot.strategies import DailyBar
import bot.strategy_scoreboard as sb


def bar(d, c=100.0):
    return DailyBar(d, c, c + 1, c - 1, c, 1000)


D = [date(2026, 11, 16) + timedelta(days=i) for i in range(5)]


def test_merge_only_appends_newer_sessions():
    old = [bar(D[0]), bar(D[1])]
    merged = merge_bars(old, [bar(D[1], 999), bar(D[2])])
    assert [b.day for b in merged] == D[:3] and merged[1].close == 100.0


def test_history_round_trips(tmp_path):
    write_history(tmp_path, "SPY", [bar(D[0]), bar(D[1], 101.5)])
    got = read_history(tmp_path, ["SPY", "XOM"])
    assert [b.close for b in got["SPY"]] == [100.0, 101.5] and got["XOM"] == []


def curve(values):
    return [(D[0] + timedelta(days=i), v) for i, v in enumerate(values)]


def test_leader_is_chosen_from_past_only_and_earns_next_day():
    # A leads on day 1 (trailing 1-day), then collapses on day 2.
    curves = {"A": curve([100, 120, 60, 60]), "B": curve([100, 101, 102, 103])}
    r = sb.follow_the_leader(curves, lookback=1, switch_cost_bps=0)
    # t=1: leader A (120/100) earns 60/120; t=2: leader B (102/101) earns 103/102
    assert abs(r["final_equity"] - 500 * 0.5 * 103 / 102) < 0.02
    assert r["switches"] == 1 and r["current_pick"] == "B"


def test_switch_cost_is_charged():
    curves = {"A": curve([100, 120, 60, 60]), "B": curve([100, 101, 102, 103])}
    free = sb.follow_the_leader(curves, 1, switch_cost_bps=0)["final_equity"]
    paid = sb.follow_the_leader(curves, 1, switch_cost_bps=60)["final_equity"]
    assert paid < free


def test_scoring_starts_after_last_missing_bar(monkeypatch):
    days = [date(2024, 1, 1) + timedelta(days=i) for i in range(400)]
    data = {"SPY": [bar(d) for d in days]}
    monkeypatch.setattr(sb, "STOCKS", ("AAA",))
    data["AAA"] = [bar(d) for i, d in enumerate(days) if i != 300]
    assert sb.first_clean_index(data) == 301
    data["AAA"] = [bar(d) for d in days]
    assert sb.first_clean_index(data) == sb.WARMUP


def test_scoreboard_runs_only_runnable_variants():
    ids = sb.runnable_variants()
    assert len(ids) == 16
    assert not any(i.startswith(("etf_dual_momentum", "overnight_drift")) for i in ids)


# ---- promotion rule ----------------------------------------------------------
import bot.strategy_promotion as pr


def flat_curve(n, start=500.0, step=0.0):
    return [(D[0] + timedelta(days=i), start + step * i) for i in range(n)]


def wins(curve, k, pnl=2.0):
    return [{"exit_day": str(curve[i][0]), "pnl": pnl} for i in range(1, k + 1)]


def test_no_strategy_qualifies_means_cash():
    c = {"A": flat_curve(40)}
    state, ev = pr.step({"champion": None}, c, {"A": []}, 30)
    assert state["champion"] is None and ev is None


def test_qualified_strategy_is_promoted(monkeypatch):
    monkeypatch.setattr(pr, "MIN_TRADES", 3)
    c = {"A": flat_curve(40, step=1.0)}
    state, ev = pr.step({"champion": None}, c, {"A": wins(c["A"], 5)}, 30)
    assert state["champion"] == "A" and ev["to"] == "A"


def test_champion_kept_for_min_hold_even_if_failing(monkeypatch):
    c = {"A": flat_curve(40, step=-1.0), "B": flat_curve(40, step=2.0)}
    monkeypatch.setattr(pr, "MIN_TRADES", 3)
    state, ev = pr.step({"champion": "A", "since_t": 28}, c, {"A": [], "B": wins(c["B"], 5)}, 30)
    assert state["champion"] == "A" and ev is None


def test_working_champion_is_not_swapped_for_better_challenger(monkeypatch):
    monkeypatch.setattr(pr, "MIN_TRADES", 3)
    c = {"A": flat_curve(80, step=0.5), "B": flat_curve(80, step=5.0)}
    t = {"A": wins(c["A"], 5), "B": wins(c["B"], 10)}
    state, ev = pr.step({"champion": "A", "since_t": 10}, c, t, 70)
    assert state["champion"] == "A" and ev is None


def test_failing_champion_after_min_hold_is_replaced(monkeypatch):
    monkeypatch.setattr(pr, "MIN_TRADES", 3)
    down = [(D[0] + timedelta(days=i), 500.0 - 6.0 * i) for i in range(80)]
    c = {"A": down, "B": flat_curve(80, step=2.0)}
    t = {"A": [{"exit_day": str(down[i][0]), "pnl": -6.0} for i in range(60, 70)],
         "B": wins(c["B"], 10)}
    state, ev = pr.step({"champion": "A", "since_t": 10}, c, t, 70)
    assert state["champion"] == "B" and ev["from"] == "A"
