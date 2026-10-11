"""No-network tests for opt-in NTFY safety notifications."""
import pytest

from bot.monitor_policy import make_incident, unverified_incident
from bot.monitor_notify import NotificationError, NotificationThrottle, send_critical_ntfy


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


def test_duplicate_critical_alert_suppressed_in_one_process():
    delivery = []
    def deliver(incident, *, topic):
        delivery.append(incident.fingerprint)
        return True
    throttle = NotificationThrottle(cooldown_seconds=3600)
    incident = unverified_incident()
    assert throttle.maybe_send(incident, topic="z" * 32,
                               now_seconds=100, transport=deliver)
    assert not throttle.maybe_send(incident, topic="z" * 32,
                                  now_seconds=120, transport=deliver)
    assert throttle.maybe_send(incident, topic="z" * 32,
                               now_seconds=3701, transport=deliver)
    assert len(delivery) == 2


def test_transport_failure_does_not_block_future_retry():
    throttle = NotificationThrottle()
    incident = unverified_incident()
    calls = []
    def broken(_incident, *, topic):
        calls.append(topic)
        raise NotificationError("NTFY delivery failed")
    with pytest.raises(NotificationError):
        throttle.maybe_send(incident, topic="z" * 32,
                            now_seconds=100, transport=broken)
    assert throttle.maybe_send(incident, topic="z" * 32,
                               now_seconds=105,
                               transport=lambda *_a, **_kw: True)
    assert len(calls) == 1


def test_missing_topic_and_noncritical_never_call_transport():
    throttle = NotificationThrottle()
    warning = make_incident("warning", "degraded", ("alert",))
    explode = lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("sent"))
    assert not throttle.maybe_send(unverified_incident(), topic=None,
                                   now_seconds=0, transport=explode)
    assert not throttle.maybe_send(warning, topic="z" * 32,
                                   now_seconds=0, transport=explode)
