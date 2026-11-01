from hashlib import sha256
from types import SimpleNamespace

import pytest

from bot.paper_executor_service import (
    build_safety_state, dispatch_approved_artifact, main,
)
from bot.paper_safety import TradeBlocked, execution_blockers


ACCOUNT_ID = "00000000-0000-0000-0000-000000000001"
ACCOUNT_HASH = sha256(ACCOUNT_ID.encode()).hexdigest()


def inputs(**overrides):
    values = dict(
        account=SimpleNamespace(id=ACCOUNT_ID, equity="500", cash="500",
                                 trading_blocked=False),
        clock=SimpleNamespace(is_open=True),
        positions=[], orders=[],
        control=SimpleNamespace(kill_switch_active=True,
                                submissions_enabled=False),
        unresolved=[], day_open_equity="500", recorded_peak_equity="500",
        lifetime_evidence={
            "history_scope": "authenticated cash-only account lifetime",
            "equity_history_explained": True, "cashflows_reconciled": True,
            "can_submit_orders": False, "account_id_sha256": ACCOUNT_HASH,
            "equity": "500", "cash": "500",
        },
        lease=SimpleNamespace(healthy=True, age_seconds=5),
        calendar_verified=True, quote_age_seconds=1,
        journal_healthy=True, broker_reconciled=True,
    )
    values.update(overrides)
    return values


def test_live_evidence_builder_accepts_only_complete_cash_lifetime_for_proposal():
    state = build_safety_state(**inputs())
    blockers = execution_blockers(state)
    assert "kill_switch_active" in blockers
    assert "paper_order_submission_disabled" in blockers
    assert not set(blockers) - {"kill_switch_active", "paper_order_submission_disabled"}


@pytest.mark.parametrize("field,value", [
    ("day_open_equity", None),
    ("recorded_peak_equity", None),
    ("calendar_verified", False),
    ("journal_healthy", False),
    ("broker_reconciled", False),
    ("unresolved", [("hds-20261030-ABC-0123456789ab", "unknown", "ABC", 1)]),
    ("positions", [object()]),
    ("orders", [object()]),
])
def test_missing_or_conflicting_evidence_remains_blocked(field, value):
    state = build_safety_state(**inputs(**{field: value}))
    assert execution_blockers(state)


def test_lifetime_evidence_from_another_account_is_rejected_as_incomplete():
    evidence = dict(inputs()["lifetime_evidence"], account_id_sha256="f" * 64)
    state = build_safety_state(**inputs(lifetime_evidence=evidence))
    assert "external_cash_flows_unverified" in execution_blockers(state)
    assert "equity_history_unverified" in execution_blockers(state)


def test_untrusted_boolean_values_are_not_coerced_to_safe_states():
    with pytest.raises(TradeBlocked):
        build_safety_state(**inputs(control=SimpleNamespace(
            kill_switch_active="true", submissions_enabled="false")))


def test_missing_watchdog_lease_does_not_become_healthy():
    state = build_safety_state(**inputs(lease=SimpleNamespace(
        healthy=False, age_seconds=None)))
    assert "no_healthy_persistent_watchdog" in execution_blockers(state)


def test_builder_rejects_malformed_lifetime_values():
    evidence = dict(inputs()["lifetime_evidence"], equity="NaN")
    with pytest.raises(ValueError):
        build_safety_state(**inputs(lifetime_evidence=evidence))


def test_dispatch_cli_is_hard_disabled_before_reading_or_connecting(capsys):
    assert main(["--dispatch-file", "does-not-exist.json"]) == 2
    output = capsys.readouterr()
    assert "PAPER broker submissions NOT RELEASED" in output.err
    assert "no broker order sent" in output.err


def test_dispatch_helper_is_hard_disabled_before_using_components(monkeypatch):
    monkeypatch.setattr(
        "bot.paper_supervised_execution.PAPER_ORDER_SUBMISSIONS_RELEASED", False)
    with pytest.raises(TradeBlocked, match="NOT RELEASED"):
        dispatch_approved_artifact(components=None, artifact=None)
