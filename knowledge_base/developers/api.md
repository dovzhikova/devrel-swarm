# CLI Commands and API

OpenClaw is primarily operated through its CLI (`openclaw`) and the Gateway's WebSocket API. There is no REST API -- all programmatic interaction goes through WebSocket or CLI commands.

## Core CLI Commands

### Setup and Configuration

```bash
openclaw onboard [--install-daemon]    # guided setup wizard
openclaw config set <key> <value>      # set config values
openclaw config get <key>              # read config values
openclaw doctor                        # diagnose config issues
openclaw setup                         # initialize workspace files
```

### Gateway Management

```bash
openclaw gateway [--port 18789]        # start gateway (foreground)
openclaw gateway status --deep         # deep health check
openclaw status                        # quick status overview
openclaw dashboard                     # open web Control UI
```

### Agent Interaction

```bash
openclaw agent --message "text"        # send a message to the agent
openclaw agent --message "plan" --thinking high  # with extended thinking
openclaw message send --to <id> --message "text" # send via channel
```

### Model Management

```bash
openclaw models list [--all]           # list models
openclaw models status                 # primary, fallbacks, auth
openclaw models set <provider/model>   # set primary model
openclaw models scan                   # discover OpenRouter models
openclaw models auth login --provider <name>  # add auth profile
```

### Channel Management

```bash
openclaw channels status [--probe]     # channel connection status
openclaw channels login                # WhatsApp QR pairing
openclaw channels add --channel <name> --token <token>  # add channel
```

### Session Management

```bash
openclaw sessions [--json]             # list sessions
openclaw sessions cleanup [--dry-run]  # maintenance
```

### Plugin Management

```bash
openclaw plugins install <package>     # install from npm
openclaw plugins enable <id>           # enable a plugin
openclaw plugins disable <id>          # disable a plugin
openclaw plugins list                  # list installed plugins
```

### Device Management

```bash
openclaw devices list                  # list paired devices
openclaw devices approve <requestId>   # approve a pairing request
```

## In-Chat Slash Commands

These commands work from any connected messaging channel:

- `/model` -- switch models for the current session
- `/new` or `/reset` -- start a fresh session
- `/status` -- session status and context usage
- `/context list` -- what is in the current context
- `/compact` -- summarize old context to free space
- `/thinking` -- toggle thinking/reasoning mode
- `/fast` -- toggle fast/priority processing mode
- `/stop` -- abort the current agent run
- `/send on|off` -- toggle delivery for this session

## WebSocket API

The Gateway exposes a typed WebSocket API on the configured port. Clients connect with a `connect` frame including device identity and optional auth token. After handshake:

- **Requests**: `{type:"req", id, method, params}` with responses `{type:"res", id, ok, payload}`
- **Events**: `{type:"event", event, payload}` for streaming and push notifications
- Methods include: `health`, `status`, `send`, `agent`, `agent.wait`, `sessions.list`

## Health Endpoints

HTTP endpoints (no auth required):
- `/healthz` -- liveness probe
- `/readyz` -- readiness probe (checks channel connectivity after startup grace)
