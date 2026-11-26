"""Early-close time exit and Neon-outage fallback (2026-11-23)."""
from datetime import datetime, time
from types import SimpleNamespace as NS
from zoneinfo import ZoneInfo

from bot.paper_exit_manager import TIME_EXIT_AT, time_exit_cutoff
from bot.paper_exit_service import _broker_rows, _read_approved

NY = ZoneInfo("America/New_York")


def test_cutoff_normal_and_early_close():
    assert time_exit_cutoff(None) == TIME_EXIT_AT
    assert time_exit_cutoff(time(16, 0)) == time(15, 45)
    assert time_exit_cutoff(time(13, 0)) == time(12, 45)
    assert time_exit_cutoff(datetime(2026, 11, 27, 13, 0)) == time(12, 45)
    assert time_exit_cutoff(datetime(2026, 11, 27, 18, 0, tzinfo=ZoneInfo("UTC"))) == time(12, 45)


class Boom:
    def read_approved_stop_intents(self, limit):
        raise OSError("quota")


class Ok:
    def read_approved_stop_intents(self, limit):
        return [("hds-1", "filled", "DIS", 1, "117.14", "p1", "gtc")]


def test_good_read_is_cached_then_used_when_neon_fails(tmp_path):
    cache = tmp_path / "c.json"
    rows, src = _read_approved(Ok(), cache)
    assert src == "journal" and cache.exists()
    rows, src = _read_approved(Boom(), cache)
    assert src == "cache" and rows[0][:3] == ("hds-1", "filled", "DIS")
    assert _read_approved(None, tmp_path / "missing.json") == ([], "none")


def _parent(cid="hds-20261120-DIS-x", side="buy", status="filled", legs=1):
    leg = NS(side="sell", type="stop", stop_price="117.14", status="new")
    return NS(client_order_id=cid, side=side, status=status, symbol="DIS",
              filled_qty="1", id="p1", legs=[leg] * legs)


def test_broker_rows_only_for_filled_bot_entries_with_one_stop():
    assert _broker_rows([_parent()], [])[0][:5] == ("hds-20261120-DIS-x", "filled", "DIS", "1", "117.14")
    assert _broker_rows([_parent(cid="manual-1")], []) == []
    assert _broker_rows([_parent(status="new")], []) == []
    assert _broker_rows([_parent(legs=2)], []) == []
    assert _broker_rows([_parent()], [("hds-20261120-DIS-x",)]) == []
