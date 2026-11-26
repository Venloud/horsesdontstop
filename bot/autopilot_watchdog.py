"""Read-only local health watchdog for the autonomous Alpaca PAPER autopilot.

Run independently on the owner's Windows machine. A GitHub runner cannot see
%LOCALAPPDATA%\\horsesdontstop\\autopilot-runtime.json. No orders, Neon or
Cloudflare calls are made by this module.
"""
from __future__ import annotations

import argparse
import json
import os
from datetime import datetime, timezone
from pathlib import Path

MAX_AGE_SECONDS = 15 * 60
RUNTIME = Path(os.environ.get("LOCALAPPDATA", ".")) / "horsesdontstop"
MESSAGE = {
    "RECEIPT_MISSING": "autopilot runtime receipt is missing",
    "RECEIPT_INVALID": "autopilot runtime receipt is invalid",
    "RECEIPT_STALE": "autopilot runtime receipt is older than 15 minutes",
    "AUTOPILOT_ERROR": "autopilot last run did not succeed",
    "CLOCK_UNAVAILABLE": "PAPER broker market clock is unavailable",
}


def assess_receipt(folder: Path, *, now: datetime, market_open: bool) -> str:
    """Only require a fresh successful receipt while the broker market is open."""
    if market_open is not True:
        return "MARKET_CLOSED"
    path = folder / "autopilot-runtime.json"
    try:
        record = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return "RECEIPT_MISSING"
    except (OSError, UnicodeError, ValueError):
        return "RECEIPT_INVALID"
    if not isinstance(record, dict) or not isinstance(record.get("at"), str):
        return "RECEIPT_INVALID"
    try:
        stamp = datetime.fromisoformat(record["at"].replace("Z", "+00:00"))
        if stamp.tzinfo is None or now.tzinfo is None:
            return "RECEIPT_INVALID"
        age = (now.astimezone(timezone.utc) - stamp.astimezone(timezone.utc)).total_seconds()
    except (TypeError, ValueError, OverflowError):
        return "RECEIPT_INVALID"
    if age < -60:
        return "RECEIPT_INVALID"
    if age > MAX_AGE_SECONDS:
        return "RECEIPT_STALE"
    if record.get("status") != "ok":
        return "AUTOPILOT_ERROR"
    return "OK"


def paper_market_open() -> bool:
    """Use Alpaca's authoritative session clock, including holidays/half-days."""
    key = os.environ.get("ALPACA_PAPER_API_KEY")
    secret = os.environ.get("ALPACA_PAPER_SECRET_KEY")
    if not key or not secret:
        raise RuntimeError("PAPER credentials unavailable")
    from alpaca.trading.client import TradingClient
    from .paper_safety import PAPER_ENDPOINT

    trading = TradingClient(key, secret, paper=True, url_override=PAPER_ENDPOINT)
    value = getattr(trading.get_clock(), "is_open", None)
    if type(value) is not bool:
        raise RuntimeError("PAPER clock status unverified")
    return value


def notify_alert(code: str, *, folder: Path, now: datetime) -> str:
    """Optional notification, at most once per code per hour across task runs."""
    topic = os.environ.get("PAPER_MONITOR_NTFY_TOPIC")
    if not topic:
        return "NOT_CONFIGURED"
    path = folder / "autopilot-watchdog-last-alert.json"
    try:
        old = json.loads(path.read_text(encoding="utf-8"))
        if old.get("code") == code:
            prior = datetime.fromisoformat(old["at"])
            if prior.tzinfo and 0 <= (now - prior).total_seconds() < 3600:
                return "THROTTLED"
    except (FileNotFoundError, OSError, ValueError, KeyError, TypeError, AttributeError):
        pass
    try:
        from .monitor_notify import send_activity_ntfy
        if not send_activity_ntfy(
            "autopilot watchdog", MESSAGE[code], topic=topic, priority="high"
        ):
            return "DELIVERY_FAILED"
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps({"code": code, "at": now.isoformat()}), encoding="utf-8")
        os.replace(tmp, path)
        return "SENT"
    except Exception:
        # Never print transport errors; they can contain the private topic.
        return "DELIVERY_FAILED"


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Windows-local PAPER autopilot health check")
    parser.add_argument("--folder", type=Path, default=RUNTIME)
    args = parser.parse_args(argv)
    now = datetime.now(timezone.utc)
    try:
        market_open = paper_market_open()
    except Exception:
        code = "CLOCK_UNAVAILABLE"
        print("ALERT " + code + ": " + MESSAGE[code])
        print("NOTIFY " + notify_alert(code, folder=args.folder, now=now))
        return 2
    code = assess_receipt(args.folder, now=now, market_open=market_open)
    if code in ("OK", "MARKET_CLOSED"):
        print("WATCHDOG " + code)
        return 0
    print("ALERT " + code + ": " + MESSAGE[code])
    print("NOTIFY " + notify_alert(code, folder=args.folder, now=now))
    return 3


if __name__ == "__main__":
    raise SystemExit(main())
