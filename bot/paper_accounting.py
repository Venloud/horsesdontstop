"""Authenticated PAPER history accounting. No broker writes or risk release.

Daily portfolio samples alone cannot prove an intraday peak after trading.
The cash-only lifetime proof below is deliberately narrower and exact: no
fills, no assets, no earnings/fees, and every dollar explained by funding.
"""
from datetime import datetime, timezone
from decimal import Decimal
import hashlib
import json
from dataclasses import dataclass, asdict

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
    evidence = json.dumps({"activities": activities, "history": history,
        "account_id_sha256": identity, "created_at": created.isoformat(), "currency": "USD",
        "equity": str(equity), "cash": str(cash), "observed_at": now.isoformat(),
        "activities_complete": activities_complete, "positions": positions, "orders": orders}, sort_keys=True)
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


@dataclass(frozen=True)
class LedgerOrder:
    """Previously broker-fetched and journal-linked order, NOT authentication.

    An exit links to the independently verified entry parent and its journal
    client ID. Construction alone cannot attest either identity.
    """
    order_id: str
    symbol: str
    side: str
    quantity: str
    client_order_id: str
    journal_client_order_id: str
    status: str
    limit_price: str = "0"
    parent_order_id: str | None = None
    broker_verified: bool = False
    journal_verified: bool = False


@dataclass(frozen=True)
class LedgerEvent:
    """Normalized independent execution/cash evidence; never an order ack.

    Deposits are positive, withdrawals and fees negative. Fill quantity/price
    are actual executions, not requested or acknowledged quantities. Unknown
    corporate actions, funding adjustments and provider types require a
    separately reviewed adapter and must not be coerced into these kinds.
    """
    event_id: str
    at: str
    kind: str
    broker_verified: bool = False
    amount: str = "0"
    order_id: str = ""
    quantity: str = "0"
    price: str = "0"


@dataclass(frozen=True)
class LedgerPosition:
    symbol: str
    quantity: str
    market_price: str
    at: str
    broker_verified: bool = False


