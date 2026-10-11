"""No-network tests for opt-in NTFY safety notifications."""
import pytest

from bot.monitor_policy import make_incident, unverified_incident
from bot.monitor_notify import NotificationError, send_critical_ntfy


class FakeResponse:
    def __init__(self, status):
        self.status = status

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False


def test_does_not_send_warning_or_idle_even_with_secret():
    def fail(*_a, **_kw):
        raise AssertionError("No network needed")
    incident = make_incident("warning", "degraded", ("missing_data",))
    assert not send_critical_ntfy(
        incident, topic="X" * 36, open_url=fail)


def test_critical_ntfy_is_sent_without_db_secrets():
    calls = []
    def capture(request, **kwargs):
        calls.append((request, kwargs))
        return FakeResponse(200)

    incident = unverified_incident()
    assert send_critical_ntfy(
        incident, topic="r" * 36, open_url=capture)
    assert len(calls) == 1
    request, kwargs = calls[0]
    assert request.full_url == "https://ntfy.sh/" + "r" * 36
    body = request.data.decode("utf-8")
    assert incident.fingerprint in body
    assert "password" not in body.lower()
    assert "postgresql://" not in body
    assert "no orders were placed" in body.lower()
    assert request.get_method() == "POST"
    assert kwargs["timeout"] == 5


@pytest.mark.parametrize("topic", [
    "", "my-public-topic", "hello/world", "one\nHeader:X",
    "postgres://owner:secret@host/db", "a" * 97, None,
])
def test_weak_or_non_topic_values_rejected(topic):
    with pytest.raises(NotificationError):
        send_critical_ntfy(
            unverified_incident(), topic=topic,
            open_url=lambda *_a, **_kw: (_ for _ in ()).throw(
                AssertionError("No invalid network calls")))


def test_ntfy_network_failure_redacts_secret_url():
    def reject(_request, **_kw):
        raise RuntimeError("https://ntfy.sh/" + "s" * 40)
    with pytest.raises(NotificationError) as exc:
        send_critical_ntfy(unverified_incident(), topic="s" * 40,
                           open_url=reject)
    assert "s" * 40 not in str(exc.value)


def test_http_failure_requires_retry():
    with pytest.raises(NotificationError):
        send_critical_ntfy(unverified_incident(), topic="s" * 40,
                           open_url=lambda *_a, **_kw: FakeResponse(503))
