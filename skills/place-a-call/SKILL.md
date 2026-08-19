---
description: Place an outbound call through iCallAgent and report back what happened. Use when the user asks to call, phone, or ring someone and get told how it went.
---

You have three tools from the `icallagent` MCP server: `icallagent_login`, `icallagent_call`, and
`icallagent_get_call_result`.

When the user asks you to call someone:

1. Make sure you have a clear **who** (a saved contact's name, or a phone number) and **what to
   say/find out**. Ask if either is missing — don't guess a phone number or invent talking points.
2. Call `icallagent_call` with `target` and `message` (and `agent_name` only if the user named a
   specific agent, or a prior error listed more than one) — leave `confirm` at its default (False).
3. If it reports "not connected yet", call `icallagent_login`, tell the user to approve it in the
   browser that opens, then retry from step 2.
4. If it reports more than one matching contact or agent, ask the user to disambiguate rather than
   picking one yourself, then retry from step 2 with the clarified target/agent_name.
5. Otherwise it returns a preview of exactly who will be called, on what number, via which agent,
   saying what. **Show this to the user verbatim and wait for an explicit yes** — never skip this
   or assume the resolved contact/agent is right. A wrong-number call is a real mistake, not a
   minor one.
6. Only once the user confirms, call `icallagent_call` again with the same arguments plus
   `confirm=true`. This is what actually places the call.
7. Then call `icallagent_get_call_result` with the returned `scheduled_call_id`. This can take a
   couple of minutes (it waits for the call to actually happen) — let the user know you're waiting
   on the call rather than going quiet.
8. If it comes back "still in progress", call it again with the same id rather than giving up.
9. Report the final outcome in your own words from the summary/reason returned — don't just paste
   the raw tool output.
