"""Operator login provisioning: SCRAM verifier and privilege expectations."""
import base64
from pathlib import Path

import pytest

from bot.paper_operator import (
    EXPECTED_VERIFY, OPERATOR_LOGIN, operator_login_sql, scram_sha256_verifier,
)

ROOT = Path(__file__).resolve().parents[1]


def test_scram_verifier_matches_rfc7677_test_vector():
    # RFC 7677 section 3: user "user", password "pencil", 4096 iterations.
    salt = base64.b64decode("W22ZaJ0SNY7soEsUEjb6gQ==")
    verifier = scram_sha256_verifier("pencil", salt=salt)
    iterations, rest = verifier.split("$", 1)[1].split(":", 1)
    salt_b64, keys = rest.split("$")
    stored_key_b64, server_key_b64 = keys.split(":")
    assert iterations == "4096" and salt_b64 == "W22ZaJ0SNY7soEsUEjb6gQ=="
    # RFC 7677 ServerSignature derives from this ServerKey; check ServerKey directly.
    import hashlib, hmac
    salted = hashlib.pbkdf2_hmac("sha256", b"pencil", salt, 4096)
    assert base64.b64decode(server_key_b64) == hmac.new(salted, b"Server Key", hashlib.sha256).digest()
    auth = (b"n=user,r=rOprNGfwEbeRWgbNEkqO,r=rOprNGfwEbeRWgbNEkqO%hvYDpWUa2RaTCAfuxFIlj)hNlF$k0,"
            b"s=W22ZaJ0SNY7soEsUEjb6gQ==,i=4096,c=biws,r=rOprNGfwEbeRWgbNEkqO%hvYDpWUa2RaTCAfuxFIlj)hNlF$k0")
    signature = hmac.new(base64.b64decode(server_key_b64), auth, hashlib.sha256).digest()
    assert base64.b64encode(signature) == b"6rriTRBi23WpRR/wtup+mMhUZUn/dB5nLTJRsjl95G4="


def test_login_sql_contains_only_the_verifier_never_the_password():
    secret = "correct horse battery staple 2026"
    sql = operator_login_sql(scram_sha256_verifier(secret))
    assert secret not in sql and "SCRAM-SHA-256$4096:" in sql
    assert f"GRANT hds_paper_operator TO {OPERATOR_LOGIN};" in sql
    for forbidden in ("SUPERUSER ", "CREATEROLE ", "hds_paper_executor", "hds_paper_reconciler",
                      "neondb_owner", "BYPASSRLS "):
        assert forbidden not in sql.replace("NOSUPERUSER", "").replace("NOCREATEROLE", "") \
            .replace("NOBYPASSRLS", "")


def test_two_verifiers_for_same_password_use_fresh_salts():
    assert scram_sha256_verifier("x" * 24) != scram_sha256_verifier("x" * 24)


def test_verify_expects_arm_and_pause_only():
    assert EXPECTED_VERIFY == (OPERATOR_LOGIN, True, True, False, False, False, False, False)


def test_operator_password_is_never_persisted_by_the_script():
    text = (ROOT / "ops" / "windows" / "set-paper-trading.ps1").read_text()
    assert "Set-Content" not in text and "Out-File" not in text and "ConvertFrom-SecureString" not in text
    assert "PAPER_OPERATOR_NEW_PASSWORD" in text and "Remove-Item \"Env:$n\"" in text
