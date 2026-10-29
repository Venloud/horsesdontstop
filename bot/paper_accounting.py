"""Authenticated PAPER history accounting. No broker writes or risk release.

Daily portfolio samples alone cannot prove an intraday peak after trading.
The cash-only lifetime proof below is deliberately narrower and exact: no
fills, no assets, no earnings/fees, and every dollar explained by funding.
"""
from datetime import datetime, timezone
from decimal import Decimal
import hashlib
import json

from .paper_safety import PAPER_ENDPOINT, TradeBlocked, money


def timestamp(value):
    try:
        result = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if result.tzinfo is None:
            raise ValueError()
        return result.astimezone(timezone.utc)
    except (ValueError, TypeError):
        raise TradeBlocked("Accounting timestamp unverified") from None


def fetch_account_history(trading):
    if str(getattr(trading, "_base_url", "")).rstrip("/") != PAPER_ENDPOINT:
        raise TradeBlocked("Accounting requires PAPER broker")
    account = trading.get_account()
    rows, seen, token = [], set(), None
    for _ in range(100):
        args = {"page_size": 100, "direction": "asc"}
        if token:
            args["page_token"] = token
        page = trading.get("/account/activities", args)
        if not isinstance(page, list) or len(page) > 100:
            raise TradeBlocked("Accounting pagination unverified")
        for item in page:
            if not isinstance(item, dict) or not item.get("id") or item["id"] in seen:
                raise TradeBlocked("Accounting duplicate or malformed activity")
            seen.add(item["id"])
            rows.append(item)
        if len(page) < 100:
            break
        token = page[-1]["id"]
    else:
        raise TradeBlocked("Accounting activity history incomplete")
    history = trading.get("/account/portfolio/history", {
        "start": account.created_at.isoformat(), "timeframe": "1D", "cashflow_types": "ALL"})
    return account, rows, history, True


def reconcile_cash_lifetime(*, account, activities, history, positions, orders,
                            expected_account_hash, observed_at, activities_complete=False):
    """Prove the actual pre-trade cash account without inventing past P&L.

    JNLC is accepted only as the single initial funding journal, independently
    corroborated by broker portfolio cashflow. Later journal adjustments,
    fills, transfers of securities and other activity require full accounting.
    """
    now = timestamp(observed_at)
    if (activities_complete is not True
            or not 0 <= (datetime.now(timezone.utc) - now).total_seconds() <= 120):
        raise TradeBlocked("Accounting completeness or freshness unverified")
    created = timestamp(account.created_at)
    identity = hashlib.sha256(str(account.id).encode()).hexdigest()
    if identity != expected_account_hash or created > now or getattr(account, "currency", None) != "USD":
        raise TradeBlocked("Accounting account identity changed")
    if (not isinstance(positions, (list, tuple)) or positions
            or not isinstance(orders, (list, tuple)) or orders):
        raise TradeBlocked("Cash-only accounting cannot explain exposure")
    if not isinstance(activities, list) or not activities or not isinstance(history, dict):
        raise TradeBlocked("Accounting historical evidence missing")
    ids, flows, last_time = set(), [], created
    journal_count = 0
    for item in activities:
        if (not isinstance(item, dict) or not isinstance(item.get("id"), str)
                or not item["id"] or item["id"] in ids
                or item.get("status") != "executed" or item.get("currency") != "USD"):
            raise TradeBlocked("Accounting activity unverified")
        ids.add(item["id"])
        at = timestamp(item.get("created_at"))
        if not created <= at <= now or at < last_time:
            raise TradeBlocked("Accounting activity chronology unverified")
        last_time = at
        kind, amount = item.get("activity_type"), money(item.get("net_amount"))
        if kind == "JNLC":
            journal_count += 1
            if (len(flows) or journal_count != 1 or amount <= 0
                    or (at - created).total_seconds() > 15 * 60):
                raise TradeBlocked("Unexplained broker journal adjustment")
        elif kind == "CSD":
            if amount <= 0:
                raise TradeBlocked("Invalid broker cash deposit evidence")
        elif kind == "CSW":
            if amount >= 0:
                raise TradeBlocked("Invalid broker cash withdrawal evidence")
        else:
            raise TradeBlocked("Non-cash activity requires full trading ledger")
        flows.append((at, kind, amount))
    times, equities = history.get("timestamp"), history.get("equity")
    pnl, cashflow = history.get("profit_loss"), history.get("cashflow")
    if (not isinstance(times, list) or not times or times != sorted(set(times))
            or not isinstance(equities, list) or len(equities) != len(times)
            or not isinstance(pnl, list) or len(pnl) != len(times)
            or not isinstance(cashflow, dict)):
        raise TradeBlocked("Broker portfolio history incomplete")
    totals = {}
    for kind, values in cashflow.items():
        if not isinstance(values, list) or len(values) != len(times):
            raise TradeBlocked("Broker cashflow history incomplete")
        total = sum((money(v) for v in values), Decimal("0"))
        if total:
            totals[kind] = total
    activity_totals = {}
    for _, kind, amount in flows:
        activity_totals[kind] = activity_totals.get(kind, Decimal("0")) + amount
    if totals != {k: v for k, v in activity_totals.items() if v}:
        raise TradeBlocked("Activity and portfolio cashflow disagree")
    for raw_time, raw_equity, raw_pnl in zip(times, equities, pnl):
        if type(raw_time) not in (int, float):
            raise TradeBlocked("Portfolio observation timestamp unverified")
        at = datetime.fromtimestamp(raw_time, timezone.utc)
        expected = sum((amount for t, _, amount in flows if t <= at), Decimal("0"))
        if at > now or at < created or money(raw_equity) != expected or money(raw_pnl) != 0:
            raise TradeBlocked("Broker historical equity is unexplained")
    total = sum((amount for _, _, amount in flows), Decimal("0"))
    equity, cash = money(account.equity), money(account.cash)
    if equity != cash or cash != total or equity <= 0:
        raise TradeBlocked("Current cash equity does not reconcile")
    evidence = json.dumps({"activities": activities, "history": history}, sort_keys=True)
    running, peak = Decimal("0"), Decimal("0")
    for _, _, amount in flows:
        running += amount
        if running < 0:
            raise TradeBlocked("Historical cash balance unexplained")
        peak = max(peak, running)
    return dict(account_id_sha256=identity, observed_at_utc=now.isoformat(),
                history_scope="authenticated cash-only account lifetime",
                equity=str(equity), cash=str(cash), realized_pnl="0", unrealized_pnl="0",
                net_external_funding=str(total), historical_trading_drawdown_pct="0",
                nominal_peak_equity=str(peak), daily_trading_loss_pct="0",
                daily_loss_limit_breached=False, trading_drawdown_limit_breached=False,
                equity_history_explained=True, cashflows_reconciled=True,
                evidence_sha256=hashlib.sha256(evidence.encode()).hexdigest(),
                can_submit_orders=False)


