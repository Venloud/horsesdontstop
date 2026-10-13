"""Read-only watchdog incident classifier. Pure logic; no broker writes.

Classifies safe-idle, degraded observations, and urgent conditions.
Never interprets an unsuccessful or stale preflight as trading approval.
Incident keys are deterministic so a durable outbox can deduplicate
repeat notifications across process restarts.
"""
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from hashlib import sha256
import json
from typing import Mapping


EXPECTED_READ_ONLY_BLOCKERS = frozenset({
    "kill_switch_active",
    "paper_order_submission_disabled",
    "market_closed",
    "no_fresh_realtime_quote",
    "durable_journal_not_ready",
    "broker_orders_not_reconciled",
    "no_healthy_persistent_watchdog",
    "missing_daily_open_equity",
    "missing_durable_equity_peak",
    # These independent attestations deliberately remain unavailable
    # while submissions are hard-disabled. They are expected diagnostic
    # blockers in observation-only mode, never execution approvals.
    "external_cash_flows_unverified",
    "equity_history_unverified",
    "nyse_calendar_unverified",
    "preflight_has_no_broker_write_capability",
})
EXPOSURE_DANGER = frozenset({
    "position_missing_live_protective_stop",
    "broker_order_history_incomplete",
    "malformed_broker_order_tree",
    "open_order_not_in_durable_journal",
    "unresolved_order_intent_requires_broker_lookup",
    "invalid_broker_position",
    "invalid_or_duplicate_journal_id",
    "unknown_journal_status",
    "existing_position_or_open_order",
    "daily_three_percent_loss_cutoff",
    "fifteen_percent_drawdown_cutoff",
})
NO_TRADE_MODE = "ALPACA_PAPER_READ_ONLY_PREFLIGHT"


@dataclass(frozen=True)
class MonitorIncident:
    level: str  # idle, warning, critical
    code: str
    fingerprint: str
    summary: str
    observed_at_utc: str | None
    positions: int | None
    orders: int | None
    reasons: tuple[str, ...]

    def as_dict(self):
        data = asdict(self)
        data["reasons"] = list(self.reasons)
        return data


def make_incident(level: str, code: str, reasons, *, observation=None,
                  positions=None, orders=None):
    if level not in ("idle", "warning", "critical"):
        raise ValueError("Invalid alert classification")
    # Do not include credentials, exception bodies or untrusted text in
    # persistent fingerprints, notifications or public workflow logs.
    reasons = tuple(sorted(set(str(s) for s in reasons
                               if isinstance(s, str) and len(s) <= 100)))
    key = json.dumps([level, code, reasons, bool((positions or 0) > 0),
                      bool((orders or 0) > 0)], separators=(",", ":"))
    fingerprint = sha256(key.encode("utf-8")).hexdigest()[:32]
    friendly = {
        "idle": "Read-only paper monitor: no exposure detected.",
        "degraded": "Read-only paper monitor is degraded; manual review needed.",
        "unverifiable": "Paper broker or Neon state cannot be verified; investigate immediately.",
        "unsafe_exposure": "Paper holdings or orders may lack adequate protection; investigate immediately.",
        "security_switch": "Unexpected paper trading control state; investigate immediately.",
        "unexpected_broker_write": "Paper monitor reported a broker-write capability; investigate immediately.",
        "stale_observation": "Paper broker observation is stale; verify positions and stops.",
    }
    return MonitorIncident(
        level=level, code=code, fingerprint=fingerprint,
        summary=friendly.get(code, "Paper monitor needs attention."),
        observed_at_utc=observation,
        positions=positions, orders=orders, reasons=reasons,
    )


def unverified_incident() -> MonitorIncident:
    """Use on every exception. Never include exception strings (secrets)."""
    return make_incident("critical", "unverifiable",
                         ("broker_or_database_read_failed",))