def reconcile_posttrade_ledger(*, account, events, orders, positions,
                               expected_account_hash, observed_at,
                               activities_complete=False, journal_complete=False,
                               pending_client_order_ids=(), equity_observations=None,
                               historical_positions=None,
                               all_equity_samples_accounted_for=False,
                               exchange_calendar_verified=False):
    """Deterministic long-only FIFO accounting; never releases submissions.

    This is a pure evidence consumer, not a provider-authentication adapter.
    Full history must start with cash funding and no inherited inventory.
    Intraday peaks/daily loss remain UNKNOWN unless a separately verified
    complete sampled-equity history and exchange calendar are supplied.
    Every current share and dollar must conserve across executions and fees.
    The strategy requires flat-before-entry: overlapping entry parents for a
    symbol are unsupported. Fees are expensed when charged, not capitalized
    into tax lots; this is trading-risk accounting, not a tax-basis attestation.
    """
    now = timestamp(observed_at)
    created = timestamp(account.created_at)
    identity = hashlib.sha256(str(account.id).encode()).hexdigest()
    if (activities_complete is not True or journal_complete is not True or identity != expected_account_hash
            or getattr(account, "currency", None) != "USD" or created > now
            or not 0 <= (datetime.now(timezone.utc) - now).total_seconds() <= 120
            or not isinstance(events, list) or not 0 < len(events) <= 10000
            or not isinstance(orders, list) or not isinstance(positions, list)):
        raise TradeBlocked("Trading ledger identity/history/freshness unverified")
    by_id, used_clients = {}, set()
    active = {"new", "accepted", "pending_new", "pending_cancel", "partially_filled"}
    terminal = {"filled", "canceled", "expired", "rejected"}
    for order in orders:
        if (not isinstance(order, LedgerOrder) or order.broker_verified is not True
                or order.journal_verified is not True or not order.order_id
                or order.order_id in by_id or not order.symbol
                or order.side not in {"buy", "sell"} or money(order.quantity) <= 0
                or not order.client_order_id or order.client_order_id in used_clients
                or not order.journal_client_order_id or order.status not in active | terminal):
            raise TradeBlocked("Trading ledger order identity/state unverified")
        if order.side == "buy" and (order.parent_order_id is not None
                or order.client_order_id != order.journal_client_order_id
                or money(order.limit_price) <= 0):
            raise TradeBlocked("Trading ledger entry approval changed")
        by_id[order.order_id] = order
        used_clients.add(order.client_order_id)
    if (not isinstance(pending_client_order_ids, (tuple, list))
            or any(x not in {o.journal_client_order_id for o in orders}
                   for x in pending_client_order_ids)):
        raise TradeBlocked("Trading ledger unresolved journal reservation")
    for order in orders:
        if order.side == "sell":
            parent = by_id.get(order.parent_order_id)
            if (parent is None or parent.side != "buy" or parent.symbol != order.symbol
                    or parent.journal_client_order_id != order.journal_client_order_id):
                raise TradeBlocked("Trading ledger exit is not linked to entry")
    cash = funding = gross_realized = fees = Decimal("0")
    lots, fills, ids, cash_events = {}, {}, set(), []
    last = created
    for event in events:
        if (not isinstance(event, LedgerEvent) or event.broker_verified is not True
                or not event.event_id or event.event_id in ids):
            raise TradeBlocked("Trading ledger event unverified or duplicated")
        at = timestamp(event.at)
        if not last <= at <= now:
            raise TradeBlocked("Trading ledger chronology incomplete")
        last = at
        ids.add(event.event_id)
        amount, qty, price = money(event.amount), money(event.quantity), money(event.price)
        delta = Decimal("0")
        if event.kind in {"deposit", "withdrawal", "fee"}:
            if (event.order_id or qty != 0 or price != 0
                    or (amount <= 0 if event.kind == "deposit" else amount >= 0)):
                raise TradeBlocked("Trading ledger cash event unexplained")
            delta = amount
            if event.kind == "fee":
                fees -= amount
            else:
                funding += amount
        elif event.kind == "fill":
            order = by_id.get(event.order_id)
            if order is None or amount != 0 or qty <= 0 or price <= 0:
                raise TradeBlocked("Trading ledger execution missing")
            fills[order.order_id] = fills.get(order.order_id, Decimal("0")) + qty
            if fills[order.order_id] > money(order.quantity):
                raise TradeBlocked("Trading ledger fill exceeds approved quantity")
            symbol_lots = lots.setdefault(order.symbol, [])
            if order.side == "buy":
                if price > money(order.limit_price):
                    raise TradeBlocked("Trading ledger entry exceeds approved limit")
                if any(q > 0 and parent != order.order_id for q, _, parent in symbol_lots):
                    raise TradeBlocked("Trading ledger overlapping entry parents unsupported")
                symbol_lots.append([qty, price, order.order_id])
                delta = -qty * price
            else:
                left, basis = qty, Decimal("0")
                for lot in symbol_lots:
                    if lot[2] != order.parent_order_id:
                        continue
                    take = min(left, lot[0])
                    lot[0] -= take
                    basis += take * lot[1]
                    left -= take
                if left != 0:
                    raise TradeBlocked("Trading ledger competing exit or short position")
                gross_realized += qty * price - basis
                delta = qty * price
        else:
            raise TradeBlocked("Unsupported trading ledger event")
        cash += delta
        if cash < 0:
            raise TradeBlocked("Trading ledger cash funding missing")
        cash_events.append((at, delta, event))
    reserved = Decimal("0")
    for order in orders:
        actual, requested = fills.get(order.order_id, Decimal("0")), money(order.quantity)
        if (order.status == "filled" and actual != requested
                or order.status == "rejected" and actual != 0
                or order.status in {"new", "accepted", "pending_new"} and actual != 0
                or order.status in active and actual == requested
                or order.status == "partially_filled" and not 0 < actual < requested):
            raise TradeBlocked("Trading ledger order executions contradict status")
        if order.side == "buy" and order.status in active:
            reserved += (requested - actual) * money(order.limit_price)
    held = {symbol: sum((q for q, _, _ in ls), Decimal("0"))
            for symbol, ls in lots.items() if any(q for q, _, _ in ls)}
    def value_positions(records, at, *, exact=False):
        if not isinstance(records, list):
            raise TradeBlocked("Trading ledger valuation snapshot missing")
        observed, market_value = {}, Decimal("0")
        for position in records:
            if (not isinstance(position, LedgerPosition) or position.broker_verified is not True
                    or position.symbol in observed or money(position.quantity) <= 0
                    or money(position.market_price) <= 0
                    or not 0 <= (at - timestamp(position.at)).total_seconds() <= 120
                    or exact and timestamp(position.at) != at):
                raise TradeBlocked("Trading ledger position valuation unverified")
            observed[position.symbol] = money(position.quantity)
            market_value += money(position.quantity) * money(position.market_price)
        return observed, market_value
    observed, market_value = value_positions(positions, now)
    if observed != held or cash != money(account.cash) or cash + market_value != money(account.equity):
        raise TradeBlocked("Trading ledger share/cash/equity conservation failed")
    open_basis = sum((q * p for ls in lots.values() for q, p, _ in ls), Decimal("0"))
    unrealized = market_value - open_basis
    if funding + gross_realized - fees + unrealized != money(account.equity):
        raise TradeBlocked("Trading ledger profit conservation failed")
    risk = None
    historical_positions = {} if historical_positions is None else historical_positions
    if not isinstance(historical_positions, dict):
        raise TradeBlocked("Trading ledger historical valuations malformed")
    if equity_observations is not None:
        from .paper_equity_risk import AuditedExternalCashFlow, contribution_adjusted_risk
        if not equity_observations:
            raise TradeBlocked("Trading ledger equity sampling history missing")
        first = timestamp(equity_observations[0].observed_at)
        if any(e.kind in {"fill", "fee"} and t <= first for t, _, e in cash_events):
            raise TradeBlocked("Trading ledger prior trading highwater missing")
        for sample in equity_observations:
            sample_at = timestamp(sample.observed_at)
            expected_cash = sum((d for t, d, _ in cash_events if t <= sample_at), Decimal("0"))
            if money(sample.cash_usd) != expected_cash:
                raise TradeBlocked("Trading ledger historical cash does not reconcile")
            sample_held = {}
            for t, _, e in cash_events:
                if t <= sample_at and e.kind == "fill":
                    order = by_id[e.order_id]
                    sample_held[order.symbol] = sample_held.get(order.symbol, Decimal("0")) + money(e.quantity) * (1 if order.side == "buy" else -1)
            sample_held = {k: v for k, v in sample_held.items() if v}
            marks = positions if sample_at == now else historical_positions.get(sample_at.isoformat(), [])
            marked_quantities, sample_value = value_positions(marks, sample_at, exact=sample_at != now)
            if marked_quantities != sample_held or money(sample.equity_usd) != expected_cash + sample_value:
                raise TradeBlocked("Trading ledger historical equity/positions unexplained")
        latest = equity_observations[-1]
        if money(latest.equity_usd) != money(account.equity) or money(latest.cash_usd) != cash:
            raise TradeBlocked("Trading ledger latest equity sample contradicts broker")
        flows = [AuditedExternalCashFlow(e.event_id, t, money(e.amount), True)
                 for t, _, e in cash_events if e.kind in {"deposit", "withdrawal"} and t > first]
        risk = contribution_adjusted_risk(equity_observations, flows, as_of_utc=now,
            all_equity_samples_accounted_for=all_equity_samples_accounted_for,
            all_external_flows_reconciled=True, exchange_calendar_verified=exchange_calendar_verified)
    digest = json.dumps({'method': 'long-only FIFO; no overlapping entry parents; fees expensed',
        'account': {'id_sha256': identity, 'created_at': created.isoformat(), 'currency': 'USD',
                    'cash': str(money(account.cash)), 'equity': str(money(account.equity))},
        'observed_at': now.isoformat(), 'events': [asdict(e) for e in events],
        'orders': [asdict(o) for o in orders], 'positions': [asdict(p) for p in positions],
        'pending_client_order_ids': list(pending_client_order_ids),
        'equity_observations': [asdict(x) for x in equity_observations] if equity_observations is not None else None,
        'historical_positions': {k: [asdict(p) for p in v] for k, v in historical_positions.items()},
        'activities_complete': activities_complete, 'journal_complete': journal_complete,
        'all_equity_samples_accounted_for': all_equity_samples_accounted_for,
        'exchange_calendar_verified': exchange_calendar_verified}, sort_keys=True, default=str)
    return dict(account_id_sha256=identity, observed_at_utc=now.isoformat(),
        cash=str(cash), equity=str(money(account.equity)), net_external_funding=str(funding),
        realized_gross_pnl=str(gross_realized), fees=str(fees), realized_net_pnl=str(gross_realized-fees),
        unrealized_pnl=str(unrealized), open_cost_basis=str(open_basis), market_exposure=str(market_value),
        open_entry_reserved_notional=str(reserved), positions={k: str(v) for k, v in held.items()},
        daily_loss_pct=str(risk.loss_from_open_pct) if risk else None,
        drawdown_pct=str(risk.drawdown_from_peak_pct) if risk else None,
        verified_sampled_peak=str(risk.verified_peak_performance_equity_usd) if risk else None,
        daily_loss_limit_breached=risk.daily_loss_limit_breached if risk else None,
        drawdown_limit_breached=risk.peak_drawdown_limit_breached if risk else None,
        risk_history_complete=risk is not None, can_submit_orders=False,
        risk_history_scope="verified sampled valuations; no inferred intratick peaks" if risk else "UNKNOWN",
        evidence_sha256=hashlib.sha256(digest.encode()).hexdigest())