def verify_fresh_baseline(witnesses, *, authorization_reference, prior_history_digest):
    """Prepare an explicit new scope, never erase or approve unknown prehistory.

    Activation needs separately preserved operator/independent verification.
    This receipt alone cannot set the execution history-completeness flags.
    """
    if (not isinstance(authorization_reference, str) or not authorization_reference
            or not isinstance(prior_history_digest, str) or len(prior_history_digest) != 64
            or any(c not in "0123456789abcdef" for c in prior_history_digest)
            or not isinstance(witnesses, list) or len(witnesses) != 2):
        raise TradeBlocked("Fresh baseline authorization/evidence missing")
    a, b = witnesses
    if (any(not isinstance(w.get("principal"), str) or not w["principal"].strip()
            or len(w["principal"]) > 128 for w in witnesses)
            or a.get("principal") == b.get("principal")
            or any(w.get("authenticated") is not True
                   or any(type(w.get(k)) is not int or w[k] != 0
                          for k in ("positions", "open_orders", "unresolved_intents")) for w in witnesses)
            or not a.get("account_id_sha256") or a["account_id_sha256"] != b.get("account_id_sha256")
            or money(a.get("equity")) != money(b.get("equity"))
            or money(a.get("cash")) != money(b.get("cash"))
            or money(a.get("equity")) != money(a.get("cash"))
            or money(a.get("equity")) <= 0
            or any(not 0 <= (datetime.now(timezone.utc) - timestamp(w.get("at"))).total_seconds() <= 120
                   for w in witnesses)
            or abs((timestamp(a.get("at")) - timestamp(b.get("at"))).total_seconds()) > 5):
        raise TradeBlocked("Independent flat baseline witnesses disagree")
    return dict(scope_start=max(timestamp(a["at"]), timestamp(b["at"])).isoformat(), equity=str(money(a["equity"])),
                account_id_sha256=a["account_id_sha256"], prior_history_digest=prior_history_digest,
                authorization_reference=authorization_reference,
                pre_scope_pnl="UNKNOWN", pre_scope_peak="UNKNOWN", activated=False,
                can_submit_orders=False)
