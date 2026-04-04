# Model Support

OpenClaw supports a wide range of LLM providers and models. You bring your own API keys or OAuth tokens, and OpenClaw handles model selection, failover, and auth rotation.

## Model Selection

OpenClaw selects models in this order:

1. **Primary model** -- set via `agents.defaults.model.primary` (e.g., `anthropic/claude-opus-4-6`)
2. **Fallbacks** -- tried in order from `agents.defaults.model.fallbacks`
3. **Auth profile rotation** -- within a provider, OpenClaw rotates between available auth profiles before moving to the next model

Model refs use the format `provider/model` (e.g., `openai/gpt-5.4`, `google/gemini-3.1-pro-preview`).

## Built-in Providers

These require no custom provider config -- just set auth and pick a model:

- **OpenAI** -- `openai/gpt-5.4`, etc. Auth via `OPENAI_API_KEY`.
- **Anthropic** -- `anthropic/claude-opus-4-6`, etc. Auth via `ANTHROPIC_API_KEY` or setup-token.
- **OpenAI Codex** -- `openai-codex/gpt-5.4`. OAuth via ChatGPT subscription.
- **Google Gemini** -- `google/gemini-3.1-pro-preview`. Auth via `GEMINI_API_KEY`.
- **Google Vertex** -- `google-vertex/*`. Auth via gcloud ADC.
- **OpenRouter** -- `openrouter/*`. Access to many models via one key.
- **xAI** -- `xai/*`. Auth via `XAI_API_KEY`.
- **Mistral** -- `mistral/*`. Auth via `MISTRAL_API_KEY`.
- **Groq** -- `groq/*`. Auth via `GROQ_API_KEY`.
- **Ollama** -- `ollama/*`. Local models, no auth required.
- **Hugging Face** -- `huggingface/*`. Auth via `HF_TOKEN`.

Plus: OpenCode, Cerebras, GitHub Copilot, Z.AI, Vercel AI Gateway, Kilo Gateway, and more.

## Custom Providers

Use `models.providers` in config to add OpenAI-compatible or Anthropic-compatible endpoints: LM Studio, vLLM, SGLang, LiteLLM, Moonshot AI, MiniMax, and others.

## Auth and Failover

- Multiple auth profiles per provider (API keys and OAuth tokens can coexist).
- Session-sticky profile pinning for cache efficiency.
- Exponential backoff cooldowns on rate limits (1min, 5min, 25min, 1hr cap).
- Billing-failure detection with longer backoff (5hr start, 24hr cap).
- Automatic fallback to next model when all profiles for a provider fail.

## Model Management CLI

```bash
openclaw models list          # show configured models
openclaw models status        # primary, fallbacks, auth overview
openclaw models set <ref>     # change primary model
openclaw models scan          # discover free OpenRouter models
```

In chat, use `/model` to switch models for the current session without restarting.
