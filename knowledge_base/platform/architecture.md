# OpenClaw Architecture

OpenClaw is a personal AI assistant you run on your own devices. It is built around a **Gateway** process that acts as the control plane, connecting messaging channels, model providers, and agent runtimes into one always-on system.

## Core Components

### Gateway (Daemon)

The Gateway is a single long-lived Node.js process (Node 22+) that owns all messaging surfaces and agent state. It listens on a WebSocket server (default `127.0.0.1:18789`) and serves as the central hub for:

- Maintaining connections to messaging channels (WhatsApp via Baileys, Telegram via grammY, Discord, Slack, Signal, iMessage, and many more via plugins).
- Exposing a typed WebSocket API for clients and nodes.
- Running the agent runtime (based on pi-mono) with tool execution, streaming, and session management.
- Hosting the Canvas and web Control UI over HTTP on the same port.

Only one Gateway runs per host. It is supervised by launchd (macOS) or systemd (Linux) for auto-restart.

### Clients

Control-plane clients -- the macOS companion app, CLI, and web UI -- connect to the Gateway over WebSocket. They send requests (`health`, `status`, `send`, `agent`) and subscribe to events (`tick`, `agent`, `presence`, `shutdown`).

### Nodes

Native apps on macOS, iOS, and Android connect as nodes with `role: node`, providing device-specific capabilities like camera, screen recording, location, and Canvas rendering. Nodes use device-based pairing with challenge-response signatures.

## Wire Protocol

Transport is WebSocket with JSON text frames. The first frame must be a `connect` handshake. After that, communication uses request/response pairs and server-push events. Idempotency keys are required for side-effecting methods (`send`, `agent`) to support safe retries.

## Security Model

All WebSocket clients must include a device identity on connect. New devices require pairing approval. Local (loopback) connections can be auto-approved. Non-local connections require explicit approval. Gateway auth tokens can be enforced via `OPENCLAW_GATEWAY_TOKEN`. Remote access is recommended over Tailscale or SSH tunnels.

## Tech Stack

OpenClaw is written in TypeScript (ESM) and runs on Node.js. The build system uses pnpm, tsdown, and oxlint/oxfmt. Native companion apps exist for macOS (Swift), iOS (Swift), and Android (Kotlin). The project is MIT-licensed and open source at github.com/openclaw/openclaw.