def assess_read_only_report(report: Mapping, *, now=None,
                            max_age_seconds: int = 180) -> MonitorIncident:
    """Interpret ONLY a complete, recent, PAPER, explicitly read-only report.

    The return value is a status/alert classification, NOT authorization
    to trade. A missing report is critical even without known exposure
    because the account could have open trades that could not be checked.
    """
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None or max_age_seconds <= 0:
        raise ValueError("Expected aware timestamp and positive age")
    if not isinstance(report, Mapping):
        return unverified_incident()
    rec = report.get("reconciliation")
    if not isinstance(rec, Mapping):
        return unverified_incident()
    positions, orders = rec.get("open_broker_positions"), rec.get("open_broker_orders")
    if (type(positions) is not int or type(orders) is not int
            or positions < 0 or orders < 0):
        return unverified_incident()
    blockers = report.get("safety_blockers")
    if not isinstance(blockers, list) or not all(isinstance(x, str)
                                                 for x in blockers):
        return unverified_incident()
    blockers = frozenset(blockers)
    rec_blockers = rec.get("blockers")
    if not isinstance(rec_blockers, list) or not all(
            isinstance(x, str) for x in rec_blockers):
        return unverified_incident()
    # A self-contradictory report must not be called healthy. In
    # particular, a zero-position summary with unreported outstanding
    # intents or stop gaps cannot be trusted as a safe idle account.
    unresolved = rec.get("unresolved_journal_intents")
    uncovered = rec.get("uncovered_symbols")
    if (type(unresolved) is not int or unresolved < 0
            or not isinstance(uncovered, list)
            or not all(isinstance(x, str) for x in uncovered)
            or (rec.get("consistent") is True
                and (unresolved or uncovered or rec_blockers))):
        return unverified_incident()
    if report.get("mode") != NO_TRADE_MODE:
        return unverified_incident()
    if report.get("broker_paper_url") != "https://paper-api.alpaca.markets":
        return unverified_incident()

    dtstr = report.get("observed_at_utc")
    try:
        observed = datetime.fromisoformat(dtstr.replace("Z", "+00:00"))
        if observed.tzinfo is None:
            raise ValueError
        age = (now - observed.astimezone(timezone.utc)).total_seconds()
    except (ValueError, AttributeError, TypeError):
        return unverified_incident()
    if age < -30 or age > max_age_seconds:
        level = "critical" if positions or orders else "warning"
        return make_incident(
            level, "stale_observation", ("observation_outside_freshness_window",),
            observation=dtstr, positions=positions, orders=orders)

    if (report.get("can_submit_orders") is not False
            or report.get("code_submissions_enabled") is not False
            or type(report.get("broker_write_operations")) is not int
            or report["broker_write_operations"] != 0):
        return make_incident(
            "critical", "unexpected_broker_write",
            ("read_only_contract_broken",), observation=dtstr,
            positions=positions, orders=orders)

    if (report.get("journal_kill_switch_active") is not True
            or report.get("journal_submissions_enabled") is not False):
        return make_incident(
            "critical", "security_switch", ("paper_controls_not_locked",),
            observation=dtstr, positions=positions, orders=orders)

    if rec.get("consistent") is not True:
        if positions or orders or rec.get("uncovered_symbols"):
            return make_incident(
                "critical", "unsafe_exposure",
                blockers | frozenset(rec_blockers),
                observation=dtstr, positions=positions, orders=orders)
        return make_incident(
            "warning", "degraded", blockers | frozenset(rec_blockers),
            observation=dtstr, positions=positions, orders=orders)

    if positions or orders or blockers.intersection(EXPOSURE_DANGER):
        return make_incident(
            "critical", "unsafe_exposure", blockers,
            observation=dtstr, positions=positions, orders=orders)

    # No exposure and all failures are expected limitations of deliberately
    # DISABLED paper execution. This is healthy as an observer, not a bot.
    unexpected = blockers - EXPECTED_READ_ONLY_BLOCKERS
    if unexpected:
        return make_incident(
            "warning", "degraded", unexpected,
            observation=dtstr, positions=positions, orders=orders)
    return make_incident(
        "idle", "idle", (),
        observation=dtstr, positions=positions, orders=orders)
