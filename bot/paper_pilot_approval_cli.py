"""Print a Neon owner-approval call for a prepared bounded PAPER proposal.

Usage: python -m bot.paper_pilot_approval_cli proposal.json

The command performs no network access and no database/broker writes. The
printed statement is only an approval artifact for owner review in Neon.
"""
import argparse
import json
from pathlib import Path

from .live_quotes import LivePaperEntryPreview
from .paper_pilot_approval import render_owner_approval_sql
from .paper_supervised_execution import (
    PreparedPaperOrder, _plan_from_quoted_preview,
)


def approval_sql_from_document(document):
    if not isinstance(document, dict):
        raise ValueError("proposal must be a JSON object")
    preview_data = document.get("quote_preview")
    if not isinstance(preview_data, dict):
        raise ValueError("proposal lacks full quote preview")
    preview = LivePaperEntryPreview(**preview_data)
    plan = _plan_from_quoted_preview(preview)
    if (document.get("client_order_id") != plan.client_order_id
            or document.get("symbol") != plan.symbol
            or document.get("side") != "buy"
            or document.get("quantity") != plan.whole_shares
            or document.get("time_in_force") != "gtc"
            or document.get("order_class") != "oto"
            or document.get("limit_price") != preview.buy_limit_usd
            or document.get("stop_price") != preview.stop_usd
            or document.get("purchase_cost_cap") != preview.max_limit_purchase_usd
            or document.get("fee_reserve") != 0.50
            or document.get("submitted") is not False):
        raise ValueError("proposal summary does not match its quote evidence")
    prepared = PreparedPaperOrder(
        plan=plan, preview=preview,
        request_sha256=document.get("request_sha256"),
        account_id_sha256=document.get("account_id_sha256"),
        fee_reserve_usd=document.get("fee_reserve"),
    )
    return render_owner_approval_sql(prepared)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("proposal", type=Path)
    args = parser.parse_args(argv)
    try:
        document = json.loads(args.proposal.read_text(encoding="utf-8"))
        print(approval_sql_from_document(document))
    except Exception:
        parser.error("invalid or inconsistent prepared PAPER proposal; no writes performed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
