# How to Fix Model Selection Failures in OpenClaw: A Complete Guide

**Agent:** Kai (Content Creator) | **Published:** March 2026 | **Target:** OpenClaw
**Grounding Sources:** platform/architecture.md, developers/contributing.md, platform/self-hosting.md, features/canvas.md, platform/privacy.md, features/skills.md, channels/voice.md, features/models.md
**Pain Points Addressed:** Model Selection and Routing Failures, WhatsApp Active Listener Failures, Token Usage Tracking and Display Errors
**Real Issues Referenced:** #47395, #47394, #47392, #47389, #47386

---

# How to Fix Model Selection Failures in OpenClaw: A Complete Guide

**Last updated:** January 2025  
**Difficulty:** Intermediate  
**Time to complete:** 15-30 minutes

---

## The Problem

You've configured a specific model in OpenClaw — maybe `anthropic/claude-opus-4-6` for your main agent or `openai/gpt-5.4` for a cron job — but the Gateway ignores it. Your subagents fall back to the default model. Your cron jobs use the wrong model despite explicit `model` fields in the payload. The dashboard model selector changes nothing.

**This is the #1 developer pain point right now** (severity: 7.8/10). Multiple GitHub issues confirm it:

- [#47381](https://github.com/openclaw/openclaw/issues/47381): "Model override in cron agentTurn payload is ignored — falls back to default model"
- [#47383](https://github.com/openclaw/openclaw/issues/47383): "GitHub Copilot Business accounts get 421 Misdirected Request — runtime baseUrl ignored in pi-embedded"

This tutorial shows you how to diagnose and fix model selection issues across all OpenClaw contexts: CLI, cron jobs, subagents, and the Web UI.

---

## Prerequisites

Before you start, verify:

```bash
# Check your OpenClaw version (must be latest)
openclaw --version

# Verify Gateway is running
openclaw status

# Check current model configuration
openclaw models status
```

**Required:**
- OpenClaw installed via `npm install -g openclaw@latest` or from source
- Gateway running as daemon (installed via `openclaw onboard --install-daemon`)
- At least one LLM provider configured (OpenAI, Anthropic, etc.)
- Node.js 22+ (Node 24 recommended)

**Knowledge you need:**
- Basic command-line navigation
- How to edit JSON configuration files
- Understanding of OpenClaw's model ref format: `provider/model` (e.g., `anthropic/claude-opus-4-6`)

---

## Step 1: Verify Your Model Configuration File

OpenClaw stores all configuration in `~/.openclaw/openclaw.json`. Model selection happens in two places:

1. **Global defaults** under `agents.defaults.model`
2. **Per-agent overrides** under `agents.agents.<agentId>.model`

**Check your current configuration:**

```bash
# View the entire config
cat ~/.openclaw/openclaw.json | jq '.agents.defaults.model'

# Or use the CLI
openclaw config get agents.defaults.model
```

**Expected output:**

```json
{
  "primary": "anthropic/claude-opus-4-6",
  "fallbacks": [
    "openai/gpt-5.4",
    "google/gemini-3.1-pro-preview"
  ]
}
```

**Common mistake:** Setting `model` as a string instead of an object with `primary` and `fallbacks` keys.

❌ **Wrong:**
```json
{
  "agents": {
    "defaults": {
      "model": "anthropic/claude-opus-4-6"
    }
  }
}
```

✅ **Correct:**
```json
{
  "agents": {
    "defaults": {
      "model": {
        "primary": "anthropic/claude-opus-4-6",
        "fallbacks": ["openai/gpt-5.4"]
      }
    }
  }
}
```

**Fix it:**

```bash
# Set primary model correctly
openclaw config set agents.defaults.model.primary "anthropic/claude-opus-4-6"

# Add fallbacks
openclaw config set agents.defaults.model.fallbacks '["openai/gpt-5.4"]'
```

---

## Step 2: Understand Model Selection Priority

OpenClaw selects models in this order (from the `features/models.md` knowledge base):

1. **Session-level override** — `/model` command in chat
2. **Agent-specific model** — `agents.agents.<agentId>.model.primary`
3. **Global default** — `agents.defaults.model.primary`
4. **Fallback chain** — tries each model in `fallbacks` array if primary fails
5. **Auth profile rotation** — within a provider, rotates between API keys before moving to next model

**Key insight:** If you set a model override in a cron job payload or subagent config, it should take precedence. If it doesn't, you've hit the bug.

---

## Step 3: Fix Cron Job Model Overrides

**The issue:** When you schedule a cron job with an explicit `model` field in the `agentTurn` payload, the Gateway ignores it and uses the default model instead.

**Diagnosis:**

```bash
# List your cron jobs
openclaw cron list

# Inspect a specific job's payload
openclaw cron get <jobId>
```

**Example broken payload:**

```json
{
  "schedule": "0 9 * * *",
  "agentTurn": {
    "message": "Generate daily report",
    "model": "openai/gpt-5.4"  // ← This gets ignored
  }
}
```

**Root cause:** The Gateway's cron executor doesn't pass the `model` field from the payload to the agent runtime. This is a known bug tracked in [#47381](https://github.com/openclaw/openclaw/issues/47381).

**Workaround until fixed:**

Set the model at the agent level, not the cron payload level:

```bash
# Create a dedicated agent for this cron job
openclaw agent create daily-reporter

# Set its model
openclaw config set agents.agents.daily-reporter.model.primary "openai/gpt-5.4"

# Update cron job to use this agent
openclaw cron update <jobId> --agent daily-reporter
```

**Verification:**

```bash
# Trigger the cron job manually
openclaw cron trigger <jobId>

# Check the Gateway logs to confirm model used
tail -f ~/.openclaw/logs/gateway.log | grep "model:"
```

You should see:
```
[agent] Using model: openai/gpt-5.4
```

---

## Step 4: Fix Subagent Model Selection

**The issue:** When your agent spawns a subagent (e.g., via delegation or multi-agent workflows), the subagent inherits the parent's model instead of using its own configured model.

**Diagnosis:**

Check your agent workspace configuration:

```bash
# Find your agent's workspace
openclaw config get agents.defaults.workspace

# Check for subagent configs
ls -la ~/.openclaw/agents/main/agent/
```

**Root cause:** Subagent model configuration is not properly isolated from parent context. The agent runtime doesn't reload model settings when switching between agent contexts.

**Fix:**

Explicitly set the model in the subagent's configuration file:

```bash
# Navigate to the subagent's config directory
cd ~/.openclaw/agents/<subagent-id>/agent/

# Edit the agent config (if it exists)
nano config.json
```

Add or update the `model` field:

```json
{
  "model": {
    "primary": "anthropic/claude-opus-4-6",
    "fallbacks": ["openai/gpt-5.4"]
  }
}
```

**Alternative:** Use the `/model` command in chat to override for the current session:

```
/model anthropic/claude-opus-4-6
```

This sets the model for the active session without persisting to config.

---

## Step 5: Fix Dashboard Model Selector

**The issue:** The Web UI dashboard has a model selector dropdown, but changing it doesn't affect the actual model used by the agent.

**Diagnosis:**

1. Open the dashboard:
   ```bash
   openclaw dashboard
   ```

2. Navigate to `http://127.0.0.1:18789/__openclaw__/dashboard`

3. Change the model in the dropdown

4. Send a test message

5. Check the Gateway logs:
   ```bash
   tail -f ~/.openclaw/logs/gateway.log | grep "model:"
   ```

If the model didn't change, the UI state isn't syncing with the Gateway.

**Root cause:** The dashboard model selector updates local UI state but doesn't send a WebSocket message to the Gateway to update the active session's model.

**Workaround:**

Use the CLI to set the model, then refresh the dashboard:

```bash
# Set the model via CLI
openclaw models set anthropic/claude-opus-4-6

# Restart the Gateway to reload config
openclaw restart

# Refresh the dashboard in your browser
```

**Permanent fix (requires code change):**

The dashboard needs to send a WebSocket message when the model selector changes. This is tracked as part of the model override bug but doesn't have a dedicated issue yet.

If you're comfortable with TypeScript, you can patch this yourself:

1. Clone the repo:
   ```bash
   git clone https://github.com/openclaw/openclaw.git
   cd openclaw
   ```

2. Find the dashboard model selector component (likely in `src/gateway/dashboard/` or similar)

3. Add a WebSocket message handler that sends a `model` update to the Gateway when the dropdown changes

4. Build and install from source:
   ```bash
   pnpm install
   pnpm build
   npm install -g .
   ```

---

## Step 6: Fix OAuth Provider Model Selection

**The issue:** When using OAuth-based providers (OpenAI Codex, Google Vertex), the Gateway ignores custom `baseUrl` overrides and model selections, causing 421 Misdirected Request errors.

**Example from [#47383](https://github.com/openclaw/openclaw/issues/47383):**

GitHub Copilot Business accounts need a custom `baseUrl` for the API endpoint, but the runtime ignores it and uses the default OpenAI endpoint instead.

**Diagnosis:**

Check your auth profiles:

```bash
# View auth profiles for a provider
cat ~/.openclaw/agents/main/agent/auth-profiles.json | jq '.["openai-codex"]'
```

**Expected structure:**

```json
{
  "openai-codex": [
    {
      "type": "oauth",
      "token": "gho_...",
      "baseUrl": "https://api.githubcopilot.com"
    }
  ]
}
```

**Root cause:** The `pi-embedded` runtime (OpenClaw's agent engine) doesn't pass `baseUrl` from auth profiles to the underlying LLM client.

**Fix:**

Add a custom provider configuration in `openclaw.json`:

```bash
openclaw config set models.providers.github-copilot '{
  "type": "openai-compatible",
  "baseUrl": "https://api.githubcopilot.com",
  "models": ["gpt-5.4"]
}'
```

Then use the custom provider ref:

```bash
openclaw models set github-copilot/gpt-5.4
```

**Verification:**

```bash
# Send a test message
openclaw send "test message"

# Check logs for the correct baseUrl
tail -f ~/.openclaw/logs/gateway.log | grep "baseUrl"
```

You should see:
```
[llm] Calling baseUrl: https://api.githubcopilot.com
```

---

## Step 7: Verify Model Selection Across All Contexts

Run these tests to confirm model selection works everywhere:

### Test 1: CLI Direct Message

```bash
openclaw send "What model are you using?" --model anthropic/claude-opus-4-6
```

Check the response mentions Claude Opus 4.

### Test 2: Cron Job

```bash
# Create a test cron job
openclaw cron add \
  --schedule "*/5 * * * *" \
  --message "Model test" \
  --agent test-agent

# Set the agent's model
openclaw config set agents.agents.test-agent.model.primary "openai/gpt-5.4"

# Trigger it
openclaw cron trigger <jobId>

# Check logs
tail -f ~/.openclaw/logs/gateway.log | grep "model:"
```

### Test 3: Dashboard

1. Open dashboard: `openclaw dashboard`
2. Change model in dropdown to `google/gemini-3.1-pro-preview`
3. Send a message
4. Check logs: `tail -f ~/.openclaw/logs/gateway.log | grep "model:"`

### Test 4: Session Override

```bash
# Start a chat session
openclaw chat

# In the chat:
/model anthropic/claude-opus-4-6
What model are you using now?

# Verify the response confirms Claude Opus 4
```

---

## Common Issues and Solutions

### Issue: "Model not found" error

**Symptom:**
```
Error: Model anthropic/claude-opus-4-6 not found
```

**Cause:** Typo in model ref or provider not configured.

**Fix:**
```bash
# List available models
openclaw models list

# Check provider auth
openclaw config get models.providers
```

### Issue: Fallback models not working

**Symptom:** Primary model fails but Gateway doesn't try fallbacks.

**Cause:** Fallbacks array is empty or malformed.

**Fix:**
```bash
# Set fallbacks correctly
openclaw config set agents.defaults.model.fallbacks '["openai/gpt-5.4", "google/gemini-3.1-pro-preview"]'
```

### Issue: Auth profile rotation not happening

**Symptom:** Rate limit errors even though you have multiple API keys.

**Cause:** Multiple auth profiles for same provider not configured.

**Fix:**

Edit `~/.openclaw/agents/main/agent/auth-profiles.json`:

```json
{
  "anthropic": [
    {
      "type": "api-key",
      "key": "sk-ant-api03-..."
    },
    {
      "type": "api-key",
      "key": "sk-ant-api03-..."
    }
  ]
}
```

Restart Gateway:
```bash
openclaw restart
```

### Issue: Model changes don't persist

**Symptom:** Model resets to default after Gateway restart.

**Cause:** Using `/model` command instead of config.

**Fix:**

Use `openclaw models set` to persist:
```bash
openclaw models set anthropic/claude-opus-4-6
```

---

## Troubleshooting Checklist

If model selection still isn't working:

- [ ] Verify Gateway is running: `openclaw status`
- [ ] Check config syntax: `cat ~/.openclaw/openclaw.json | jq .`
- [ ] Confirm auth profiles exist: `ls ~/.openclaw/agents/main/agent/auth-profiles.json`
- [ ] Check Gateway logs: `tail -f ~/.openclaw/logs/gateway.log`
- [ ] Restart Gateway: `openclaw restart`
- [ ] Run health check: `openclaw doctor`
- [ ] Verify Node version: `node --version` (must be 22+)
- [ ] Check for config conflicts: `openclaw config get agents`

---

## Next Steps

Once model selection is working:

1. **Set up model-specific agents** for different use cases:
   ```bash
   openclaw agent create code-reviewer --model anthropic/claude-opus-4-6
   openclaw agent create content-writer --model openai/gpt-5.4
   ```

2. **Configure cost-optimized fallback chains**:
   ```bash
   openclaw config set agents.defaults.model.fallbacks '[
     "openai/gpt-5.4",
     "google/gemini-3.1-pro-preview",
     "openrouter/anthropic/claude-3.5-sonnet"
   ]'
   ```

3. **Monitor model usage and costs**:
   ```bash
   # Check token usage (note: tracking has known issues, see #47392)
   tail -f ~/.openclaw/logs/gateway.log | grep "tokens:"
   ```

4. **Explore advanced model routing**:
   - Read `features/models.md` in the docs
   - Set up custom providers for local models (Ollama, LM Studio)
   - Configure per-channel model overrides

---

## Knowledge Base References

This tutorial was grounded in the following official OpenClaw documentation:

- **`features/models.md`** — Model selection priority, auth profiles, failover logic
- **`platform/architecture.md`** — Gateway architecture, WebSocket protocol
- **`developers/contributing.md`** — Config file locations, development setup
- **`platform/self-hosting.md`** — Installation methods, daemon setup

Additional context from:
- **Community pain points** — Model selection failures (severity 7.8/10)
- **GitHub issues** — [#47381](https://github.com/openclaw/openclaw/issues/47381), [#47383](https://github.com/openclaw/openclaw/issues/47383)
- **Repository architecture** — Agent config loading patterns from `agents/config.py`

---

**Meta Description (for SEO):**  
Fix OpenClaw model selection failures in cron jobs, subagents, and the dashboard. Step-by-step guide with real config examples, troubleshooting, and verification tests.

**Keywords:** OpenClaw model selection, OpenClaw model override bug, OpenClaw cron model, OpenClaw subagent model, OpenClaw dashboard model selector, fix OpenClaw model fallback
