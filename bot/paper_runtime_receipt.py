"""Bounded local supervision receipt; never trading authorization."""
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from tempfile import NamedTemporaryFile

from .paper_external_liveness import ID, STATES
from .paper_safety import TradeBlocked


def write_runtime_receipt(path, event):
    if (set(event) != {"worker_id", "sequence", "status", "external_ack"}
            or not isinstance(event["worker_id"], str)
            or ID.fullmatch(event["worker_id"]) is None
            or type(event["sequence"]) is not int or event["sequence"] < 1
            or event["status"] not in STATES
            or type(event["external_ack"]) is not bool):
        raise TradeBlocked("Invalid local supervisor receipt")
    target = Path(path)
    record = dict(event, pid=os.getpid(), observed_at_utc=datetime.now(timezone.utc).isoformat())
    temporary = None
    try:
        with NamedTemporaryFile(mode="w", encoding="utf-8", dir=target.parent,
                                prefix=target.name + ".", delete=False) as f:
            temporary = Path(f.name)
            json.dump(record, f, sort_keys=True)
            f.flush()
            os.fsync(f.fileno())
        os.replace(temporary, target)
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()
