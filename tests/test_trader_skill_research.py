"""Offline integration contract for all six MIT Trader Skill research tools."""
from datetime import datetime, timezone
import json
from pathlib import Path

import pytest

from bot.trader_skill_research import (
    VENDOR, TOOLS, ResearchBlocked, _vendor, evaluate, main,
)


@pytest.fixture
def risk_context():
    return {
        "config": {
            "paper": True, "allow_margin": False, "allow_short": False,
            "allow_options": False, "max_order_usd": 100,
            "cash_buffer_pct": 10, "max_position_pct": 40,
            "min_order_usd": 5, "max_orders_per_day": 5,
        },
        "portfolio": {"cash": 500, "positions": []},
        "prices": {"AAPL": 100.0, "MSFT": 200.0},
        "proposals": [
            {"symbol": "AAPL", "side": "buy", "notional": "50",
             "type": "market", "time_in_force": "day"},
        ],
        "date": "2026-10-20",
    }


def assert_readonly(res, cap):
    assert res["capability"] == cap
    assert res["status"] == "research_only"
    assert res["external_data_verified"] is False
    assert res["paper_execution_authorized"] is False
    assert res["authoritative_trade_gate"] is False
    assert res["broker_orders_submitted"] == 0
    assert res["vendor_upstream_commit"] == "5189e79b9a480755a3733ac9713f5353981dbe18"


def test_all_six_vendored_tools_pinned_and_importable():
    assert set(TOOLS) == {"analyst", "screener", "news", "risk", "alerts", "budget"}
    for name in TOOLS:
        assert _vendor(name) is _vendor(name)
        dirname, filename = TOOLS[name]
        assert (VENDOR / dirname / filename).is_file()
        assert (VENDOR / dirname / "SKILL.md").is_file()
    assert (VENDOR / "LICENSE").read_text().startswith("MIT License")
    assert not (VENDOR / "trader-broker").exists()


def test_fundamental_screener_compares_actual_rules():
    res = evaluate("screener", {
        "companies": [
            {"ticker":"AAPL","revenue_growth_yoy":15.0,"operating_margin":30},
            {"ticker":"KO","revenue_growth_yoy":3.0,"operating_margin":22},
            {"ticker":"MSFT","revenue_growth_yoy":None,"operating_margin":31},
        ],
        "rules":{"name":"synthetic","missing":"fail",
                 "rules":[{"metric":"revenue_growth_yoy","op":">=","value":10}],
                 "rank":[{"metric":"revenue_growth_yoy","order":"desc"}]},
    })
    assert_readonly(res,"screener")
    result=res["result"]["screened"]
    assert result[0]["ticker"] == "AAPL" and result[0]["passed"]
    assert {i["ticker"] for i in result if not i["passed"]} == {"KO","MSFT"}
    assert res["result"]["scored_strategy_backtest"] is False


def test_screener_never_permits_skipping_missing_metrics():
    with pytest.raises(ResearchBlocked, match="fail"):
        evaluate("screener", {"companies":[{"ticker":"AAPL"}],
                              "rules":{"missing":"skip","rules":[]}})


def test_upstream_sec_analyst_parses_filed_companyfacts_fixture():
    path = VENDOR / "tests/fixtures/trader-analyst/companyfacts-CIK0000021344.json"
    facts=json.loads(path.read_text())
    res=evaluate("analyst", {"companyfacts":facts,"quarters":6})
    assert_readonly(res,"analyst")
    assert res["result"]["entity"]
    assert isinstance(res["result"]["rows"],list)
    assert isinstance(res["result"]["ttm"],dict)


def test_analyst_refuses_noninteger_quarters():
    with pytest.raises(ResearchBlocked):
        evaluate("analyst", {"companyfacts":{},"quarters":True})


def test_upstream_news_parses_headlines_and_sec_filings_offline():
    rss=(VENDOR/"tests/fixtures/trader-news/yahoo-AAPL.xml").read_text()
    sec=(VENDOR/"tests/fixtures/trader-news/sec-8-K-0000320193.xml").read_text()
    res=evaluate("news",{"ticker":"AAPL","rss_xml":rss,"sec_atom_xml":sec})
    assert_readonly(res,"news")
    items=res["result"]["items"]
    assert items and any(x["kind"]=="headline" for x in items)
    assert any(x["kind"]=="filing" for x in items)
    assert "Public headlines are leads" in res["result"]["note"]
    json.dumps(res)


def test_news_does_not_fetch_or_infer_missing_data():
    with pytest.raises(ResearchBlocked):
        evaluate("news", {"ticker":"AAPL"})


def test_risk_checks_independently_without_signalling_order_release(risk_context):
    res=evaluate("risk",risk_context)
    assert_readonly(res,"risk")
    r=res["result"]["upstream_advisory"]
    assert r["paper"] is True
    assert r["pass"]==1 and r["block"]==0
    assert res["result"]["protective_order_lifecycle_checked"] is False
    assert res["result"]["daily_loss_or_equity_peak_verified"] is False
    assert res["result"]["explicit_owner_order_approval"] is False


