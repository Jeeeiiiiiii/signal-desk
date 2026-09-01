"""Triage board.

Deliberately plain. The architecture is the subject of this project; the app is
here to prove the architecture carries real traffic.
"""

import logging
import os
import time

from flask import Flask, jsonify, redirect, render_template, request, session, url_for

import auth
import consumer
import store

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "dev-only-not-for-production")

store.init(os.environ.get("DB_PATH", "/tmp/signal-desk.db"))
consumer.start()


def _require_user():
    user = auth.current_user()
    if not user and auth.enabled():
        return None
    return user


@app.get("/health")
def health():
    """Liveness only — deliberately does not touch SQS or the database.

    A health check that fails when a dependency is down causes Kubernetes to
    restart a process that was working fine, turning a degradation into an
    outage.
    """
    return jsonify(ok=True)


@app.get("/login")
def login():
    return auth.login() if auth.enabled() else redirect(url_for("index"))


@app.get("/callback")
def callback():
    return auth.callback()


@app.get("/logout")
def logout():
    session.clear()
    return redirect(url_for("index"))


@app.get("/")
def index():
    user = _require_user()
    if not user:
        return redirect(url_for("login"))
    return render_template(
        "index.html", incidents=store.list_incidents(), user=user,
        auth_mode=auth.MODE,
    )


@app.get("/api/incidents")
def api_incidents():
    if not _require_user():
        return jsonify(error="unauthorized"), 401
    return jsonify(store.list_incidents())


@app.post("/api/incidents/<fingerprint>/ack")
def api_ack(fingerprint):
    user = _require_user()
    if not user:
        return jsonify(error="unauthorized"), 401
    # Signing in is not the same as being allowed to act. Reading the board is
    # open to the org; changing incident state requires the responder group.
    if not user.get("responder"):
        return jsonify(error="responder group required"), 403
    store.ack(fingerprint, user["email"], int(time.time() * 1000))
    return jsonify(ok=True)


@app.post("/api/incidents/<fingerprint>/resolve")
def api_resolve(fingerprint):
    user = _require_user()
    if not user:
        return jsonify(error="unauthorized"), 401
    if not user.get("responder"):
        return jsonify(error="responder group required"), 403
    store.resolve(fingerprint, int(time.time() * 1000))
    return jsonify(ok=True)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080)
