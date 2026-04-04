# Developer Feedback Synthesis — OpenClaw

**Agent:** Iris | **Issues Processed:** 40

## Top Themes
| # | Theme | Freq | Severity | Score |
|---|-------|------|----------|-------|
| 1 | Model Selection and Routing Failures | 5 | 7.8 | 39.0 |
| 2 | WhatsApp Active Listener Failures | 4 | 8.5 | 34.0 |
| 3 | Token Usage Tracking and Display Errors | 4 | 6.0 | 24.0 |
| 4 | Gateway Crashes and Instability | 3 | 8.0 | 24.0 |
| 5 | OAuth and Authentication Scope Issues | 3 | 7.0 | 21.0 |
| 6 | Tool Execution Validation and Reliability Issues | 3 | 7.0 | 21.0 |
| 7 | Feishu Channel Integration Problems | 3 | 6.5 | 19.5 |
| 8 | Subagent Communication and Visibility Gaps | 3 | 6.5 | 19.5 |
| 9 | Message Loss During Session Compaction | 2 | 9.0 | 18.0 |
| 10 | Cross-Platform Detection and Support Issues | 3 | 5.5 | 16.5 |
| 11 | Web UI Rendering and Feature Gaps | 3 | 5.0 | 15.0 |
| 12 | Runtime Configuration Override Failures | 1 | 7.5 | 7.5 |
| 13 | Long-Running Request Visibility | 1 | 5.0 | 5.0 |

## Theme Details

### Model Selection and Routing Failures
Developers consistently report that model overrides are ignored across multiple contexts: subagents fall back to default models, cron payloads ignore model specifications, and dashboard model selector constructs incorrect provider/model IDs when switching providers.
- Category:  | Issues: 

### WhatsApp Active Listener Failures
Multiple developers report WhatsApp QR code not displaying, outbound messages failing with 'No active WhatsApp Web listener' error, and listener state persisting incorrectly across restarts. This blocks basic WhatsApp channel functionality.
- Category:  | Issues: 

### Token Usage Tracking and Display Errors
Token metrics are consistently misreported across the platform: Web UI shows cumulative counts as context usage, console doesn't display spend correctly, and streaming APIs (Alibaba Bailian) fail to capture usage entirely.
- Category:  | Issues: 

### Gateway Crashes and Instability
Gateway exhibits critical stability issues including crashes after second cron job addition, heartbeat mechanisms that stop firing, and log rotation failures that cause resource exhaustion.
- Category:  | Issues: 

### OAuth and Authentication Scope Issues
Multiple OAuth integration failures due to missing or incorrect scopes: GitHub Copilot missing model.request scope causes 401 errors, and local gateway shows 'missing scope: operator.read' despite working correctly.
- Category:  | Issues: 

### Tool Execution Validation and Reliability Issues
Tool execution shows multiple failure modes: exec tool validation errors for missing command properties, tool failures displayed to users even when operations succeed, and ACP coding-agent unusable from Telegram due to run mode failures.
- Category:  | Issues: 

### Feishu Channel Integration Problems
Feishu channel shows multiple integration issues including first message not being delivered after session startup, LaTeX rendering problems in document creation, and lack of multi-bot support.
- Category:  | Issues: 

### Subagent Communication and Visibility Gaps
Developers struggle with subagent workflows due to inability to send progress updates to parent agents, lack of recursive listing capabilities, and permission configuration issues for subagent invocation.
- Category:  | Issues: 

### Message Loss During Session Compaction
Critical data loss occurs when session compaction triggers rollover, causing generated replies to not be delivered. This affects long-running conversations and undermines reliability.
- Category:  | Issues: 

### Cross-Platform Detection and Support Issues
OpenClaw shows platform-specific bugs including memory detection skipping macOS entirely (os.freemem/totalmem never reported on darwin), SSH transport missing on node hosts, and iOS TestFlight access requests indicating mobile deployment gaps.
- Category:  | Issues: 

### Web UI Rendering and Feature Gaps
Web control UI lacks essential rendering capabilities including image display (Markdown and Base64), collapsible tool output summaries, and server-side STT for voice input, limiting usability for rich interactions.
- Category:  | Issues: 

### Runtime Configuration Override Failures
Runtime configuration overrides are ignored in embedded contexts, specifically GitHub Copilot Business accounts receiving 421 Misdirected Request errors because baseUrl is not respected in pi-embedded runtime.
- Category:  | Issues: 

### Long-Running Request Visibility
Developers request channel silence watchdog functionality to send runtime beacons during long model requests, preventing users from thinking the bot is unresponsive during extended processing.
- Category:  | Issues: 

## Journey Map
| Stage | Friction |
|-------|---------|
