# Voice Capabilities

OpenClaw supports voice input, voice output, and full voice calls through a combination of built-in features and the Voice Call plugin.

## Voice Notes and Transcription

OpenClaw can receive voice notes on messaging channels (WhatsApp, Telegram, etc.) and transcribe them using a configurable transcription hook. This enables voice-based interaction without requiring a phone call.

## Text-to-Speech (TTS)

OpenClaw includes TTS support via the `node-edge-tts` library, enabling the assistant to generate audio responses. This is used in voice call scenarios and can be configured for different voices and languages.

## Voice Call Plugin

The Voice Call plugin enables full telephone-style voice calls -- both outbound notifications and multi-turn conversations with the assistant.

### Supported Providers

- **Twilio** -- Programmable Voice plus Media Streams
- **Telnyx** -- Call Control v2
- **Plivo** -- Voice API plus XML transfer plus GetInput speech
- **Mock** -- development/testing mode with no network

### Installation

```bash
openclaw plugins install @openclaw/voice-call
```

Restart the Gateway after installation.

### Configuration

Configure under `plugins.entries.voice-call.config` with your chosen provider's credentials, `fromNumber`, and `toNumber`. The plugin runs inside the Gateway process.

### Usage

- CLI: `openclaw voicecall dial` to place outbound calls
- Agent tool: the `voice_call` tool is available during agent runs when the plugin is installed
- Inbound calls: configurable inbound policies determine how incoming calls are handled

### Architecture

The Voice Call plugin uses WebSocket-based media streams for real-time audio. Speech-to-text converts incoming audio, the agent processes the text, and TTS converts the response back to audio. This creates a natural conversational flow over telephone.

## iOS and Android Voice

The iOS and Android companion apps include native voice features:

- **iOS node**: voice input/output, camera, screen recording, location, and Canvas support
- **Android node**: voice tab, camera, Canvas, plus device commands (notifications, contacts, calendar, SMS)

Both mobile nodes connect to the Gateway via WebSocket with device pairing for security.

## macOS Voice

On macOS, voice wake forwarding allows triggering the assistant via voice commands. The macOS companion app supports voice input through the system microphone with configurable wake detection.
