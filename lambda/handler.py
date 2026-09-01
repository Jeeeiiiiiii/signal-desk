"""Webhook ingest.

Runs outside the cluster on purpose. The alert reporting that the cluster is
down cannot be received by the cluster, so this path shares no infrastructure
with it: it validates, archives, and enqueues, then exits.

Contract with the consumer: every message carries a stable `idempotency_key`.
Delivery is at-least-once, so the consumer must tolerate seeing one twice.
"""

import hashlib
import hmac
import json
import os
import time
import uuid

import boto3

ENDPOINT = os.environ.get("SIGNAL_DESK_ENDPOINT") or None
RAW_BUCKET = os.environ["RAW_BUCKET"]
QUEUE_URL = os.environ["QUEUE_URL"]
SECRET = os.environ["WEBHOOK_SECRET"].encode()

# Built lazily rather than at import. A module that opens clients on import
# cannot be imported by a test without AWS configuration present, and the cold
# start cost is the same either way.
_clients = {}


def _client(name):
    if name not in _clients:
        _clients[name] = boto3.client(name, endpoint_url=ENDPOINT)
    return _clients[name]

# Vendors disagree about severity names; the triage board should not have to.
# Two deliveries of one condition inside this window are treated as the same
# notification. Wide enough to absorb a sender's retry burst, narrow enough that
# a real re-fire still registers.
DEDUP_WINDOW_MS = 60_000

SEVERITY_MAP = {
    "critical": "critical", "crit": "critical", "p1": "critical", "fatal": "critical",
    "error": "error", "high": "error", "p2": "error",
    "warning": "warning", "warn": "warning", "medium": "warning", "p3": "warning",
    "info": "info", "low": "info", "p4": "info",
}


def _verify(body: str, signature: str) -> bool:
    """Machines cannot do interactive SSO, so senders sign instead.

    Compared in constant time: a naive `==` leaks the correct prefix through
    timing, which is enough to forge a signature given patience.
    """
    if not signature:
        return False
    expected = hmac.new(SECRET, body.encode(), hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature.strip().removeprefix("sha256="))


def _fingerprint(source: str, payload: dict) -> str:
    """Identity of the *condition*, not of the delivery.

    Two notifications about one failing host must collapse to a single incident,
    so the fingerprint deliberately excludes timestamps and delivery ids.
    """
    parts = [
        source,
        str(payload.get("alertname") or payload.get("title") or payload.get("check") or "unknown"),
        str(payload.get("instance") or payload.get("host") or payload.get("target") or ""),
    ]
    return hashlib.sha256("|".join(parts).encode()).hexdigest()[:16]


def normalize(source: str, payload: dict) -> dict:
    """Vendor payload -> the one shape the triage board understands."""
    raw_sev = str(payload.get("severity") or payload.get("priority") or "warning").lower()
    status = str(payload.get("status") or payload.get("state") or "firing").lower()
    fingerprint = _fingerprint(source, payload)
    received_at = int(time.time() * 1000)

    # Distinguishing a retry from a genuine re-fire is the hard part: both carry
    # the same condition and the same status. Prefer the sender's own delivery
    # id when there is one. Otherwise fall back to a time bucket — retries land
    # within seconds of each other and collapse, while a condition re-firing
    # minutes later counts as a new occurrence.
    delivery = payload.get("delivery_id") or payload.get("id") or ""
    window = delivery or str(received_at // DEDUP_WINDOW_MS)

    return {
        "event_id": str(uuid.uuid4()),
        "source": source,
        "fingerprint": fingerprint,
        # The consumer dedupes on this key and ignores repeats outright.
        "idempotency_key": f"{fingerprint}:{status}:{window}",
        "title": payload.get("alertname") or payload.get("title") or "Untitled alert",
        "severity": SEVERITY_MAP.get(raw_sev, "warning"),
        "status": "resolved" if status in ("resolved", "ok", "recovered") else "firing",
        "target": payload.get("instance") or payload.get("host") or payload.get("target") or "",
        "description": payload.get("description") or payload.get("message") or "",
        "received_at": received_at,
    }


def handler(event, context):
    body = event.get("body") or "{}"
    headers = {k.lower(): v for k, v in (event.get("headers") or {}).items()}

    if not _verify(body, headers.get("x-signal-signature", "")):
        # Deliberately terse. A detailed rejection tells a forger which half
        # of the request was wrong.
        return {"statusCode": 401, "body": json.dumps({"error": "bad signature"})}

    try:
        payload = json.loads(body)
    except json.JSONDecodeError:
        return {"statusCode": 400, "body": json.dumps({"error": "invalid json"})}

    source = headers.get("x-signal-source", "unknown")
    normalized = normalize(source, payload)

    # Archive BEFORE enqueueing. If normalization is wrong we can replay from
    # the archive; if the archive write fails there is nothing to replay from,
    # so it is the operation allowed to fail loudly.
    _client("s3").put_object(
        Bucket=RAW_BUCKET,
        Key=f"raw/{time.strftime('%Y/%m/%d')}/{normalized['event_id']}.json",
        Body=body.encode(),
        ContentType="application/json",
        Metadata={"source": source, "fingerprint": normalized["fingerprint"]},
    )

    _client("sqs").send_message(QueueUrl=QUEUE_URL, MessageBody=json.dumps(normalized))

    return {
        "statusCode": 202,
        "body": json.dumps({"accepted": True, "event_id": normalized["event_id"]}),
    }
