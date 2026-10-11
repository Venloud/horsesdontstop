"""Offline source-level tripwires: staging must never silently enable orders.

These tests are not a replacement for live role/endpoint/owner approval
checks. They prevent accidental release-switch toggles and workflow
order entry during ordinary GitHub development. No API calls.
"""
import ast
from pathlib import Path

from bot.paper_safety import PAPER_ENDPOINT
from bot.paper_supervised_execution import PAPER_ORDER_SUBMISSIONS_RELEASED


ROOT = Path(__file__).resolve().parents[1]


def test_alpaca_order_release_constant_is_off_in_source_and_runtime():
    source = (ROOT / "bot" / "paper_supervised_execution.py").read_text()
    module = ast.parse(source)
    assignments = [
        node for node in module.body
        if isinstance(node, ast.Assign)
        and any(isinstance(target, ast.Name)
                and target.id == "PAPER_ORDER_SUBMISSIONS_RELEASED"
                for target in node.targets)
    ]
    assert len(assignments) == 1
    assert isinstance(assignments[0].value, ast.Constant)
    assert assignments[0].value.value is False
    assert PAPER_ORDER_SUBMISSIONS_RELEASED is False


def test_paper_endpoint_cannot_silently_become_real_money():
    assert PAPER_ENDPOINT == "https://paper-api.alpaca.markets"
    source = (ROOT / "bot" / "paper_supervised_execution.py").read_text()
    assert "trading, \"_base_url\"" in source
    assert "Actual broker client endpoint is not Alpaca PAPER" in source


def test_no_scheduled_or_manual_workflow_invokes_unreleased_submit_gateway():
    for workflow in (ROOT / ".github" / "workflows").glob("*.yml"):
        contents = workflow.read_text()
        assert "python -m bot.paper_supervised_execution" not in contents
        assert "bot.paper_supervised_execution:submit_paper_once_supervised" not in contents
        assert "PAPER_ORDER_SUBMISSIONS_RELEASED=true" not in contents
        assert "PAPER_ORDER_SUBMISSIONS_RELEASED=True" not in contents
        # No existing shell workflow should run a general buy command.
        assert "alpaca submit_order" not in contents.lower()


def test_default_neon_schema_still_blocks_paper_submission():
    from bot.paper_store import SCHEMA
    assert "kill_switch_active BOOLEAN NOT NULL DEFAULT TRUE" in SCHEMA
    assert "submissions_enabled BOOLEAN NOT NULL DEFAULT FALSE" in SCHEMA


def test_read_only_watchdog_cannot_place_or_cancel_orders():
    source = (ROOT / "bot" / "paper_watchdog.py").read_text()
    forbidden = (
        ".submit_order(", ".cancel_order(", ".cancel_orders(",
        ".replace_order(", ".close_position(", ".close_all_positions(",
    )
    assert all(term not in source for term in forbidden)
