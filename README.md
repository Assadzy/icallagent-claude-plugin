# iCallAgent plugin for Claude Code

Ask Claude to place an outbound call ("call Jane and ask if she's still interested in renewing,
then tell me how it went") and it will queue the call through your iCallAgent workspace, wait for
it to finish, and summarize the outcome — using the same public API and OAuth2 flow as the
Make.com integration.

Requires an existing iCallAgent account. No backend access or setup needed beyond that — the
plugin ships with a pre-registered OAuth2 client id ("Claude Code Plugin" in iCallAgent's own
Django admin); you just log in with your own account and pick your own workspace.

## One-time setup

### 1. Install Python dependencies

```bash
pip install mcp httpx
```

### 2. (Only if pointing at a non-production backend)

```bash
export ICALLAGENT_API_BASE=http://127.0.0.1:8000
export ICALLAGENT_CLIENT_ID=<client id of an Application registered on THAT backend>
```

The built-in client id only exists on `api.icallagent.com`'s database — a local/staging backend
needs its own registered Application (see [For maintainers](#for-maintainers-registering-the-oauth2-application)).
Production users skip this step entirely.

## Try it

```bash
claude --plugin-dir /path/to/claude-plugin/icallagent
```

Then, in the chat: *"Call Jane Doe, ask if she's still interested in renewing, and let me know
what she says."* On first use Claude will call `icallagent_login`, which opens a browser to
iCallAgent's consent screen — approve it and pick the workspace, and the login is saved to
`~/.icallagent/credentials.json` (0600 permissions) for next time.

## How it works

- **Auth**: OAuth2 authorization-code + PKCE against `/oauth/authorize/` and `/oauth/token/`, with
  a loopback redirect (`http://127.0.0.1:8934/callback`) — the same pattern CLIs like `gh`/`gcloud`
  use, no client secret stored on disk.
- **Placing a call**: resolves the agent (by name, or the workspace's only Live agent) and the
  target (a saved contact search, or a phone number directly), auto-provisions a standing campaign
  named `Claude Code requests — <agent>` if one isn't already active, then queues the call via
  `POST /contacts/` — the exact path Make's "Send Contact" module and the Slack integration both
  use (`integrations/public_views.py::queue_outbound_call`).
- **Getting the result**: there's no server here for iCallAgent to push a `call.completed` webhook
  to, so the plugin polls `GET /scheduled-calls/{id}/` instead, which returns an AI-generated
  transcript summary once the call is done (reusing
  `integrations/tasks.py::_generate_call_summary`, the same generator behind the Slack reply).

## For maintainers: registering the OAuth2 Application

Only needed once per backend (already done for `api.icallagent.com`), or again for a separate
staging/local backend. In Django admin → OAuth2 Provider → Applications → Add:

| Field | Value |
|---|---|
| Client type | **Public** |
| Authorization grant type | **Authorization code** |
| Redirect URIs | `http://127.0.0.1:8934/callback` |
| Name | `Claude Code Plugin` |
| Skip authorization | unchecked |

No client secret needed — PKCE is required for public clients
(`icallagent/settings.py::_oauth2_pkce_required`). This also requires the `GET
/api/public/v1/scheduled-calls/{id}/` endpoint and `calls:read` scope
(`icallagent/integrations/public_views.py::ScheduledCallStatusView`) to be deployed on that backend.

## Known limitations

- The auto-provisioned campaign can only be reused while it stays `active`; there's no public
  endpoint to reactivate one that later gets paused/completed, so the plugin creates a fresh one
  in that case rather than trying to update it.
- Ambiguous contact/agent matches are surfaced as an error for Claude to ask the user about, not
  auto-resolved.
- The redirect port (`8934`) is fixed — set `ICALLAGENT_REDIRECT_PORT` and update the Application's
  redirect URI to match if it's already in use on your machine.
