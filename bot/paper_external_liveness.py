"""Opt-in out-of-band PAPER WORKER LIVENESS heartbeat, not trade readiness.

The receiver independently detects stopped hosts while Neon is idled. It
only gets a process ID, increasing tick number and observation category.
This is never durable journal health, execution proof or broker authority.
"""
from __future__ import annotations

import json
import re
from urllib.parse import urlsplit
import requests

from .paper_safety import TradeBlocked

ID = re.compile(r"paper-[a-f0-9]{32}")
TOKEN = re.compile(r"[A-Za-z0-9_-]{32,128}")
STATES = frozenset(("read_only_poll_ok", "closed_flat_idle", "poll_failed"))


def check_liveness_settings(url, token):
    """Accept only HTTPS Cloudflare Workers-dev POST endpoint.

    Never send a bearer credential to production Neon or an arbitrary URL.
    All unexpected query/service options and URL userinfo are forbidden.
    """
    if not isinstance(url, str) or not isinstance(token, str):
        raise TradeBlocked("External liveness endpoint and token are required")
    try:
        parsed = urlsplit(url)
        host = parsed.hostname
        port = parsed.port
    except (TypeError, ValueError):
        raise TradeBlocked("Malformed external liveness endpoint") from None
    if (parsed.scheme != "https" or not host
            or not re.fullmatch(r"[a-z0-9-]+[.][a-z0-9-]+[.]workers[.]dev", host)
            or parsed.path != "/v1/heartbeat"
            or parsed.query or parsed.fragment or parsed.username
            or parsed.password or port is not None
            or TOKEN.fullmatch(token) is None):
        raise TradeBlocked("External liveness endpoint or token is invalid")
    return url, token


def send_liveness(*, url, token, worker_id, sequence, status, post=None):
    """Strict, time-bounded POST with sanitized errors and zero broker calls.

    The default requests transport reaches Cloudflare Workers when the
    urllib default client is blocked at the edge. Never follow redirects.
    """
    check_liveness_settings(url, token)
    if (not isinstance(worker_id, str) or ID.fullmatch(worker_id) is None
            or type(sequence) is not int or not 1 <= sequence <= 2147483647
            or status not in STATES):
        raise TradeBlocked("Invalid external liveness observation")
    payload = json.dumps(
        {"worker_id": worker_id, "sequence": sequence, "status": status},
        separators=(",", ":"), sort_keys=True
    ).encode("utf-8")
    try:
        response = (post or requests.post)(
            url, data=payload,
            headers={
                "Authorization": "Bearer " + token,
                "Content-Type": "application/json",
            },
            timeout=5, allow_redirects=False,
        )
        code = response.status_code
    except Exception:
        raise TradeBlocked("External liveness delivery failed") from None
    if code != 204:
        raise TradeBlocked("External liveness delivery not acknowledged")
    return True
