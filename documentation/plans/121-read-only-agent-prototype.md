# Preview a playlist change and inspect a past run

In this fixture, moving Night Bus into newest-first order changes three of five positions. The user can see every position, including the station ID locked in second place, before deciding what to do. Two separately seeded journal entries answer a different question about changes that happened earlier. Previewing Night Bus creates no run to inspect. [Issue #121](https://github.com/chrisrogers37/shuffify/issues/121)

## Hosted product direction

Build the hosted MCP after the user-facing move to Raccoon at `raccoon.fm` and #120’s production cutover. The handoff is the owner completing the new web setup, running a routine through the workspace’s own Spotify app, and restoring its snapshot. At that point #121 can reuse the verified setup flow, workspace permissions, and operation journal on their permanent domain. The fixture serves as a design example until then. [Epic sequence](https://github.com/chrisrogers37/shuffify/issues/119) · [#120 cutover and acceptance](120-build/09_cutover.md)

A person first signs up for Raccoon with Google. This creates their workspace before they connect Spotify. They can then add Raccoon’s hosted MCP URL to their agent and authorize it to help with that existing workspace. The owner sees which agent is requesting access and can revoke its grant from the workspace. [Workspace design](120-workspaces-byo-spotify-app.md)

| What the user asks | What the hosted tools would do |
| --- | --- |
| “I’ve created my workspace. Help me connect Spotify.” | Read the workspace’s setup status and resume at the next incomplete step. Guide the owner through creating their Spotify developer app, registering Raccoon’s redirect URL, and adding their account to the app’s allowlist. With setup permission, save the app’s public client ID to the workspace and return a Raccoon link to connect Spotify. The owner approves access in the browser. The agent checks the resulting connection status and helps fix an incomplete or broken connection. |
| “Put Night Bus newest first and keep Station ID pinned.” | Once the workspace connection is ready, preview the order in chat. When the owner confirms, submit that reviewed change with playlist-write permission. Raccoon checks the snapshot and locks, queues the change once, and returns an operation ID. The agent can then report the verified outcome or explain what needs attention. |

Use HTTPS with Streamable HTTP and MCP protected-resource discovery. The agent obtains a token for the Shuffify MCP endpoint through browser authorization with PKCE. On each tool call, Shuffify checks the token’s audience, derives the workspace from the grant, and enforces the permissions needed to read, configure the workspace, or change a playlist. The Spotify connection has its own consent flow and server-side tokens. [MCP transports](https://modelcontextprotocol.io/specification/latest/basic/transports) · [MCP authorization](https://modelcontextprotocol.io/specification/latest/basic/authorization) · [MCP security guidance](https://modelcontextprotocol.io/specification/latest/basic/security_best_practices)

The fixture below exercises the preview and status tools. Workspace setup, agent authorization, and playlist writes are planned capabilities of the hosted product.

## Run the conversation

From the repository root with the backend Python dependencies installed, run the first command for the agent transcript. The other commands print both tool contracts or call either tool directly. The demo runs in one local process. Its only data source is the checked-in fixture. [Python dependencies](../guides/python-dependencies.md) · [Fixture workspace](../../harness/agent_preview/workspace.json)

```sh
python -m harness.agent_preview
python -m harness.agent_preview contracts
python -m harness.agent_preview call plan_playlist_operation '{"playlist_id":"NightBus","action":"newest_first"}'
python -m harness.agent_preview call get_operation_status '{"operation_id":42}'
```

## Tool contracts

The `contracts` command emits each tool's `name`, `description`, `inputSchema`, `outputSchema`, and `readOnlyHint` annotation. The caller's workspace comes from the local fixture binding. Both tools reject additional arguments, including an owner, workspace, or an instruction to execute. [Tool definitions](../../harness/agent_preview/tools.py)

| Tool | Required arguments | Successful result |
| --- | --- | --- |
| `plan_playlist_operation` | `playlist_id` (string of 1–255 characters), `action` (literal `newest_first`) | `state: "preview"`, playlist ID and name, observed snapshot ID, action, total track count and count of changed positions. The first 20 positions each include a one-based index, before/after track names and URIs, and a lock flag. `truncated` signals a longer order. |
| `get_operation_status` | `operation_id` (positive integer) | The web API's `OperationContext`. The outer `success` describes the lookup. `operation.state`, progress, timestamps and stored result describe the run. `operation.recovery` carries local review eligibility, action, reason and any related restoration IDs supplied by the journal projection. |

The planner uses Shuffify's Newest First algorithm with jitter set to 1. It honors valid position locks, checks that every track occurrence survives, and counts positions whose URI differs across the **whole** order. A result with zero changed positions is a valid preview. The snapshot identifies the version the user saw. It grants no authority to apply that order. [Shuffle service](../../shuffify/services/shuffle_service.py)

Status uses the same `public_operation` projection as the owner-scoped status endpoint. The two fixture rows are transient `JobExecution` objects with complete version-1 journal plans and acknowledged batches. Neither has a restore child. For a real parent, the repository also checks persisted restore children before exposing eligibility. Only the public DTO reaches the agent. [Public status schema](../../web/schemas/operation-context.schema.json) · [Owner lookup and projection](../../shuffify/services/operations/repository.py) · [Restore rules](../architecture/operation-restoration-contract.md)

A failed CLI call exits with code 2 and emits `{"error":"<code>","message":"..."}` on stderr. Invalid input returns `invalid_arguments`, an unknown tool returns `unknown_tool`, and a plan that loses track occurrences returns `invalid_plan`. A foreign or missing resource produces the same `playlist_not_found` or `operation_not_found` response.

## Agent transcript

**User** — Show me Night Bus newest first. What would move?

**Tool** — `plan_playlist_operation({"playlist_id":"NightBus","action":"newest_first"})`

**Agent** — Three of five positions would change in snapshot `night-bus-v8`. Both copies of Afterglow stay. This is a preview.

| Position | Current | Proposed |
| ---: | --- | --- |
| 1 | Paper Moon | Northbound |
| 2 | Station ID | Station ID (locked) |
| 3 | Afterglow | Afterglow |
| 4 | Northbound | Afterglow |
| 5 | Afterglow | Paper Moon |

**User** — Look up the earlier shuffle, run 41. Did it finish? Can I restore it?

**Tool** — `get_operation_status({"operation_id":41})`

**Agent** — Run 41 succeeded. The saved order was verified. Shuffify offers no operation-level restore for this run.

**User** — And run 42 on Late Set?

**Tool** — `get_operation_status({"operation_id":42})`

**Agent** — Run 42 needs attention. Its one batch was acknowledged, but a later playlist edit prevented final verification. The journal makes this run eligible for restore review. You would compare the current playlist with the saved order before confirming. Reading the status leaves the later edit in place.

A dispatched batch with an unknown outcome changes the answer to `available: false`, with reason `write_completion_unknown`. The demo's regression test changes just that evidence in memory and exercises the same projection again. [Prototype tests](../../tests/test_agent_preview.py)

## The same questions through a thin Spotify connector

Assume a connector that exposes playlist-item reads and writes. This comparison uses the same fixture scenario.

| User's question | Thin connector | Shuffify tools |
| --- | --- | --- |
| What would move on Night Bus? | The agent can read items and dates and compute a proposed order. To keep Station ID pinned it also needs Shuffify's lock data or an instruction from the user. | One tool uses the configured locks and preserves duplicate occurrences. Its answer identifies the snapshot and shows each proposed position. |
| Did run 42 finish? | A fresh playlist read can show today's order. That order alone cannot establish which change was acknowledged or whether a later edit happened before verification. | The operation ID retrieves the recorded acknowledgement and the terminal `needs_attention` outcome, even when the playlist has since changed. |
| Can I put it back? | An agent would need a separately retained baseline and a way to distinguish its own writes from subsequent edits before proposing another write. | The journal identifies confirmed effects and refuses ambiguous ones. An eligible result leads to a fresh review of current and desired contents in Shuffify. |

## What needs #120 before real use

- **A workspace principal.** Replace the fixture's fixed owner with the workspace belonging to the signed-in person. Scope playlist reads, track locks, operation and restoration-child lookups, and Activity to that workspace. Verify that one workspace gets the same response for another workspace's ID as for a missing one. Today's operation lookup takes a user ID. [Identity and ownership plan](120-build/04_rekey-to-workspace.md) · [Google sign-in plan](120-build/05_google-sign-in.md)
- **A workspace Spotify connection.** A real preview needs a complete, authorized playlist observation and its snapshot through the workspace's own app and bound Spotify account. #120 provides the client ID, PKCE connection and per-workspace token handling. Route a broken or revoked connection to setup. Historical status can still read the local journal independently of Spotify. [BYO connection](120-build/06_byo-app-and-connect.md) · [Broken connections](120-build/08_broken-app.md)
- **Agent authorization belongs to #121.** Once #120 supplies the workspace identity, the hosted endpoint can authorize setup tools even while the Spotify connection is incomplete. Playlist writes require a working connection and a write grant. Apply per-workspace and per-agent limits and give the agent a link to the owned Activity entry. [Agent design questions](https://github.com/chrisrogers37/shuffify/issues/121)
- **A future write needs its own contract.** Ask the user to confirm the exact reviewed change, recheck the snapshot, playlist ownership and locks, and admit it through the existing idempotent queue. Report the journal's terminal outcome. Restore requires its own fresh review and confirmation of the exact reviewed resources. This prototype ends at inspection. [Durable operations](../architecture/durable-operations.md) · [Reviewed restoration](../architecture/operation-restoration-contract.md)
