"""Tests for the parts that are easy to get wrong and expensive to get wrong:
signature checking, fingerprint stability, and dedup.
"""
import hashlib
import hmac
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "lambda"))
os.environ.setdefault("RAW_BUCKET", "x")
os.environ.setdefault("QUEUE_URL", "x")
os.environ.setdefault("WEBHOOK_SECRET", "s3cret")

import handler  # noqa: E402


def sign(body, secret=b"s3cret"):
    return hmac.new(secret, body.encode(), hashlib.sha256).hexdigest()


def test_valid_signature_accepted():
    body = '{"alertname":"X"}'
    assert handler._verify(body, sign(body))
    assert handler._verify(body, "sha256=" + sign(body))


def test_wrong_signature_rejected():
    assert not handler._verify('{"a":1}', sign('{"a":2}'))
    assert not handler._verify('{"a":1}', "")


def test_fingerprint_ignores_time_and_delivery_id():
    """Two deliveries about one condition must collapse into one incident."""
    a = {"alertname": "HighCPU", "instance": "web-01", "timestamp": 1}
    b = {"alertname": "HighCPU", "instance": "web-01", "timestamp": 9999, "id": "abc"}
    assert handler._fingerprint("prom", a) == handler._fingerprint("prom", b)


def test_fingerprint_separates_hosts():
    a = {"alertname": "HighCPU", "instance": "web-01"}
    b = {"alertname": "HighCPU", "instance": "web-02"}
    assert handler._fingerprint("prom", a) != handler._fingerprint("prom", b)


def test_severity_vendor_names_normalized():
    for raw, expected in [("P1", "critical"), ("crit", "critical"),
                          ("warn", "warning"), ("nonsense", "warning")]:
        ev = handler.normalize("prom", {"alertname": "X", "severity": raw})
        assert ev["severity"] == expected


def test_resolve_gets_a_different_idempotency_key():
    """Fire and resolve share a fingerprint but must both be applied."""
    fire = handler.normalize("prom", {"alertname": "X", "instance": "a", "status": "firing"})
    done = handler.normalize("prom", {"alertname": "X", "instance": "a", "status": "resolved"})
    assert fire["fingerprint"] == done["fingerprint"]
    assert fire["idempotency_key"] != done["idempotency_key"]


def test_retry_and_refire_are_distinguishable():
    """A sender's retry must collapse; the same condition re-firing later must not."""
    alert = {"alertname": "X", "instance": "a", "status": "firing"}

    a = handler.normalize("prom", alert)
    b = handler.normalize("prom", alert)          # immediate retry
    assert a["idempotency_key"] == b["idempotency_key"]

    later = dict(alert)
    later["id"] = "delivery-2"                     # sender-supplied delivery id
    assert handler.normalize("prom", later)["idempotency_key"] != a["idempotency_key"]
