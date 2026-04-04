# Live Canvas

Live Canvas is a real-time rendering surface that the agent can create and edit during conversations. It enables visual, interactive output beyond plain text messages.

## How It Works

The Canvas host is served by the Gateway HTTP server on the same port (default 18789) under two paths:

- `/__openclaw__/canvas/` -- agent-editable HTML/CSS/JS content
- `/__openclaw__/a2ui/` -- A2UI (Agent-to-UI) host for structured UI rendering

The agent can write HTML, CSS, and JavaScript to the Canvas, creating interactive visualizations, forms, dashboards, or any web content. Changes are reflected in real time.

## Accessing Canvas

Canvas is accessible through:

- **Web browser** -- navigate to the Gateway's Canvas URL
- **macOS companion app** -- built-in Canvas view
- **iOS node** -- Canvas support with full device integration
- **Android node** -- Canvas tab in the companion app

## Use Cases

- Data visualizations and charts
- Interactive forms and wizards
- Live dashboards
- Document previews
- Game interfaces
- Any web-based UI the agent needs to render

## Architecture

Canvas uses the Gateway's existing WebSocket connection for real-time updates. The agent writes content using its standard file tools, and the Canvas host serves the rendered output. Node commands like `canvas.*` allow native apps to interact with Canvas content.

## A2UI (Agent-to-UI)

A2UI is a structured approach to agent-generated UI. It provides a bundled UI host that the agent can populate with structured components rather than raw HTML. The A2UI bundle is built separately (`pnpm canvas:a2ui:bundle`) and shipped with the OpenClaw package.

## Security

Canvas content is served locally by the Gateway. It is not exposed publicly unless the Gateway itself is exposed. In sandboxed agent sessions, Canvas tools are denied by default to maintain isolation.
