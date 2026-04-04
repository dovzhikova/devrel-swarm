# Self-Hosting OpenClaw

OpenClaw is designed to run on your own devices. There is no hosted SaaS version -- you install and operate it yourself. This keeps your data local and your assistant always available.

## System Requirements

- **Node.js 24** (recommended; Node 22 LTS 22.16+ still supported)
- macOS, Linux, or Windows (WSL2 strongly recommended on Windows)
- pnpm only needed for building from source

## Installation Methods

### Installer Script (Recommended)

The one-liner handles Node detection, CLI installation, and onboarding:

```bash
curl -fsSL https://openclaw.ai/install.sh | bash
```

On Windows PowerShell: `iwr -useb https://openclaw.ai/install.ps1 | iex`

### npm / pnpm

```bash
npm install -g openclaw@latest
openclaw onboard --install-daemon
```

### From Source

```bash
git clone https://github.com/openclaw/openclaw.git
cd openclaw && pnpm install && pnpm build
openclaw onboard --install-daemon
```

### Docker

Docker is optional, useful for containerized or headless deployments. A `docker-setup.sh` script handles image build, onboarding, and gateway startup. Pre-built images are available at `ghcr.io/openclaw/openclaw`.

Other supported methods: Podman, Nix, Ansible, Bun, and cloud platforms (Fly.io, GCP, Hetzner, Railway, Render, Northflank, Kubernetes).

## Daemon Setup

The onboarding wizard (`openclaw onboard --install-daemon`) installs the Gateway as a system service via launchd (macOS) or systemd (Linux) so it stays running across reboots.

## Configuration

Configuration lives at `~/.openclaw/openclaw.json`. Key settings include:

- `agents.defaults.workspace` -- the agent's working directory
- `agents.defaults.model` -- primary and fallback model selection
- `channels.*` -- per-channel configuration and allowlists
- `session.*` -- session reset policy and DM scope

Use `openclaw config set <key> <value>` for CLI-based configuration, or edit the JSON file directly.

## Post-Install Verification

```bash
openclaw doctor      # check for config issues
openclaw status      # gateway and channel status
openclaw dashboard   # open the browser Control UI
```

## Updating

```bash
npm install -g openclaw@latest
openclaw doctor   # verify after update
```

Development channels: `stable` (tagged releases), `beta` (prereleases), and `dev` (main branch HEAD). Switch with `openclaw update --channel <name>`.
