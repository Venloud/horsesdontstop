"""Pure read-only health checks for the Windows local autopilot watcher."""
import json
from datetime import datetime, timedelta, timezone

from bot import autopilot_watchdog as wd

NOW = datetime(2026, 11, 27, 17, 30, tzinfo=timezone.utc)


def receipt(tmp_path, *, age=0, status="ok"):
    payload = {"schema": 1, "at": (NOW - timedelta(seconds=age)).isoformat(),
               "status": status, "trade": True}
    (tmp_path / "autopilot-runtime.json").write_text(json.dumps(payload), encoding="utf-8")


def test_missing_receipt_alerts_only_when_market_open(tmp_path):
    assert wd.assess_receipt(tmp_path, now=NOW, market_open=True) == "RECEIPT_MISSING"
    assert wd.assess_receipt(tmp_path, now=NOW, market_open=False) == "MARKET_CLOSED"


def test_exact_fifteen_minutes_is_healthy_older_is_stale(tmp_path):
    receipt(tmp_path, age=900)
    assert wd.assess_receipt(tmp_path, now=NOW, market_open=True) == "OK"
    receipt(tmp_path, age=901)
    assert wd.assess_receipt(tmp_path, now=NOW, market_open=True) == "RECEIPT_STALE"


def test_recent_error_is_alerted_even_with_fresh_receipt(tmp_path):
    receipt(tmp_path, status="error")
    assert wd.assess_receipt(tmp_path, now=NOW, market_open=True) == "AUTOPILOT_ERROR"


def test_malformed_or_future_receipts_do_not_pass(tmp_path):
    p = tmp_path / "autopilot-runtime.json"
    p.write_text("{invalid", encoding="utf-8")
    assert wd.assess_receipt(tmp_path, now=NOW, market_open=True) == "RECEIPT_INVALID"
    p.write_text(json.dumps({"at": "2026-11-27T12:30:00", "status": "ok"}), encoding="utf-8")
    assert wd.assess_receipt(tmp_path, now=NOW, market_open=True) == "RECEIPT_INVALID"
    receipt(tmp_path, age=-120)
    assert wd.assess_receipt(tmp_path, now=NOW, market_open=True) == "RECEIPT_INVALID"


def test_off_hours_never_alerts_on_stale_or_bad_receipts(tmp_path):
    receipt(tmp_path, age=7200, status="error")
    assert wd.assess_receipt(tmp_path, now=NOW, market_open=False) == "MARKET_CLOSED"


def test_main_reports_stale_with_no_network_or_notifications(tmp_path, monkeypatch, capsys):
    receipt(tmp_path, age=901)
    monkeypatch.setattr(wd, "paper_market_open", lambda: True)
    monkeypatch.setattr(wd, "notify_alert", lambda *a, **k: "NOT_CONFIGURED")
    monkeypatch.setattr(wd, "datetime", type("Clock", (), {"now": staticmethod(lambda tz: NOW)}))
    assert wd.main(["--folder", str(tmp_path)]) == 3
    assert "ALERT RECEIPT_STALE" in capsys.readouterr().out


def test_market_closed_main_ignores_missing_receipt(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(wd, "paper_market_open", lambda: False)
    assert wd.main(["--folder", str(tmp_path)]) == 0
    assert "WATCHDOG MARKET_CLOSED" in capsys.readouterr().out
