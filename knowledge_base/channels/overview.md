# Supported Channels

OpenClaw connects to the messaging platforms you already use. Each channel is managed by the Gateway, and multiple channels can run simultaneously. The assistant routes replies back to the channel where the message originated.

## Built-in Channels

These channels ship with the core OpenClaw package:

- **WhatsApp** -- Most popular integration. Uses Baileys (WhatsApp Web protocol). Requires QR code pairing. Stores session state on disk.
- **Telegram** -- Bot API via grammY. Simplest setup: just provide a bot token. Supports groups, forum topics, and inline features.
- **Discord** -- Bot API plus Gateway. Supports servers, channels, DMs, threads, and an interactive model picker.
- **Slack** -- Bolt SDK integration. Workspace apps with channel and thread support.
- **Signal** -- Uses signal-cli. Privacy-focused messaging with strong encryption.
- **iMessage (legacy)** -- macOS-only via imsg CLI. Deprecated in favor of BlueBubbles.
- **BlueBubbles** -- Recommended iMessage integration. Uses the BlueBubbles macOS server REST API with full feature support (edit, unsend, effects, reactions, group management).
- **IRC** -- Classic IRC server support with channels and DMs, plus pairing/allowlist controls.
- **WebChat** -- Built-in web UI served by the Gateway over WebSocket. No external service required.
- **Google Chat** -- Google Chat API app via HTTP webhook.

## Plugin Channels (Installed Separately)

These channels are available as extension plugins:

- **Microsoft Teams** -- Bot Framework integration for enterprise environments.
- **Matrix** -- Open protocol support via plugin.
- **Mattermost** -- Bot API plus WebSocket for self-hosted team chat.
- **LINE** -- LINE Messaging API bot for the popular Asian messenger.
- **Feishu/Lark** -- WebSocket-based bot for the Feishu platform.
- **Nextcloud Talk** -- Self-hosted chat via Nextcloud.
- **Nostr** -- Decentralized DMs via NIP-04.
- **Synology Chat** -- Synology NAS Chat via webhooks.
- **Tlon** -- Urbit-based messenger.
- **Twitch** -- Twitch chat via IRC connection.
- **Zalo** -- Zalo Bot API (popular in Vietnam).
- **Zalo Personal** -- Zalo personal account via QR login.

## Key Behaviors

- Channels run simultaneously; configure as many as needed.
- Fastest setup is Telegram (simple bot token). WhatsApp requires QR pairing.
- Group chat behavior varies by channel. Mention-based activation is common.
- DM pairing and allowlists are enforced for safety on all channels.
- Media support (images, audio, documents) varies by channel.
