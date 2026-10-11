"""Fresh Alpaca IEX quote validation and protected PAPER order PREVIEWS.

Order REQUEST objects are built for inspection only. NEVER submit from
this module, and never use these requests without a separate durable
reservation, real-time execution gate and monitored broker reconciliation.
IEX quotes cover one exchange, not consolidated NBBO/SIP.
"""
from dataclasses import asdict, dataclass
from datetime import date, datetime, timezone
from decimal import Decimal, ROUND_DOWN, ROUND_UP
import re

from .paper_safety import TradeBlocked, make_client_order_id, money


CENT = Decimal("0.01")
MAX_QUOTE_AGE_SECONDS = 45
MAX_SPREAD_BPS = Decimal("75")
ENTRY_LIMIT_BUFFER = Decimal(".001")
STOP_DISTANCE = Decimal(".03")


@dataclass(frozen=True)
class LivePaperEntryPreview:
    symbol: str
    observed_at_utc: str
    quote_at_utc: str
    bid_usd: float
    ask_usd: float
    bid_size: int
    ask_size: int
    quote_age_seconds: float
    spread_bps: float
    buy_limit_usd: float
    stop_usd: float
    shares: int
    max_limit_purchase_usd: float
    risk_budget_usd: float
    stop_distance_risk_usd: float
    client_order_id: str
    quote_feed: str = "IEX_ONLY_NOT_NBBO"
    order_class: str = "oto"
    order_time_in_force: str = "day"
    order_submission_enabled: bool = False

    def to_dict(self):
        return asdict(self)


def _quote_number(value, name):
    n = money(value)
    if n <= 0:
        raise TradeBlocked(f"Invalid {name}")
    return n


def _size(value, name):
    if value is None or isinstance(value, bool):
        raise TradeBlocked(f"Missing {name}")
    try:
        n = int(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise TradeBlocked(f"Invalid {name}") from exc
    if n < 1 or Decimal(str(value)) != n:
        raise TradeBlocked(f"Invalid {name}")
    return n


def preview_live_paper_entry(
    *, symbol: str, quote, equity, available_cash,
    now: datetime, market_open: bool, risk_fraction=Decimal(".01"),
    max_quote_age_seconds=MAX_QUOTE_AGE_SECONDS,
) -> LivePaperEntryPreview:
    """Conservatively size a whole-share long with capped limit price.

    No funds are moved. Stop price is not an execution guarantee; gaps can
    exceed the expected per-share loss. All prices must be fresh IEX quotes.
    """
    if not isinstance(symbol, str) or not re.fullmatch(r"[A-Z]{1,5}", symbol):
        raise TradeBlocked("Invalid stock symbol")
    if not isinstance(now, datetime) or now.tzinfo is None:
        raise TradeBlocked("Missing timezone-aware clock")
    if market_open is not True:
        raise TradeBlocked("NYSE regular session not confirmed open")
    if not isinstance(max_quote_age_seconds, int) or not 1 <= max_quote_age_seconds <= 60:
        raise TradeBlocked("Invalid quote freshness tolerance")
    quote_ts = getattr(quote, "timestamp", None)
    if not isinstance(quote_ts, datetime) or quote_ts.tzinfo is None:
        raise TradeBlocked("Quote missing timezone-aware timestamp")
    age = (now.astimezone(timezone.utc) -
           quote_ts.astimezone(timezone.utc)).total_seconds()
    if not -3 <= age <= max_quote_age_seconds:
        raise TradeBlocked("Live market quote is stale or timestamp is in the future")

    bid = _quote_number(getattr(quote, "bid_price", None), "bid")
    ask = _quote_number(getattr(quote, "ask_price", None), "ask")
    bid_size = _size(getattr(quote, "bid_size", None), "bid_size")
    ask_size = _size(getattr(quote, "ask_size", None), "ask_size")
    if ask <= bid:
        raise TradeBlocked("Crossed or zero-spread quote cannot be trusted")
    midpoint = (bid + ask) / 2
    spread = (ask - bid) / midpoint * Decimal("10000")
    if spread > MAX_SPREAD_BPS:
        raise TradeBlocked("Spread too wide for risk-controlled entry")

    e = _quote_number(equity, "paper equity")
    cash = _quote_number(available_cash, "paper buying cash")
    if cash > e * Decimal("1.1"):
        raise TradeBlocked("Unexpected paper cash exceeds equity")
    risk = money(risk_fraction)
    # First launch may use ONLY 1%, not experimental 2% sizing.
    if risk != Decimal(".01"):
        raise TradeBlocked("Initial PAPER entry is restricted to 1% equity risk")
    buy_limit = (ask * (1 + ENTRY_LIMIT_BUFFER)).quantize(
        CENT, rounding=ROUND_UP)
    stop = (bid * (1 - STOP_DISTANCE)).quantize(CENT, rounding=ROUND_DOWN)
    if stop <= 0 or stop >= bid - CENT:
        raise TradeBlocked("Invalid attached stop trigger")
    risk_per_share = buy_limit - stop
    budget = e * risk
    shares = min(int(budget // risk_per_share), int(cash // buy_limit),
                 ask_size)
    if shares < 1:
        raise TradeBlocked("No affordable whole share at 1% risk and current spread")
    # Deterministic symbol/day ID, derived from New York clock, not server UTC.
    from zoneinfo import ZoneInfo
    session_day = now.astimezone(ZoneInfo("America/New_York")).date()
    cid = make_client_order_id(session_day, symbol)
    return LivePaperEntryPreview(
        symbol=symbol, observed_at_utc=now.astimezone(timezone.utc).isoformat(),
        quote_at_utc=quote_ts.astimezone(timezone.utc).isoformat(),
        bid_usd=float(bid), ask_usd=float(ask),
        bid_size=bid_size, ask_size=ask_size,
        quote_age_seconds=round(max(age, 0), 3),
        spread_bps=round(float(spread), 2),
        buy_limit_usd=float(buy_limit), stop_usd=float(stop),
        shares=shares,
        max_limit_purchase_usd=float(buy_limit * shares),
        risk_budget_usd=float(budget.quantize(CENT)),
        stop_distance_risk_usd=float(
            (risk_per_share * shares).quantize(CENT)),
        client_order_id=cid,
    )


def build_live_protected_oto_request(preview: LivePaperEntryPreview):
    """Return Alpaca SDK OTO LIMIT BUY request. DO NOT SEND.

    DAY limit buys cannot remain indefinitely pending overnight; the GTC
    child stop is broker-side protection once the parent is filled.
    The stop is not guaranteed to fill at its stop price.
    """
    from alpaca.trading.enums import OrderClass, OrderSide, TimeInForce
    from alpaca.trading.requests import LimitOrderRequest, StopLossRequest
    if (preview.order_submission_enabled is not False
            or preview.order_class != "oto"
            or preview.order_time_in_force != "day"
            or preview.shares < 1
            or preview.buy_limit_usd <= preview.stop_usd
            or preview.stop_distance_risk_usd > preview.risk_budget_usd + .00001):
        raise TradeBlocked("Unsafe or modified protected order preview")
    return LimitOrderRequest(
        symbol=preview.symbol, qty=preview.shares,
        side=OrderSide.BUY, time_in_force=TimeInForce.DAY,
        limit_price=preview.buy_limit_usd,
        order_class=OrderClass.OTO,
        stop_loss=StopLossRequest(stop_price=preview.stop_usd),
        client_order_id=preview.client_order_id,
        extended_hours=False,
    )
