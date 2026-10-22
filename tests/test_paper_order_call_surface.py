"""Offline fail-closed scan of production Python's broker write call surface.

Tripwire, NOT a sandbox or proof that dynamic code cannot place orders.
No broker imports, calls, secrets, or GitHub admin permissions required.
"""
import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PRODUCTION = ("bot", "ops", "third_party")
WRITE_METHODS = frozenset({
    "submit_order", "submit_orders",
    "cancel_order", "cancel_orders", "cancel_all_orders",
    "cancel_order_by_id", "cancel_order_by_client_id",
    "replace_order", "replace_order_by_id", "replace_order_by_client_id",
    "close_position", "close_all_positions",
})
# The single existing future PAPER gateway is not a release authorization.
ALLOWED = {("bot/paper_supervised_execution.py", "submit_order")}
HTTP_WRITE_METHODS = frozenset({"post", "put", "patch", "delete", "request", "urlopen"})
BROKER_WRITE_PATHS = ("/v2/orders", "/v2/positions", "/v1/orders")


def find_order_write_calls(path, source):
    """Return (file, operation) for visible broker-write call sites."""
    tree = ast.parse(source, filename=path)
    found = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        fn = node.func
        method = (fn.attr if isinstance(fn, ast.Attribute)
                  else fn.id if isinstance(fn, ast.Name) else None)
        if method in WRITE_METHODS:
            # This one pure local research simulator has a nested
            # close_position(day, price, reason) function, not a broker call.
            if (path == "bot/portfolio_research.py"
                    and isinstance(fn, ast.Name)
                    and method == "close_position"):
                continue
            found.append((path, method))
        # Treat direct raw broker POST/PUT/PATCH/DELETE calls as unsafe.
        # This cannot detect dynamically constructed URLs: code review and
        # branch protection remain mandatory.
        if method in HTTP_WRITE_METHODS:
            literal_parts = [
                v.value for v in ast.walk(node)
                if isinstance(v, ast.Constant) and isinstance(v.value, str)
            ]
            if any(route in part.lower()
                   for part in literal_parts for route in BROKER_WRITE_PATHS):
                found.append((path, "raw_broker_write_http"))
    return found


def test_only_existing_disabled_gateway_can_invoke_broker_write():
    calls = []
    for subdir in PRODUCTION:
        for filename in (ROOT / subdir).rglob("*.py"):
            rel = filename.relative_to(ROOT).as_posix()
            calls.extend(find_order_write_calls(rel, filename.read_text(encoding="utf-8")))
    assert calls == [("bot/paper_supervised_execution.py", "submit_order")], (
        "Production broker write call surface changed: " + repr(calls)
    )


def test_scan_detects_new_sdk_methods_and_raw_http_order_writes():
    for code in (
        "client.submit_order(order_data=x)",
        "trading.cancel_order_by_id('abc')",
        "trading.replace_order_by_id('abc', data=x)",
        "trading.close_all_positions(cancel_orders=True)",
        "requests.post('https://paper-api.alpaca.markets/v2/orders', json=x)",
        "urllib.request.urlopen('https://paper-api.alpaca.markets/v2/orders')",
    ):
        assert find_order_write_calls("bot/new_module.py", code)


def test_scan_does_not_confuse_pure_research_simulator_with_broker():
    code = "def close_position(day, price, reason): pass\nclose_position(1, 2, 3)"
    assert find_order_write_calls("bot/portfolio_research.py", code) == []
    assert find_order_write_calls("bot/new_module.py", code) == [
        ("bot/new_module.py", "close_position")
    ]
