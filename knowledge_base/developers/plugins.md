# Plugin Development

OpenClaw has an extensive plugin system. The core stays lean, and optional capabilities ship as plugins. Plugins can add channels, model providers, agent tools, memory backends, and skills.

## Plugin Distribution

- **npm packages** -- the preferred distribution path. Install with `openclaw plugins install <package>`.
- **Local extensions** -- for development, load from a local folder with `openclaw plugins install ./path/to/plugin`.
- **Bundled extensions** -- some plugins ship with OpenClaw under `extensions/` but may be disabled by default.

Community plugins are listed at docs.openclaw.ai/plugins/community. The bar for adding plugins to core is intentionally high.

## Plugin Manifest

Every plugin must include a `openclaw.plugin.json` manifest in the plugin root. OpenClaw uses this for discovery and config validation without executing plugin code.

Required fields:
- `id` (string) -- canonical plugin ID
- `configSchema` (object) -- JSON Schema for the plugin's config, even if empty

Optional fields:
- `kind` -- plugin kind (e.g., `"memory"`, `"context-engine"`)
- `channels` -- channel IDs registered by this plugin
- `providers` -- model provider IDs registered by this plugin
- `skills` -- skill directories to load
- `name`, `description`, `version` -- metadata

## Registering Agent Tools

Plugins can register agent tools (JSON-schema functions) exposed to the LLM:

```typescript
import { Type } from "@sinclair/typebox";

export default function (api) {
  api.registerTool({
    name: "my_tool",
    description: "Do a thing",
    parameters: Type.Object({
      input: Type.String(),
    }),
    async execute(_id, params) {
      return { content: [{ type: "text", text: params.input }] };
    },
  });
}
```

Tools can be **required** (always available) or **optional** (opt-in via allowlists). Optional tools need `{ optional: true }` and must be enabled in `tools.allow` config.

## Plugin SDK

OpenClaw exports a plugin SDK with per-channel helpers:

```typescript
import { ... } from "openclaw/plugin-sdk";
import { ... } from "openclaw/plugin-sdk/telegram";
import { ... } from "openclaw/plugin-sdk/discord";
```

## Configuration

Plugin config lives under `plugins.entries.<id>.config` in `openclaw.json`. Enable/disable with `plugins.entries.<id>.enabled`. Exclusive plugin slots (e.g., memory backend) are selected via `plugins.slots.*`.

## Plugin Kinds

- **Memory plugins** -- selected via `plugins.slots.memory` (default: `memory-core`). Only one active at a time.
- **Channel plugins** -- register new messaging channels.
- **Provider plugins** -- add model providers (e.g., Ollama, vLLM).
- **Context engine plugins** -- selected via `plugins.slots.contextEngine`.

## Development Tips

- Keep plugin-only deps in the extension's `package.json`, not the root.
- Avoid `workspace:*` in `dependencies` (breaks npm install). Use `devDependencies` or `peerDependencies` for the `openclaw` package.
- Tool names must not clash with core tool names.
- Test with `openclaw plugins install ./local-path` during development.
