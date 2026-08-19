#!/usr/bin/env python3
"""
iCallAgent MCP server for Claude Code.

Wraps iCallAgent's public API (https://docs.icallagent.com/api-reference/) as
three MCP tools so Claude can place an outbound call and report back what
happened on it, from a plain-English request in the chat.

Auth is OAuth2 (authorization code + PKCE, no client secret needed — this is
a "public" OAuth2 client), the same mechanism the Make.com integration uses.
See README.md for the one-time setup (registering the OAuth2 Application and
exporting ICALLAGENT_CLIENT_ID).

Why polling instead of the call.completed webhook: this process is a CLI
tool with nothing listening on a public URL, so it can't receive a webhook
delivery. GET /scheduled-calls/{id}/ exists on the backend specifically for
callers in that position — see icallagent/integrations/public_views.py::
ScheduledCallStatusView.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import secrets
import threading
import time
import webbrowser
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import urlencode

import httpx
from mcp.server.fastmcp import FastMCP

DEFAULT_API_BASE = "https://api.icallagent.com"
API_BASE = os.environ.get("ICALLAGENT_API_BASE", DEFAULT_API_BASE).rstrip("/")

# Public OAuth2 client id for the "Claude Code Plugin" Application registered in
# iCallAgent's Django admin. Not a secret — this is a PKCE public client (no
# client_secret), so shipping the id is the intended distribution model: every
# install of this plugin uses the same client_id and each user still gets their
# own login/consent/token via icallagent_login. Override only for testing
# against a non-production backend that has its own separately-registered app.
DEFAULT_CLIENT_ID = "KR9GnLbS2uBfyjhkjY60peNqGWUeDGXvCeoC87dx"
CLIENT_ID = os.environ.get("ICALLAGENT_CLIENT_ID", DEFAULT_CLIENT_ID).strip()

REDIRECT_PORT = int(os.environ.get("ICALLAGENT_REDIRECT_PORT", "8934"))
REDIRECT_URI = f"http://127.0.0.1:{REDIRECT_PORT}/callback"
SCOPES = "campaigns:read campaigns:write contacts:write calls:read"

CREDS_PATH = Path.home() / ".icallagent" / "credentials.json"

# How the auto-provisioned campaign is named, per agent. Mirrors
# integrations/slack_call_agent.py::_get_or_create_slack_campaign, except
# there's no public endpoint to flip a drifted (paused/completed) campaign
# back to active, so this only ever searches for an ACTIVE one by that name
# and creates a fresh active one if none is found.
CAMPAIGN_NAME_TEMPLATE = "Claude Code requests — {agent}"

POLL_INTERVAL_SECONDS = 5
DEFAULT_WAIT_SECONDS = 180

mcp = FastMCP("icallagent")


# ---------------------------------------------------------------------------
# Credential storage
# ---------------------------------------------------------------------------

def _load_creds() -> dict | None:
    if not CREDS_PATH.exists():
        return None
    try:
        return json.loads(CREDS_PATH.read_text())
    except (ValueError, OSError):
        return None


def _save_creds(creds: dict) -> None:
    CREDS_PATH.parent.mkdir(parents=True, exist_ok=True)
    CREDS_PATH.write_text(json.dumps(creds))
    CREDS_PATH.chmod(0o600)


# ---------------------------------------------------------------------------
# OAuth2 (authorization code + PKCE), loopback redirect
# ---------------------------------------------------------------------------

def _pkce_pair() -> tuple[str, str]:
    verifier = base64.urlsafe_b64encode(secrets.token_bytes(40)).rstrip(b"=").decode()
    challenge = base64.urlsafe_b64encode(
        hashlib.sha256(verifier.encode()).digest()
    ).rstrip(b"=").decode()
    return verifier, challenge


class _CallbackResult:
    def __init__(self):
        self.event = threading.Event()
        self.code: str | None = None
        self.state: str | None = None
        self.error: str | None = None


def _make_callback_handler(result: _CallbackResult):
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802 — http.server's naming convention
            from urllib.parse import parse_qs, urlparse

            query = parse_qs(urlparse(self.path).query)
            result.code = (query.get("code") or [None])[0]
            result.state = (query.get("state") or [None])[0]
            result.error = (query.get("error") or [None])[0]

            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            if result.error:
                body = f"<html><body><p>iCallAgent login failed: {result.error}. You can close this tab.</p></body></html>"
            else:
                body = "<html><body><p>iCallAgent connected. You can close this tab and go back to Claude.</p></body></html>"
            self.wfile.write(body.encode())
            result.event.set()

        def log_message(self, format, *args):  # noqa: A002 — silence default stderr logging
            pass

    return Handler


def _run_login_flow() -> dict:
    if not CLIENT_ID:
        raise RuntimeError(
            "ICALLAGENT_CLIENT_ID is not set. Register an OAuth2 Application in the iCallAgent "
            "Django admin (client type: Public, grant: Authorization code, redirect URI: "
            f"{REDIRECT_URI}), then export ICALLAGENT_CLIENT_ID=<its client_id> and restart. "
            "See README.md."
        )

    verifier, challenge = _pkce_pair()
    state = secrets.token_urlsafe(16)

    result = _CallbackResult()
    httpd = HTTPServer(("127.0.0.1", REDIRECT_PORT), _make_callback_handler(result))
    server_thread = threading.Thread(target=httpd.handle_request, daemon=True)
    server_thread.start()

    params = {
        "client_id": CLIENT_ID,
        "response_type": "code",
        "redirect_uri": REDIRECT_URI,
        "scope": SCOPES,
        "state": state,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
    }
    authorize_url = f"{API_BASE}/oauth/authorize/?{urlencode(params)}"
    webbrowser.open(authorize_url)

    if not result.event.wait(timeout=300):
        httpd.server_close()
        raise RuntimeError(
            "Timed out waiting for the iCallAgent login to complete in your browser. "
            f"If nothing opened, visit this URL manually: {authorize_url}"
        )
    httpd.server_close()

    if result.error:
        raise RuntimeError(f"iCallAgent login was denied or failed: {result.error}")
    if not result.code or result.state != state:
        raise RuntimeError("iCallAgent login callback was missing or didn't match the request. Try again.")

    resp = httpx.post(
        f"{API_BASE}/oauth/token/",
        data={
            "grant_type": "authorization_code",
            "code": result.code,
            "redirect_uri": REDIRECT_URI,
            "client_id": CLIENT_ID,
            "code_verifier": verifier,
        },
        timeout=15.0,
    )
    if resp.status_code != 200:
        raise RuntimeError(f"iCallAgent token exchange failed: {resp.text}")

    tokens = resp.json()
    creds = {
        "access_token": tokens["access_token"],
        "refresh_token": tokens.get("refresh_token", ""),
        "expires_at": (datetime.now(timezone.utc) + timedelta(seconds=int(tokens.get("expires_in", 3600)))).isoformat(),
    }
    _save_creds(creds)
    return creds


def _refresh(creds: dict) -> dict:
    resp = httpx.post(
        f"{API_BASE}/oauth/token/",
        data={
            "grant_type": "refresh_token",
            "refresh_token": creds["refresh_token"],
            "client_id": CLIENT_ID,
        },
        timeout=15.0,
    )
    if resp.status_code != 200:
        raise RuntimeError(
            "iCallAgent's saved login has expired and refreshing it failed. "
            "Run the icallagent_login tool again."
        )
    tokens = resp.json()
    new_creds = {
        "access_token": tokens["access_token"],
        "refresh_token": tokens.get("refresh_token") or creds["refresh_token"],
        "expires_at": (datetime.now(timezone.utc) + timedelta(seconds=int(tokens.get("expires_in", 3600)))).isoformat(),
    }
    _save_creds(new_creds)
    return new_creds


def _access_token() -> str:
    creds = _load_creds()
    if creds is None or not creds.get("refresh_token"):
        raise RuntimeError("Not connected to iCallAgent yet. Call the icallagent_login tool first.")

    expires_at = datetime.fromisoformat(creds["expires_at"])
    if datetime.now(timezone.utc) >= expires_at - timedelta(seconds=60):
        creds = _refresh(creds)
    return creds["access_token"]


# ---------------------------------------------------------------------------
# Thin API client
# ---------------------------------------------------------------------------

def _request(method: str, path: str, *, params: dict | None = None, json_body: dict | None = None) -> dict:
    token = _access_token()
    resp = httpx.request(
        method, f"{API_BASE}/api/public/v1{path}",
        headers={"Authorization": f"Bearer {token}"},
        params=params, json=json_body, timeout=20.0,
    )
    if resp.status_code == 401:
        # Token might have been revoked/expired between our check and the call — refresh once and retry.
        creds = _load_creds()
        if creds and creds.get("refresh_token"):
            creds = _refresh(creds)
            resp = httpx.request(
                method, f"{API_BASE}/api/public/v1{path}",
                headers={"Authorization": f"Bearer {creds['access_token']}"},
                params=params, json=json_body, timeout=20.0,
            )

    if resp.status_code >= 400:
        try:
            detail = resp.json().get("detail", resp.text)
        except ValueError:
            detail = resp.text
        raise RuntimeError(f"iCallAgent API error ({resp.status_code}): {detail}")

    return resp.json() if resp.content else {}


# ---------------------------------------------------------------------------
# Resolution helpers (agent / contact / campaign)
# ---------------------------------------------------------------------------

def _looks_like_phone(target: str) -> bool:
    digits = sum(c.isdigit() for c in target)
    return digits >= 7


def _resolve_agent(agent_name: str | None) -> dict:
    agents = _request("GET", "/agents/").get("results", [])
    if not agents:
        raise RuntimeError("This iCallAgent workspace has no Live agent configured to place calls with.")

    if agent_name:
        matches = [a for a in agents if agent_name.strip().lower() in a["name"].lower()]
        if len(matches) == 1:
            return matches[0]
        names = ", ".join(a["name"] for a in agents)
        raise RuntimeError(
            f"Couldn't find exactly one agent matching '{agent_name}'. Available agents: {names}."
        )

    if len(agents) == 1:
        return agents[0]

    names = ", ".join(a["name"] for a in agents)
    raise RuntimeError(f"Multiple agents are configured — specify which one: {names}.")


def _resolve_target(target: str) -> tuple[str, str]:
    """Returns (phone, name) — name may be empty if `target` was already a phone number."""
    if _looks_like_phone(target):
        return target, ""

    matches = _request("GET", "/contacts/", params={"q": target, "limit": 6}).get("results", [])
    if not matches:
        raise RuntimeError(
            f"No saved contact matches '{target}'. Call again with a phone number instead."
        )
    if len(matches) > 1:
        options = "; ".join(f"{c['name']} ({c['phone']})" for c in matches)
        raise RuntimeError(
            f"More than one contact matches '{target}': {options}. Call again with the exact phone number."
        )
    contact = matches[0]
    return contact["phone"], contact.get("name", "")


def _resolve_active_campaign(agent: dict) -> dict:
    name = CAMPAIGN_NAME_TEMPLATE.format(agent=agent["name"])
    campaigns = _request("GET", "/campaigns/", params={"status": "active", "limit": 50}).get("results", [])
    match = next((c for c in campaigns if c["name"] == name), None)
    if match:
        return match

    created = _request("POST", "/campaigns/", json_body={
        "name": name, "widget_id": agent["id"], "status": "active",
    })
    return {"id": created["campaign_id"], "name": created["name"], "status": created["status"]}


# ---------------------------------------------------------------------------
# MCP tools
# ---------------------------------------------------------------------------

@mcp.tool()
def icallagent_login() -> str:
    """
    Connect this machine to an iCallAgent workspace. Opens a browser to
    iCallAgent's login/consent screen; once approved, the connection is
    saved and reused by the other tools until it's revoked. Call this
    first if icallagent_call reports it isn't connected yet.
    """
    _run_login_flow()
    return "Connected to iCallAgent. You can now place calls."


@mcp.tool()
def icallagent_call(target: str, message: str, agent_name: str | None = None, confirm: bool = False) -> str:
    """
    Resolve and, once confirmed, queue an outbound AI phone call.

    Always call this FIRST with confirm left False. It resolves who will actually be called (a
    contact-name search or the phone number as given) and which agent will make the call, and
    returns that for you to show the user verbatim and get an explicit yes on — never assume and
    set confirm=true on the first call. Only call again with confirm=true, same arguments, once
    the user has approved what was resolved. No call is placed while confirm is False.

    Args:
        target: Who to call — a saved contact's name, or a phone number
            (any format; include a country code if not a US/CA number).
        message: What the agent should say or find out on the call, in
            plain English (e.g. "ask if she's still interested in renewing
            and get a good callback time").
        agent_name: Which configured iCallAgent agent should make the call.
            Omit if the workspace only has one Live agent.
        confirm: False (default) previews who/which agent will be used without dialing.
            True actually queues the call — only after the user has confirmed the preview.

    Once queued (confirm=true), returns the call's id. The call is not placed instantly — the
    dialer picks it up on its normal schedule. Use icallagent_get_call_result with the returned
    scheduled_call_id to find out how it went.
    """
    agent = _resolve_agent(agent_name)
    phone, contact_name = _resolve_target(target)
    who = contact_name or phone

    if not confirm:
        return (
            f"About to call {who} ({phone}) via {agent['name']} to: \"{message}\". "
            "Show this to the user and get an explicit yes before proceeding. Once confirmed, "
            "call icallagent_call again with the same arguments plus confirm=true to actually "
            "place it — or call it again with different arguments first if something's wrong."
        )

    campaign = _resolve_active_campaign(agent)
    result = _request("POST", "/contacts/", json_body={
        "phone": phone,
        "campaign_id": campaign["id"],
        "name": contact_name,
        "notes": message,
    })

    return (
        f"Queued a call to {who} via {agent['name']}. "
        f"scheduled_call_id={result['scheduled_call_id']}. "
        "Use icallagent_get_call_result with this id to wait for the outcome."
    )


@mcp.tool()
def icallagent_get_call_result(scheduled_call_id: int, wait_seconds: int = DEFAULT_WAIT_SECONDS) -> str:
    """
    Wait for a queued call to finish and report what happened.

    Polls the call's status until it reaches a terminal state or
    `wait_seconds` elapses (default 180). If it's still in progress when
    this returns, call it again with the same scheduled_call_id to keep
    waiting.

    Args:
        scheduled_call_id: The id returned by icallagent_call.
        wait_seconds: How long to wait before giving up and returning
            "still in progress" (default 180, i.e. 3 minutes).
    """
    deadline = time.monotonic() + max(1, wait_seconds)

    while True:
        data = _request("GET", f"/scheduled-calls/{scheduled_call_id}/")
        if data["status"] != "in_progress":
            break
        if time.monotonic() >= deadline:
            return (
                f"Call {scheduled_call_id} is still in progress after {wait_seconds}s. "
                "Call icallagent_get_call_result again to keep waiting."
            )
        time.sleep(POLL_INTERVAL_SECONDS)

    if data["status"] == "completed":
        if data["summary"]:
            return f"Call completed ({data['duration_seconds']}s). {data['summary']}"
        return f"Call completed ({data['duration_seconds']}s), but no summary is available (short call or no transcript)."

    reason = data["result_detail"] or "unknown reason"
    return f"Call failed: {reason}."


if __name__ == "__main__":
    mcp.run()
