# Agent System

OpenClaw runs a single embedded agent runtime derived from pi-mono. The agent is the core "brain" that processes messages, calls tools, and generates responses.

## What Is an Agent?

An agent is a fully scoped instance with its own:

- **Workspace** -- a directory containing persona files (AGENTS.md, SOUL.md, USER.md, TOOLS.md, IDENTITY.md) and memory files. This is the agent's working directory for tool execution.
- **State directory** -- holds auth profiles, model registry, and per-agent config at `~/.openclaw/agents/<agentId>/agent/`.
- **Session store** -- chat history and routing state at `~/.openclaw/agents/<agentId>/sessions/`.

## Bootstrap Files

On the first turn of a new session, OpenClaw injects workspace files into the agent context:

- `AGENTS.md` -- operating instructions and persistent memory
- `SOUL.md` -- persona, boundaries, and tone
- `TOOLS.md` -- user-maintained tool usage notes
- `BOOTSTRAP.md` -- one-time first-run ritual (deleted after completion)
- `IDENTITY.md` -- agent name, vibe, and emoji
- `USER.md` -- user profile and preferred address

Blank files are skipped. Large files are truncated to keep prompts lean.

## Agent Loop

The agent loop is the lifecycle for processing a message:

1. Intake and session resolution
2. Context assembly (system prompt, workspace files, conversation history)
3. Model inference with streaming
4. Tool execution (read, write, exec, edit, browser, etc.)
5. Streaming replies back to the originating channel
6. Session persistence

Runs are serialized per session to prevent races. The agent emits lifecycle and stream events as it thinks, calls tools, and streams output.

## Thinking Modes

OpenClaw supports thinking/reasoning modes that can be set per-message:

- `openclaw agent --message "plan this" --thinking high` enables extended thinking
- Thinking level can be toggled via `/thinking` slash command in chat

## Multi-Agent

Multiple agents can run in one Gateway, each with isolated workspaces, sessions, and auth. Inbound messages are routed to agents via configurable bindings. This enables scenarios like:

- A personal agent with full access
- A work agent with read-only tools
- A public-facing agent with no filesystem access

## Built-in Tools

Core tools are always available (subject to policy): `read`, `exec`, `edit`, `write`, and related system tools. Additional tools come from plugins. Tool availability is controlled via allow/deny lists per agent.
