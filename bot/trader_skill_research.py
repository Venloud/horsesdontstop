"""HorsesDontStop -> Trader Skill research-only integration, 2026-10-20.

No broker imports, network requests, journal writes, CLI pass-through to
vendor executors, or change to the frozen preregistered strategy universe.
Vendor algorithms operate only on *provided* snapshots/fixtures. This tool
is NOT a trading gate and its PASS is never authority to submit an order.

    python -m bot.trader_skill_research --capability screen --input sample.json
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
from importlib.util import module_from_spec, spec_from_file_location
import json
import math
from pathlib import Path
import re
import sys
from functools import lru_cache

VENDOR = Path(__file__).resolve().parents[1] / "third_party" / "trader_skill"
UPSTREAM_SHA = "5189e79b9a480755a3733ac9713f5353981dbe18"
TOOLS = {
    "analyst": ("trader-analyst", "facts.py"),
    "screener": ("trader-screener", "screen.py"),
    "news": ("trader-news", "news.py"),
    "risk": ("trader-risk", "check.py"),
    "alerts": ("trader-alerts", "alerts.py"),
    "budget": ("trader-budget", "size.py"),
}
_TICKER = re.compile(r"[A-Z][A-Z0-9.]{0,9}\Z")


class ResearchBlocked(ValueError):
    """Malformed, untrusted or unsafe research payload."""


@lru_cache(maxsize=6)
def _vendor(capability):
    folder, script = TOOLS[capability]
    filename = VENDOR / folder / script
    # No ability to import the unvendored trader-broker order pathway.
    if not filename.is_file():
        raise ResearchBlocked("Missing pinned research vendor module")
    spec = spec_from_file_location("hds_readonly_vendor_" + capability, filename)
    if spec is None or spec.loader is None:
        raise ResearchBlocked("Unverifiable research vendor module")
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _dict(value, field):
    if type(value) is not dict:
        raise ResearchBlocked(field + " must be an object")
    return value


def _list(value, field):
    if type(value) is not list:
        raise ResearchBlocked(field + " must be a list")
    return value


def _finite_positive(value):
    return type(value) in (int, float) and math.isfinite(value) and value > 0


def _ticker(value):
    return type(value) is str and _TICKER.fullmatch(value) is not None


def _research_only(value, capability):
    return {
        "capability": capability, "status": "research_only",
        "external_data_verified": False,
        "paper_execution_authorized": False,
        "broker_orders_submitted": 0,
        "authoritative_trade_gate": False,
        "vendor_upstream_commit": UPSTREAM_SHA,
        "result": value,
    }


def evaluate(capability: str, payload: dict) -> dict:
    """Six allowlisted pure research operations, no broker execution."""
    if capability not in TOOLS:
        raise ResearchBlocked("Unknown or prohibited research capability")
    payload = _dict(payload, "payload")
    engine = _vendor(capability)

    if capability == "analyst":
        facts = _dict(payload.get("companyfacts"), "companyfacts")
        quarters = payload.get("quarters", 6)
        if type(quarters) is not int or not 1 <= quarters <= 12:
            raise ResearchBlocked("quarters must be an integer from 1 to 12")
        # SEC XBRL facts are from a public filing; data still needs source
        # provenance/date attestation before comparing returns or screening.
        result = engine.analyse(
            facts, quarters=quarters,
            name=facts.get("entityName"),
        )
        return _research_only(result, capability)

    if capability == "screener":
        companies = _list(payload.get("companies"), "companies")
        rules = _dict(payload.get("rules"), "rules")
        if rules.get("missing", "fail") != "fail":
            raise ResearchBlocked("Missing fundamental metrics must fail")
        engine.check_rules(rules)
        if not companies or len(companies) > 500:
            raise ResearchBlocked("Research universe must contain 1 to 500 stocks")
        for row in companies:
            _dict(row, "company row")
            if not _ticker(row.get("ticker")):
                raise ResearchBlocked("Invalid ticker in research universe")
        selected = engine.screen(companies, rules)
        return _research_only({
            "scored_strategy_backtest": False,
            "screened": selected,
        }, capability)

    if capability == "news":
        ticker = payload.get("ticker")
        if not _ticker(ticker):
            raise ResearchBlocked("Invalid news watchlist ticker")
        if not isinstance(payload.get("rss_xml"), str) and not isinstance(
                payload.get("sec_atom_xml"), str):
            raise ResearchBlocked("News requires an actual RSS or SEC XML snapshot")
        items = []
        if isinstance(payload.get("rss_xml"), str):
            items.extend(engine.parse_rss(payload["rss_xml"], ticker))
        if isinstance(payload.get("sec_atom_xml"), str):
            items.extend(engine.parse_sec_atom(payload["sec_atom_xml"], ticker))
        # Avoid serializing unknown vendor datetime object types.
        for item in items:
            for key in ("time",):
                if isinstance(item.get(key), datetime):
                    item[key] = item[key].isoformat()
        return _research_only({
            "items": items,
            "note": "Public headlines are leads, not broker trading signals.",
        }, capability)

    if capability == "risk":
        orders = _list(payload.get("proposals"), "proposals")
        if not orders or len(orders) > 50:
            raise ResearchBlocked("Need 1 to 50 research proposals")
        provided = _dict(payload.get("config"), "config")
        portfolio = _dict(payload.get("portfolio"), "portfolio")
        prices = _dict(payload.get("prices"), "prices")
        if provided.get("paper") is not True or any(
            provided.get(flag) is not False
            for flag in ("allow_margin", "allow_short", "allow_options")
        ):
            raise ResearchBlocked("PAPER-only no-margin/no-short/no-option policy required")
        if "cash" not in portfolio:
            raise ResearchBlocked("Cannot infer buying power from a missing portfolio")
        for order in orders:
            _dict(order, "proposed order")
            if any(k in order for k in (
                "order_class", "advanced_instructions", "stop_loss",
                "take_profit", "extended_hours", "client_order_id",
            )):
                raise ResearchBlocked("Advanced/protected order requires original HDS gate")
        report = engine.check(
            orders, provided, portfolio=portfolio, prices=prices,
            state=payload.get("prior_order_state"), today=payload.get("date"))
        return _research_only({
            "upstream_advisory": report,
            "protective_order_lifecycle_checked": False,
            "daily_loss_or_equity_peak_verified": False,
            "explicit_owner_order_approval": False,
            "note": "Vendor PASS is not HDS execution permission.",
        }, capability)

    if capability == "alerts":
        rules = _list(payload.get("rules"), "rules")
        quotes = _dict(payload.get("quotes"), "quotes")
        state = _dict(payload.get("state", {}), "state")
        utc = payload.get("observed_at_utc")
        if type(utc) is not str:
            raise ResearchBlocked("A timezone-aware observation timestamp is required")
        try:
            moment = datetime.fromisoformat(utc.replace("Z", "+00:00"))
        except ValueError:
            raise ResearchBlocked("Invalid alert observation timestamp") from None
        if moment.tzinfo is None or moment.utcoffset() is None:
            raise ResearchBlocked("Alert timestamp must include UTC offset")
        if len(rules) > 100:
            raise ResearchBlocked("Too many advisory alerts")
        for ticker, quote in quotes.items():
            if not _ticker(ticker):
                raise ResearchBlocked("Invalid alert ticker")
            _dict(quote, "quote")
            if not _finite_positive(quote.get("price")):
                raise ResearchBlocked("Invalid alert price")
        status = engine.market_status(moment.astimezone(timezone.utc))
        rings, next_state = engine.evaluate(
            rules, quotes, status, state, moment.astimezone(timezone.utc))
        return _research_only({
            "rings": rings, "next_state": next_state,
            "market_clock_trading_authority": False,
            "quotes_for_order_execution": False,
            "note": "Static vendor calendar is informational; use independently verified NYSE/broker clock for execution.",
        }, capability)

    if capability == "budget":
        config = _dict(payload.get("config"), "config")
        if config.get("paper") is not True or config.get("fractional") is not False:
            raise ResearchBlocked("Require PAPER-only and whole-share sizing")
        budget = payload.get("budget")
        if not _finite_positive(budget):
            raise ResearchBlocked("Explicit positive research budget is required")
        rows = engine.parse_weights(payload.get("weights"))
        prices = _dict(payload.get("prices"), "prices")
        result = engine.size(
            rows, config, budget=budget, prices=prices, whole=True)
        previews = engine.orders_from(result)
        return _research_only({
            "allocation": result,
            "non_executable_proposals": previews,
            "stop_distance_risk_verified": False,
            "note": "Only notional diversification, NOT HDS 1% stop-distance risk. Never submit proposals.",
        }, capability)

    raise ResearchBlocked("Capability not implemented")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--capability", required=True, choices=tuple(TOOLS))
    parser.add_argument("--input", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        if not args.input.is_file() or args.input.stat().st_size > 2_000_000:
            raise ResearchBlocked("Input file missing or too large")
        payload = json.loads(args.input.read_text(encoding="utf-8"))
        outcome = evaluate(args.capability, payload)
        print(json.dumps(outcome, indent=2, sort_keys=True, allow_nan=False))
        return 0
    except Exception:
        # Never echo vendor exceptions, input contents or possible secrets.
        print("BLOCKED: research adapter rejected invalid or unverified input.",
              file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