def test_risk_blocks_excess_order_but_does_not_place_it(risk_context):
    risk_context["proposals"][0]["notional"]="450"
    res=evaluate("risk",risk_context)
    assert res["result"]["upstream_advisory"]["block"]==1
    assert res["broker_orders_submitted"]==0


@pytest.mark.parametrize("flag",["paper","allow_margin","allow_short","allow_options"])
def test_risk_never_relaxes_paper_or_prohibited_instruments(risk_context,flag):
    risk_context["config"][flag] = False if flag=="paper" else True
    with pytest.raises(ResearchBlocked):
        evaluate("risk",risk_context)


@pytest.mark.parametrize("alter",[
    {"order_class":"bracket"},
    {"stop_loss":{"stop_price":"90"}},
    {"client_order_id":"my-broker-id"},
    {"extended_hours":True},
])
def test_risk_refuses_to_approve_advanced_protected_orders(risk_context,alter):
    risk_context["proposals"][0].update(alter)
    with pytest.raises(ResearchBlocked):
        evaluate("risk",risk_context)


def test_whole_share_budget_sizing_advisory_only():
    out=evaluate("budget",{
        "weights":{"AAPL":60,"MSFT":40},
        "budget":500,
        "config":{"paper":True,"fractional":False,"cash_buffer_pct":10,
                  "max_position_pct":60,"min_order_usd":5},
        "prices":{"AAPL":100.0,"MSFT":200.0},
    })
    assert_readonly(out,"budget")
    v=out["result"]
    assert v["allocation"]["budget"]==500
    assert v["allocation"]["whole_shares"] is True
    assert all("qty" in x and "notional" not in x for x in v["non_executable_proposals"])
    assert v["stop_distance_risk_verified"] is False


def test_budget_requires_explicit_paper_whole_shares_and_equity():
    base={"weights":{"AAPL":100},"budget":500,
          "config":{"paper":True,"fractional":False}, "prices":{"AAPL":50}}
    for altered in [{"config":{"paper":False,"fractional":False}},
                    {"config":{"paper":True,"fractional":True}},
                    {"budget":-500}]:
        p={**base,**altered}
        with pytest.raises(ResearchBlocked):
            evaluate("budget",p)


def test_informational_price_alerts_deduplicate_in_offline_state():
    p={"rules":[{"id":"alert1","type":"above","symbol":"AAPL","price":150}],
       "quotes":{"AAPL":{"price":170.0}},
       "state":{},"observed_at_utc":"2026-10-20T14:00:00Z"}
    res=evaluate("alerts",p)
    assert_readonly(res,"alerts")
    assert len(res["result"]["rings"])==1
    assert res["result"]["market_clock_trading_authority"] is False
    assert res["result"]["quotes_for_order_execution"] is False
    again=evaluate("alerts",{**p,"state":res["result"]["next_state"]})
    assert again["result"]["rings"]==[]


@pytest.mark.parametrize("utc",["2026-10-20T14:00:00", "not-a-time"])
def test_alerts_require_explicit_timezone(utc):
    with pytest.raises(ResearchBlocked):
        evaluate("alerts",{"rules":[],"quotes":{},"observed_at_utc":utc})


def test_rejects_unknown_or_broker_capability():
    with pytest.raises(ResearchBlocked):
        evaluate("broker",{})
    with pytest.raises(ResearchBlocked):
        evaluate("copycat",{})


def test_cli_never_echoes_untrusted_input_or_exception(tmp_path,capsys):
    path=tmp_path/"bad.json"
    path.write_text('{"secret":"SECRET_SHOULD_NOT_LEAK"}')
    assert main(["--capability","risk","--input",str(path)])==2
    x=capsys.readouterr()
    assert "SECRET_SHOULD_NOT_LEAK" not in x.out+x.err


def test_cli_operates_with_no_external_network(tmp_path,capsys):
    path=tmp_path/"screen.json"
    path.write_text(json.dumps({
        "companies":[{"ticker":"AAPL","net_margin":20}],
        "rules":{"missing":"fail","rules":[{"metric":"net_margin","op":">=","value":15}]}
    }))
    assert main(["--capability","screener","--input",str(path)])==0
    r=json.loads(capsys.readouterr().out)
    assert_readonly(r,"screener")


def test_original_execution_path_and_main_workflows_untouched():
    # A research-only vendor must never be coupled to the broker POST.
    import inspect
    from bot import trader_skill_research as bridge
    source=inspect.getsource(bridge)
    for term in ("submit_order(", "cancel_order(", "TradingClient(", "PAPER_ORDER_SUBMISSIONS_RELEASED = True"):
        assert term not in source
    assert "trader-broker" not in TOOLS
