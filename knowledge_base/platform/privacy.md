# Privacy and Security Model

OpenClaw is a local-first, self-hosted personal AI assistant. By design, your data stays on your devices and is never sent to OpenClaw's servers. The project treats security as a deliberate tradeoff: strong defaults without killing capability.

## Local-First Architecture

- The Gateway runs on your own hardware (laptop, server, VPS, or Docker container).
- All session transcripts, memory files, configuration, and credentials are stored locally under `~/.openclaw/`.
- There is no OpenClaw cloud service, no telemetry phone-home, and no account system.
- The only external calls are to the LLM providers and messaging channel APIs you explicitly configure.

## Credential Storage

Credentials are stored per-agent in local JSON files:

- **Auth profiles**: `~/.openclaw/agents/<agentId>/agent/auth-profiles.json` holds API keys and OAuth tokens for LLM providers.
- **Channel credentials**: stored in channel-specific config files under `~/.openclaw/`.
- Secrets are never committed to the repo or shared between agents unless explicitly copied.

API keys can be provided via environment variables (e.g., `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`) or through the onboarding wizard. OAuth flows (OpenAI Codex, Google) store tokens locally with automatic refresh.

## Gateway Security

- The Gateway binds to loopback (`127.0.0.1`) by default. Remote access requires explicit configuration.
- All WebSocket clients must complete a device pairing handshake with challenge-response signatures.
- Gateway auth tokens (`OPENCLAW_GATEWAY_TOKEN`) can be enforced for all connections.
- Remote access is recommended over Tailscale VPN or SSH tunnels, not public exposure.

## Channel Allowlists

Each messaging channel supports `allowFrom` configuration to restrict who can interact with your assistant. This is strongly recommended for WhatsApp and other channels where unknown senders could reach you.

## Agent Sandboxing

For untrusted or multi-user scenarios, OpenClaw supports Docker-based agent sandboxing:

- Tool execution (shell, file read/write) runs inside isolated Docker containers.
- Network access is `none` by default in sandboxed sessions.
- Per-agent sandbox profiles allow mixed access levels (full access for personal use, read-only for shared agents).

## Session Isolation

When serving multiple users, `session.dmScope` should be set to `per-channel-peer` or `per-account-channel-peer` to prevent context leaking between users. The default `main` scope shares context across all DMs and is only appropriate for single-user setups.

## Security Auditing

Run `openclaw security audit` to review DM settings, allowlists, and other security-relevant configuration. The `openclaw doctor` command also surfaces security warnings.
