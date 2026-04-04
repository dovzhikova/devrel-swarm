# Messaging and Routing

OpenClaw routes messages deterministically based on channel configuration and agent bindings. The model does not choose where to send replies -- routing is controlled by the host configuration.

## How Routing Works

When a message arrives, OpenClaw picks one agent using this priority:

1. **Exact peer match** -- bindings with specific peer kind and ID.
2. **Parent peer match** -- thread inheritance from parent conversation.
3. **Guild + roles match** -- Discord guild ID plus role-based routing.
4. **Guild match** -- Discord guild ID alone.
5. **Team match** -- Slack team ID.
6. **Account match** -- channel account ID.
7. **Channel match** -- any account on that channel.
8. **Default agent** -- falls back to the default or `main` agent.

The matched agent determines which workspace, session store, and model configuration are used.

## Session Keys

Sessions are identified by keys that encode the routing context:

- **Direct messages** collapse to the agent's main session: `agent:<agentId>:main` (default). This provides continuity across devices and channels.
- **Groups** are isolated: `agent:<agentId>:<channel>:group:<id>`.
- **Threads** (Slack, Discord, Telegram topics) append thread IDs for isolation.
- **Cron jobs** get isolated keys: `cron:<jobId>`.

The `session.dmScope` setting controls DM grouping:
- `main` (default) -- all DMs share one session for continuity.
- `per-peer` -- isolate by sender ID across channels.
- `per-channel-peer` -- isolate by channel plus sender (recommended for multi-user).
- `per-account-channel-peer` -- isolate by account, channel, and sender.

## Multi-Agent Routing

Multiple isolated agents can run in one Gateway. Each agent has its own workspace, session store, auth profiles, and model configuration. Inbound messages are routed to agents via `bindings` in the config. Broadcast groups can run multiple agents for the same peer in parallel.

## Reply Routing

Replies are always sent back to the originating channel and conversation. Inbound reply context (quoted messages) is included as `[Replying to ...]` blocks. Block streaming can chunk long replies for gradual delivery, with per-channel configuration.

## Queue Modes

When messages arrive during an active agent run, OpenClaw uses queue modes to handle them:
- `steer` -- inject the message into the current run after the next tool call.
- `followup` -- hold messages until the current turn ends, then start a new turn.
- `collect` -- batch queued messages together.
