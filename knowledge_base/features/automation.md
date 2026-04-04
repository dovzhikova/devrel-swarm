# Automation Features

OpenClaw provides several mechanisms for automated and scheduled interactions with the assistant, beyond manual messaging.

## Cron Jobs

OpenClaw supports scheduled tasks via cron-like configuration. Cron jobs run on configurable schedules using the `croner` library and can trigger agent runs with specific prompts or tasks.

- Each cron job gets an isolated session key (`cron:<jobId>`) or a persistent custom session.
- Cron events are emitted by the Gateway and can trigger agent runs automatically.
- Jobs run at the configured time in UTC by default.

Cron schedules are configured in `openclaw.json` and managed via the Gateway.

## Hooks

Hooks allow external systems to trigger agent runs via HTTP webhooks. Each hook gets a UUID-based session key (`hook:<uuid>`) unless explicitly configured otherwise.

Hooks enable integration with:
- CI/CD pipelines
- Monitoring systems
- External automation tools
- Custom applications

## Auto-Reply and Queue Modes

When messages arrive during an active agent run, OpenClaw handles them via queue modes:

- **Steer mode** -- inbound messages are injected into the current run after the next tool call, allowing real-time course correction.
- **Followup mode** -- messages are held until the current turn ends, then a new turn starts.
- **Collect mode** -- messages are batched together for processing.

## Session Reset Triggers

Sessions can be reset automatically or manually:

- **Daily reset** -- defaults to 4:00 AM local time on the gateway host.
- **Idle reset** -- configurable idle timeout (`session.idleMinutes`).
- **Manual reset** -- send `/new` or `/reset` in chat.
- Per-type and per-channel overrides available via `resetByType` and `resetByChannel`.

## Memory Flush

When a session nears auto-compaction (context window limit), OpenClaw triggers a silent agentic turn that reminds the model to write durable notes to disk before context is summarized. This preserves important information across compaction cycles.

## Compaction

When the conversation history fills the model's context window, OpenClaw can compact older context into a summary. Trigger manually with `/compact` or let it happen automatically based on token thresholds configured under `agents.defaults.compaction`.

## CLI Automation

The `openclaw agent` command enables scripted interactions:

```bash
openclaw agent --message "Generate the weekly report" --thinking low
```

This can be combined with system cron, shell scripts, or CI pipelines for automated workflows.
