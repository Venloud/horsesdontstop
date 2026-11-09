"""Operator controls through the restricted hds_paper_operator login.

The login can only PAUSE (paper_engage_kill_switch) or ARM
(paper_arm_autonomous_trading). Both are audited SECURITY DEFINER functions.
"""
import os
import sys


def main():
    import psycopg
    action = os.environ.get("PAPER_OPERATOR_ACTION")
    reason = "operator: " + (os.environ.get("PAPER_OPERATOR_REASON") or "unspecified")
    with psycopg.connect(os.environ["PAPER_OPERATOR_DSN"], sslmode="require",
                         connect_timeout=10) as conn:
        if action == "stop":
            conn.execute("SELECT public.paper_engage_kill_switch(%s)", (reason,))
        elif action == "arm":
            conn.execute("SELECT public.paper_arm_autonomous_trading(%s)", (reason,))
        elif action != "status":
            print("BLOCKED: unknown operator action")
            return 2
        conn.commit()
        kill, enabled = conn.execute(
            "SELECT kill_switch_active, submissions_enabled FROM public.paper_control").fetchone()
        policy = conn.execute(
            "SELECT autonomous_entries_enabled, max_trade_risk_fraction, "
            "max_position_fraction, max_entries_per_day, entry_window_start, "
            "entry_window_end FROM public.paper_trading_policy").fetchone()
    state = ("PAUSED" if (kill, enabled) == (True, False) else
             "ARMED" if (kill, enabled) == (False, True) else "INCOHERENT")
    print(f"PAPER controls: {state} (kill={kill}, submissions={enabled})")
    print("Policy: autonomous_entries=%s risk<=%s of opening equity, position<=%s, "
          "entries/day<=%s, window %s-%s NY" % policy)
    return 0


if __name__ == "__main__":
    sys.exit(main())
