import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))
import store  # noqa: E402


def setup_function():
    store._local.__dict__.clear()
    store.init(os.path.join(tempfile.mkdtemp(), "t.db"))


def ev(**kw):
    base = dict(event_id="e", source="prom", fingerprint="fp1",
                idempotency_key="fp1:firing", title="High CPU", severity="critical",
                status="firing", target="web-01", description="", received_at=1000)
    base.update(kw)
    return base


def test_duplicate_delivery_is_ignored():
    assert store.apply_event(ev()) is True
    assert store.apply_event(ev()) is False        # same key, redelivered
    assert len(store.list_incidents()) == 1


def test_refire_bumps_occurrences_not_row_count():
    store.apply_event(ev())
    store.apply_event(ev(idempotency_key="fp1:firing:2"))
    rows = store.list_incidents()
    assert len(rows) == 1
    assert rows[0]["occurrences"] == 2


def test_firing_sorts_above_resolved():
    store.apply_event(ev(fingerprint="a", idempotency_key="a:resolved", status="resolved"))
    store.apply_event(ev(fingerprint="b", idempotency_key="b:firing", status="firing"))
    assert store.list_incidents()[0]["fingerprint"] == "b"
