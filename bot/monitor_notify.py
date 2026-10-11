"""Optional NTFY alert transport for critical PAPER preflight incidents.

No messages are sent unless the owner configures an independent, high-
entropy PAPER_MONITOR_NTFY_TOPIC secret. No broker/database credentials are
ever included in messages. NTFY topics are bearer-like shared secrets.
"""
import re
from urllib.request import Request, urlopen

from .monitor_policy import MonitorIncident


class NotificationError(RuntimeError):
    pass


def validate_topic(topic: str):
    if not isinstance(topic, str) or not re.fullmatch(r"[A-Za-z0-9_-]{24,96}", topic):
        raise NotificationError(
            "PAPER_MONITOR_NTFY_TOPIC requires a private random 24-96 character topic"
        )
    return topic


def send_critical_ntfy(incident: MonitorIncident, *, topic: str,
                       open_url=urlopen) -> bool:
    """Only send critical incidents to the official HTTPS NTFY endpoint.

    This transport performs no persistence or deduplication across process
    restarts. A durable incident outbox will be needed before frequent
    scheduled runs can use notifications without excessive repetitions.
    """
    if incident.level != "critical":
        return False
    key = validate_topic(topic)
    # Sanitized wording from a fixed string table; fingerprint only.
    body = (
        f"Project 10K PAPER WATCH: {incident.summary}\n"
        f"Incident {incident.code}, ID {incident.fingerprint}.\n"
        "Open Alpaca paper dashboard and review exposure/stops. "
        "No orders were placed by this monitor."
    )
    request = Request(
        f"https://ntfy.sh/{key}", data=body.encode("utf-8"), method="POST",
        headers={
            "Title": "horsesdontstop PAPER safety alert",
            "Priority": "urgent",
            "Tags": "warning",
            "Content-Type": "text/plain; charset=utf-8",
        },
    )
    try:
        with open_url(request, timeout=5) as response:
            status = response.status
    except Exception as exc:
        # The underlying HTTP exception may embed a topic or other secret.
        raise NotificationError("NTFY delivery failed") from None
    if not 200 <= status < 300:
        raise NotificationError("NTFY declined the notification")
    return True
