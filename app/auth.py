"""OIDC for the human path.

Three modes, one switch:
  off    - no auth. Local development only.
  mock   - a local OIDC issuer. Lets a stranger clone the repo and run it.
  entra  - a real Microsoft Entra ID tenant.

The mock exists so the project is runnable without a tenant. It proves nothing
on its own — it will assert whatever claims you ask it to — so the screenshots
in docs/ are taken against a real tenant.
"""

import os
import secrets
import urllib.parse
import urllib.request
import json

from flask import redirect, request, session, url_for

MODE = os.environ.get("AUTH_MODE", "off").lower()
ISSUER = os.environ.get("OIDC_ISSUER", "")
CLIENT_ID = os.environ.get("OIDC_CLIENT_ID", "")
CLIENT_SECRET = os.environ.get("OIDC_CLIENT_SECRET", "")

# Entra puts group membership in a claim only after you configure the app
# registration to emit it. Authorization lives in the directory, not in a table
# this app maintains.
RESPONDER_GROUP = os.environ.get("RESPONDER_GROUP", "oncall-responders")

_meta_cache = {}


def _metadata():
    """OIDC discovery. Same document shape from Entra and from the mock, which
    is what lets one code path serve both."""
    if ISSUER not in _meta_cache:
        url = ISSUER.rstrip("/") + "/.well-known/openid-configuration"
        with urllib.request.urlopen(url, timeout=10) as r:
            _meta_cache[ISSUER] = json.loads(r.read())
    return _meta_cache[ISSUER]


def enabled():
    return MODE in ("mock", "entra")


def current_user():
    if not enabled():
        return {"name": "local", "email": "local@example.com", "responder": True}
    return session.get("user")


def login():
    state = secrets.token_urlsafe(16)
    session["oidc_state"] = state
    params = {
        "client_id": CLIENT_ID,
        "response_type": "code",
        "redirect_uri": url_for("callback", _external=True),
        "scope": "openid profile email",
        "state": state,
    }
    return redirect(_metadata()["authorization_endpoint"] + "?" + urllib.parse.urlencode(params))


def callback():
    # Without this check an attacker can hand the victim a prepared code and
    # log them into an account the attacker controls.
    if request.args.get("state") != session.pop("oidc_state", None):
        return {"error": "state mismatch"}, 400

    data = urllib.parse.urlencode({
        "grant_type": "authorization_code",
        "code": request.args["code"],
        "redirect_uri": url_for("callback", _external=True),
        "client_id": CLIENT_ID,
        "client_secret": CLIENT_SECRET,
    }).encode()

    with urllib.request.urlopen(
        urllib.request.Request(_metadata()["token_endpoint"], data=data), timeout=10
    ) as r:
        tokens = json.loads(r.read())

    claims = _decode_id_token(tokens["id_token"])
    groups = claims.get("groups", []) or []

    session["user"] = {
        "name": claims.get("name") or claims.get("preferred_username", "unknown"),
        "email": claims.get("email") or claims.get("preferred_username", ""),
        # Everyone who can sign in can read the board. Only responders can
        # change incident state.
        "responder": RESPONDER_GROUP in groups,
    }
    return redirect(url_for("index"))


def _decode_id_token(token):
    """Read the claims out of the ID token payload.

    NOTE: this does not verify the signature. The token arrives over TLS
    directly from the token endpoint (the confidential-client code flow), so it
    is not attacker-supplied here. A public client, or accepting tokens from the
    front channel, would make verification mandatory.
    """
    import base64
    payload = token.split(".")[1]
    payload += "=" * (-len(payload) % 4)
    return json.loads(base64.urlsafe_b64decode(payload))
