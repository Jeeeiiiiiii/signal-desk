"""SQS consumer.

Runs as a thread inside the web process. That is a deliberate simplification for
a lab: in production this is a separate deployment so the queue keeps draining
during a web rollout.
"""

import json
import logging
import os
import threading
import time

import boto3

import store

log = logging.getLogger("consumer")

ENDPOINT = os.environ.get("AWS_ENDPOINT_URL") or None
QUEUE_URL = os.environ.get("QUEUE_URL", "")


def _loop(stop):
    sqs = boto3.client("sqs", endpoint_url=ENDPOINT)
    log.info("consuming %s", QUEUE_URL)

    while not stop.is_set():
        try:
            # Long poll: one call parks for 20s rather than 20 calls returning
            # empty. Cheaper and lower latency at the same time.
            resp = sqs.receive_message(
                QueueUrl=QUEUE_URL,
                MaxNumberOfMessages=10,
                WaitTimeSeconds=20,
            )
        except Exception:
            # The queue being briefly unreachable is not fatal — that is the
            # whole reason the buffer exists. Back off and try again.
            log.exception("receive failed; backing off")
            stop.wait(5)
            continue

        for msg in resp.get("Messages", []):
            try:
                event = json.loads(msg["Body"])
                applied = store.apply_event(event)
                log.info(
                    "%s %s (%s)",
                    "applied" if applied else "duplicate ignored",
                    event.get("title"),
                    event.get("fingerprint"),
                )
            except Exception:
                # Leave the message on the queue: it becomes visible again
                # after the visibility timeout, and after 5 attempts the
                # redrive policy moves it to the DLQ rather than looping here
                # forever.
                log.exception("failed to apply message; leaving for redelivery")
                continue

            sqs.delete_message(
                QueueUrl=QUEUE_URL, ReceiptHandle=msg["ReceiptHandle"]
            )


def start():
    if not QUEUE_URL:
        log.warning("QUEUE_URL unset; consumer disabled")
        return None
    stop = threading.Event()
    threading.Thread(target=_loop, args=(stop,), daemon=True).start()
    return stop
