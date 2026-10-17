"""Alert classifier tests; every input is synthetic and credentials-free."""
from datetime import datetime, timedelta, timezone
from copy import deepcopy

import pytest

from bot.monitor_policy import assess_read_only_report, unverified_incident


NOW = datetime(2026, 10, 12, 14, 35, tzinfo=timezone.utc)


def clean_report():
    return {
        "mode": "ALPACA_PAPER_READ_ONLY_PREFLIGHT",
        "observed_at_utc": NOW.isoformat(),
        "broker_paper_url": "https://paper-api.alpaca.markets",
        "market_open": True,
        "journal_kill_switch_active": True,
        "journal_submissions_enabled": False,
        "code_submissions_enabled": False,
        "can_submit_orders": False,
        "broker_write_operations": 0,
        "safety_blockers": [
            "kill_switch_active", "paper_order_submission_disabled",
            "no_fresh_realtime_quote", "missing_daily_open_equity",
            "missing_durable_equity_peak",
            "no_healthy_persistent_watchdog",
            "preflight_has_no_broker_write_capability"
        ],
        "reconciliation": {
            "consistent": True, "blockers": [],
            "open_broker_positions": 0,
            "open_broker_orders": 0,
            "unresolved_journal_intents": 0,
            "uncovered_symbols": []
        },
    }


def test_normal_disabled_paper_account_is_safe_as_monitor_only():
    a = assess_read_only_report(clean_report(), now=NOW)
    assert a.level == "idle"
    assert a.code == "idle"
    assert "no exposure" in a.summary
    assert a.positions == 0
    assert len(a.fingerprint) == 32


def test_same_failure_has_same_stable_fingerprint():
    p = clean_report()
    p["journal_submissions_enabled"] = True
    a = assess_read_only_report(p, now=NOW)
    q = deepcopy(p)
    q["safety_blockers"].reverse()
    b = assess_read_only_report(q, now=NOW + timedelta(seconds=1))
    assert a.fingerprint == b.fingerprint
    assert a.level == "critical"
    assert a.code == "security_switch"


@pytest.mark.parametrize("attribute,value", [
    ("journal_kill_switch_active", False),
    ("journal_submissions_enabled", True),
])
def test_unexpected_change_to_trading_control_is_critical(attribute, value):
    p = clean_report()
    p[attribute] = value
    a = assess_read_only_report(p, now=NOW)
    assert a.level == "critical"


@pytest.mark.parametrize("attribute,value", [
    ("can_submit_orders", True),
    ("code_submissions_enabled", True),
    ("broker_write_operations", 1),
])
def test_broker_write_attempt_cannot_be_silently_ignored(attribute, value):
    p = clean_report()
    p[attribute] = value
    assert assess_read_only_report(p, now=NOW).code == "unexpected_broker_write"


def test_unprotected_held_position_is_critical():
    p = clean_report()
    p["reconciliation"].update({
        "consistent": False, "open_broker_positions": 1,
        "uncovered_symbols": ["F"],
        "blockers": ["position_missing_live_protective_stop"]
    })
    p["safety_blockers"].append("position_missing_live_protective_stop")
    a = assess_read_only_report(p, now=NOW)
    assert a.level == "critical"
    assert a.code == "unsafe_exposure"
    assert "position_missing_live_protective_stop" in a.reasons


def test_unresolved_journal_without_open_broker_position_is_warning():
    p = clean_report()
    p["reconciliation"]["consistent"] = False
    p["reconciliation"]["blockers"] = [
        "unresolved_order_intent_requires_broker_lookup"
    ]
    a = assess_read_only_report(p, now=NOW)
    assert a.level == "warning"
    assert a.code == "degraded"


def test_outdated_broker_state_is_not_a_success():
    p = clean_report()
    p["observed_at_utc"] = (NOW - timedelta(minutes=4)).isoformat()
    assert assess_read_only_report(p, now=NOW).level == "warning"
    p["reconciliation"]["open_broker_orders"] = 1
    assert assess_read_only_report(p, now=NOW).level == "critical"


def test_future_timestamp_outside_tolerance_is_rejected():
    p = clean_report()
    p["observed_at_utc"] = (NOW + timedelta(seconds=40)).isoformat()
    assert assess_read_only_report(p, now=NOW).code == "stale_observation"


def test_bad_or_missing_report_fails_closed():
    for r in (None, {}, [], {"reconciliation": None}):
        a = assess_read_only_report(r, now=NOW)
        assert a.level == "critical"
        assert a.code == "unverifiable"
    assert unverified_incident().level == "critical"


def test_cannot_trust_unrecognized_endpoint_or_mode():
    p = clean_report()
    p["broker_paper_url"] = "https://api.alpaca.markets"
    assert assess_read_only_report(p, now=NOW).level == "critical"
    p = clean_report()
    p["mode"] = "LIVE"
    assert assess_read_only_report(p, now=NOW).level == "critical"


def test_incomplete_data_never_treated_as_idle():
    for key in ("safety_blockers", "reconciliation", "observed_at_utc"):
        p = clean_report()
        del p[key]
        assert assess_read_only_report(p, now=NOW).level == "critical"
    p = clean_report()
    p["reconciliation"]["open_broker_positions"] = False
    assert assess_read_only_report(p, now=NOW).level == "critical"


def test_unexpected_future_execution_blocker_is_not_ignored():
    p = clean_report()
    p["safety_blockers"].append("some_new_error")
    a = assess_read_only_report(p, now=NOW)
    assert a.level == "warning"
    assert a.code == "degraded"
    assert "some_new_error" in a.reasons


def test_no_secrets_in_exception_alert():
    a = unverified_incident()
    assert "password" not in a.summary.lower()
    assert "dsn" not in a.summary.lower()
    assert a.observed_at_utc is None


@pytest.mark.parametrize("change", [
    lambda p: p["reconciliation"].update({"unresolved_journal_intents": 1}),
    lambda p: p["reconciliation"].update({"uncovered_symbols": ["F"]}),
    lambda p: p["reconciliation"].update({"blockers": ["not_reconciled"]}),
])
def test_contradictory_consistent_report_is_critical(change):
    p = clean_report()
    change(p)
    report = assess_read_only_report(p, now=NOW)
    assert report.level == "critical"
    assert report.code == "unverifiable"


def test_missing_reconciliation_fields_refuse_idle():
    for missing in ("unresolved_journal_intents", "uncovered_symbols"):
        p = clean_report()
        del p["reconciliation"][missing]
        report = assess_read_only_report(p, now=NOW)
        assert report.level == "critical"


def test_unreleased_independent_evidence_is_expected_observer_only():
    # These default-False executor proof gates are not evidence that the
    # observer failed; they must NEVER authorize an executor either.
    report = clean_report()
    report["safety_blockers"].extend([
        "external_cash_flows_unverified",
        "equity_history_unverified",
        "nyse_calendar_unverified",
    ])
    incident = assess_read_only_report(report, now=NOW)
    assert incident.level == "idle"
    assert report["can_submit_orders"] is False


@pytest.mark.parametrize("bad_writes", [False, True, None, "0", 0.0, -1])
def test_noninteger_or_nonzero_broker_write_count_is_critical(bad_writes):
    report = clean_report()
    report["broker_write_operations"] = bad_writes
    incident = assess_read_only_report(report, now=NOW)
    assert incident.level == "critical"
    assert incident.code == "unexpected_broker_write"
