"""Offline semantic dataset checks: no broker/Neon, no scored research."""
import hashlib
from pathlib import Path

import pytest

from bot.phase2_dataset_gate import validate_manifest
from test_phase2_dataset_gate import synthetic_manifest


def replace_csv(tmp_path, manifest, key, body):
    asset = manifest["artifacts"][key]
    path = tmp_path / asset["path"]
    path.write_text(body, encoding="utf-8")
    asset["sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.mark.parametrize("body", [
    "symbol,day,open,high,low,close,volume\nSPY,2026-10-13,100,105,98,101,10000\nSPY,2026-10-13,100,105,98,101,10000\n",
    "symbol,day,open,high,low,close,volume\nSPY,2026-10-14,100,105,98,101,10000\nSPY,2026-10-13,100,105,98,101,10000\n",
    "symbol,day,open,high,low,close,volume\nSPY,2026-10-13,100,100,98,101,10000\n",
    "symbol,day,open,high,low,close,volume\nSPY,2026-10-13,100,105,102,101,10000\n",
    "symbol,day,open,high,low,close,volume\nSPY,2026-10-13,100,105,98,NaN,10000\n",
    "symbol,day,open,high,low,close,volume\nSPY,2026-10-13,100,105,98,101,-1\n",
    "symbol,day,open,high,low,close,volume\nSPY,2026-10-13,100,105,98,101,1.5\n",
    "symbol,day,open,high,low,close,volume\nspy,2026-10-13,100,105,98,101,10000\n",
    "symbol,day,open,high,low,close,volume\nAAPL,2026-10-13,100,105,98,101,10000\n",
    "symbol,day,open,high,low,close,volume\nSPY,2026-10-13,100,105,98,101,10000,EXTRA\n",
])
def test_hash_matched_but_malformed_market_data_still_blocks(tmp_path, body):
    m = synthetic_manifest(tmp_path)
    replace_csv(tmp_path, m, "bars", body)
    assert "asset_semantic_invalid:bars" in validate_manifest(m, tmp_path)


@pytest.mark.parametrize("body", [
    "day,total_return_index\n2026-10-13,0\n",
    "day,total_return_index\n2026-10-13,NaN\n",
    "day,total_return_index\n2026-10-13,100\n2026-10-13,101\n",
    "day,total_return_index\n2026-10-14,100\n2026-10-13,101\n",
])
def test_hash_matched_invalid_benchmark_blocks(tmp_path, body):
    m = synthetic_manifest(tmp_path)
    replace_csv(tmp_path, m, "benchmark_total_return", body)
    assert "asset_semantic_invalid:benchmark_total_return" in (
        validate_manifest(m, tmp_path))


@pytest.mark.parametrize("line", [
    "2026-10-12,2026-10-12T20:10:00Z,s1,QC:SPY,SPY,yes,fixture,true",
    "2026-10-12,2026-10-12T20:10:00Z,s1,QC:SPY,SPY,true,fixture,false",
    "2026-10-12,2026-10-12T20:10:00,s1,QC:SPY,SPY,true,fixture,true",
    "2026-10-12,2026-10-11T20:10:00Z,s1,QC:SPY,SPY,true,fixture,true",
    "2026-10-12,2026-10-12T20:10:00Z,,QC:SPY,SPY,true,fixture,true",
])
def test_bogus_point_in_time_records_block(tmp_path, line):
    m = synthetic_manifest(tmp_path)
    replace_csv(tmp_path, m, "universe_membership",
                "snapshot_day,known_at_utc,snapshot_id,security_id,"
                "symbol,eligible,source_id,complete\n" + line + "\n")
    assert "asset_semantic_invalid:universe_membership" in (
        validate_manifest(m, tmp_path))


def test_duplicate_security_in_same_snapshot_is_blocked(tmp_path):
    m = synthetic_manifest(tmp_path)
    path = tmp_path / m["artifacts"]["universe_membership"]["path"]
    source = path.read_text()
    replace_csv(tmp_path, m, "universe_membership",
                source + source.splitlines()[-1] + "\n")
    assert "asset_semantic_invalid:universe_membership" in (
        validate_manifest(m, tmp_path))


@pytest.mark.parametrize("body", [
    "day,amount_usd\n2026-10-13,Infinity\n",
    "day,amount_usd\n2026-10-13,1.001\n",
    "day,amount_usd\n2026-10-13,5\n2026-10-13,10\n",
    "day,amount_usd\n2026-10-13,0\n2026-10-12,10\n",
])
def test_cash_flow_ledger_rejects_invalid_amounts_and_duplicates(tmp_path, body):
    m = synthetic_manifest(tmp_path)
    replace_csv(tmp_path, m, "contributions", body)
    assert "asset_semantic_invalid:contributions" in (
        validate_manifest(m, tmp_path))


def test_zero_contributions_can_be_a_header_only_csv(tmp_path):
    m = synthetic_manifest(tmp_path)
    replace_csv(tmp_path, m, "contributions", "day,amount_usd\n")
    assert validate_manifest(m, tmp_path) == []


def test_signed_actual_cash_transfers_allowed_without_auto_deposit(tmp_path):
    m = synthetic_manifest(tmp_path)
    replace_csv(tmp_path, m, "contributions",
                "day,amount_usd\n2026-10-12,10.50\n2026-10-13,-2.00\n")
    assert validate_manifest(m, tmp_path) == []


def test_non_overlapping_spy_bars_and_total_return_days_block(tmp_path):
    m = synthetic_manifest(tmp_path)
    replace_csv(tmp_path, m, "benchmark_total_return",
                "day,total_return_index\n2026-10-14,100.001\n")
    assert "spy_benchmark_dates_do_not_overlap" in (
        validate_manifest(m, tmp_path))


def test_semantics_code_never_calls_broker_or_writes_results():
    source = (Path(__file__).resolve().parents[1]
              / "bot" / "phase2_semantic_validation.py").read_text()
    assert ".submit_order(" not in source
    assert "TradingClient" not in source
    assert "psycopg" not in source
    assert "alpaca." not in source
    assert "run_portfolio(" not in source
