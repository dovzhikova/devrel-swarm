# Skills System

Skills in OpenClaw are reusable capabilities that extend the agent's knowledge and behavior. They are loaded into the agent context at session start and provide specialized instructions, tools, or workflows.

## How Skills Work

Skills are loaded from three locations, with workspace skills winning on name conflicts:

1. **Bundled** -- shipped with the OpenClaw install (baseline UX skills)
2. **Managed/local** -- installed at `~/.openclaw/skills`
3. **Workspace** -- placed in `<workspace>/skills` for per-agent customization

Skills can be gated by configuration or environment variables. They are injected into the system prompt so the agent knows what capabilities are available.

## ClawHub

New skills should be published to **ClawHub** (`clawhub.ai`) rather than added to the OpenClaw core. Core skill additions are rare and require a strong product or security justification.

This keeps the core lean while allowing the community to build and share specialized capabilities.

## Skill Development

Skills are directories containing instruction files that get injected into the agent's context. They can include:

- Markdown instruction files with domain-specific knowledge
- Tool definitions for specialized actions
- Configuration for when the skill should be active

## Per-Agent Skills

In multi-agent setups, skills can be scoped per agent:

- Workspace-level skills in `<workspace>/skills/` are specific to that agent
- Shared skills at `~/.openclaw/skills` are available to all agents on the host
- Skill loading order: workspace skills override shared skills of the same name

## MCP Support

OpenClaw supports Model Context Protocol (MCP) through `mcporter` (github.com/steipete/mcporter). This bridge approach keeps MCP integration flexible and decoupled from the core runtime:

- Add or change MCP servers without restarting the Gateway
- Keep the core tool/context surface lean
- Reduce MCP churn impact on core stability and security

## Configuration

Skills can be enabled, disabled, or configured in `openclaw.json`. The agent's `/context list` and `/context detail` commands show which skills are active and how much context they consume.
