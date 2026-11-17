# iCallAgent plugin for Claude Code

Ask Claude to place an outbound AI phone call in plain English — *"Call Jane and ask if she's still
interested in renewing, then tell me how it went"* — and it will queue the call through your
iCallAgent workspace, wait for it to finish, and summarize the outcome.

## Requirements

- An [iCallAgent](https://icallagent.com) account with at least one Live agent
- Python 3.10+ with the `mcp` and `httpx` packages:

  ```bash
  pip install mcp httpx
  ```

- Claude Code or Cowork. The plugin runs a small local helper on your computer, so it isn't
  available in Claude on the web.

## Getting started

1. Install the plugin from the Claude plugin directory.
2. In a chat, ask Claude to call someone, for example: *"Call Jane Doe, ask if she's still
   interested in renewing, and let me know what she says."*
3. The first time, Claude will open your browser to iCallAgent's sign-in page. Approve access and
   pick your workspace. You stay signed in after that.

## How it works

- **You confirm before it dials.** Claude first shows exactly who will be called, on which number,
  by which agent, and what the agent will say. Nothing is dialed until you say yes.
- **Who to call.** Give a saved contact's name or a phone number. If more than one contact or agent
  matches, Claude asks you which one you meant.
- **Where calls go.** Calls are placed in a campaign named `Claude Code requests — <agent name>`,
  which the plugin creates in your workspace the first time it's needed.
- **Getting the result.** Claude waits for the call to finish and then reports back from the
  AI-generated call summary. Calls aren't instant — the dialer picks them up on its normal schedule,
  so this can take a few minutes.

## Privacy and sign-in

- Sign-in uses OAuth in your browser; the plugin never sees your iCallAgent password.
- Your login is saved only on your computer, at `~/.icallagent/credentials.json`, readable only by
  your user account. Delete that file to sign out.
- The plugin talks only to `api.icallagent.com`.

## Troubleshooting

- **The browser didn't open on sign-in.** Claude will show a link you can open yourself.
- **Sign-in fails right away.** The plugin listens on local port `8934` during sign-in; make sure
  nothing else on your computer is using that port.
- **"No Live agent configured".** Set up and publish an agent in your iCallAgent dashboard first.
- **Your saved login expired.** Ask Claude to sign in to iCallAgent again.

## Support

Visit [icallagent.com](https://icallagent.com) or the
[API docs](https://docs.icallagent.com/api-reference/).

## License

[MIT](LICENSE)
