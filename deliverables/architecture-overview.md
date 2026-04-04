# Architecture Overview

**Root:** `.`

**Files:** 42 | **Lines:** 10788

## Languages

| Language | Files |
|----------|-------|
| python | 42 |

## Module Map

### `agents/`

- **`__init__.py`** (19 lines) — DevTools Advocate Agent System
- **`atlas.py`** (405 lines) — Atlas — Orchestrator Agent | Classes: SharedContext, DelegationResult, Atlas | Functions: main
- **`config.py`** (48 lines) — Agent configuration loader from YAML. | Classes: AgentConfig | Functions: load_config
- **`dex.py`** (658 lines) — Dex — Documentation Generator Agent | Classes: ParsedSymbol, ParsedModule, RepoAnalysis, Dex
- **`echo.py`** (420 lines) — Echo — Social Media Listener Agent | Classes: SocialMention, PlatformSummary, SocialListeningReport, Echo
- **`iris.py`** (334 lines) — Iris — Feedback Synthesizer Agent | Classes: FeedbackTheme, DeveloperJourneyStage, FeedbackSynthesis, Iris | Functions: _strip_markdown_fences
- **`kai.py`** (402 lines) — Kai — Content Creator Agent | Classes: ContentPiece, Kai
- **`llm.py`** (50 lines) — Shared Anthropic LLM client wrapper for all agents. | Classes: LLMClient
- **`nova.py`** (305 lines) — Nova — Growth Strategist Agent | Classes: ExperimentDesign, FunnelAnalysis, CohortSegment, Nova
- **`sage.py`** (318 lines) — Sage — Community Manager Agent | Classes: IssuePriority, SentimentScore, TriagedIssue, TriageReport, Sage
- **`vox.py`** (255 lines) — Vox — Video Tutorial Agent | Classes: Vox | Functions: _check_ffmpeg, _check_playwright

### `agents/video/`

- **`__init__.py`** (18 lines) — Video tutorial generation package for Vox agent.
- **`assembler.py`** (99 lines) — Video assembler — final FFmpeg pipeline for concatenation and audio merging. | Classes: VideoAssembler
- **`browser_recorder.py`** (120 lines) — Browser recorder — manages Playwright browser for screen recording. | Classes: BrowserAction, BrowserRecorder
- **`desktop_recorder.py`** (237 lines) — Desktop recorder — captures desktop app sessions using FFmpeg screen recording | Classes: DesktopAction, DesktopRecorder | Functions: _get_ffmpeg_input_format
- **`overlay_renderer.py`** (127 lines) — Overlay renderer — adds visual polish to recorded video segments using FFmpeg. | Classes: OverlayConfig, OverlayRenderer
- **`script_parser.py`** (150 lines) — ScriptParser — Converts markdown scripts and task strings into | Classes: TutorialStep, VideoTutorial, ScriptParser
- **`tts_engine.py`** (56 lines) — TTS engine — wraps OpenAI Text-to-Speech API for narration generation. | Classes: TTSEngine

### `tests/`

- **`__init__.py`** (1 lines)
- **`conftest.py`** (144 lines) — Shared pytest fixtures for devtools-advocate-agent tests. | Functions: posthog_client, knowledge_base_path, sample_issues, mock_llm_client, mock_github_tools
- **`test_api_client.py`** (70 lines) — Tests for PostHog API client module. | Classes: TestInsightQuerySerialization, TestPostHogClientUrlBuilding, TestPostHogClientInit
- **`test_atlas.py`** (216 lines) — Tests for Atlas orchestrator module. | Classes: TestSharedContext, TestDelegationResult, TestAtlasRetryLogic, TestAtlasOrchestration, TestAtlasWithDependencies
- **`test_code_validator.py`** (452 lines) — Tests for the CodeValidator — syntax validation of code blocks in markdown content. | Classes: TestExtractCodeBlocks, TestPythonValidation, TestJavaScriptValidation, TestJsonValidation, TestHtmlValidation, TestSqlValidation, TestSkipLanguages, TestValidateContent, TestKaiCodeValidation | Functions: validator
- **`test_config.py`** (54 lines) — Tests for agent config loader. | Classes: TestLoadConfig, TestAgentConfig
- **`test_dex.py`** (456 lines) — Tests for the Dex documentation generator agent. | Classes: TestParsePython, TestParseJsTs, TestScanRepo, TestGenerateArchitectureDoc, TestGenerateApiReference, TestGenerateModuleGuide, TestDexExecute, TestDexAtlasIntegration | Functions: dex, sample_repo
- **`test_echo.py`** (407 lines) — Tests for Echo social media listener agent. | Classes: TestEchoExecute, TestSentimentClassification, TestParseSearchResult, TestEngagementOpportunities, TestReputationRisks, TestPlatformSummaries, TestAggregateSentiment, TestExtractTopics, TestScanWeekly, TestSuggestEngagementAction | Functions: mock_search_tools, echo, echo_no_tools, sample_search_results
- **`test_github_tools.py`** (403 lines) — Tests for tools/github_tools.py using respx to mock httpx calls. | Classes: TestGitHubToolsInit, TestFetchRecentIssues, TestGetIssue, TestGetIssueComments, TestContributorProfile, TestSearchSimilarIssues, TestLabels, TestRepoStats
- **`test_integration.py`** (365 lines) — Integration tests for the full Atlas weekly cycle. | Classes: TestWeeklyCycleIntegration, TestWeeklyCycleErrorRecovery | Functions: make_atlas
- **`test_iris.py`** (315 lines) — Tests for Iris feedback synthesizer module. | Classes: TestIrisExecute, TestIrisJourneyMapping, TestIrisRecommendations, TestIrisContentOpportunities, TestIrisSynthesizeWeekly, TestFeedbackThemeDataclass, TestIrisExecuteWired, TestStripMarkdownFences | Functions: iris
- **`test_kai.py`** (129 lines) — Tests for Kai content creator module. | Classes: TestKaiKnowledgeBase, TestKaiExecuteWired, TestKaiOfficialDocsValidation, TestKaiWriteTutorial | Functions: kai
- **`test_llm.py`** (55 lines) — Tests for shared LLM client wrapper. | Classes: TestLLMClient
- **`test_mcp_server.py`** (382 lines) — Tests for tools/mcp_server.py — ToolDefinition and MCPServer. | Classes: TestToolDefinition, TestMCPServerInit, TestHandleRequest, TestRPCHelpers, TestCleanup, TestToolHandlerDelegation | Functions: _dummy_handler
- **`test_nova.py`** (159 lines) — Tests for Nova growth strategist module. | Classes: TestNovaCalculateSampleSize, TestNovaAnalyzeFunnel, TestNovaDesignExperiment, TestNovaExecuteWired | Functions: nova
- **`test_sage.py`** (166 lines) — Tests for Sage community manager module. | Classes: TestSageSentimentAnalysis, TestSageIssueCategorization, TestSageProductAreaDetection, TestSagePriorityScoring, TestSageTriageIssue, TestSageExecuteWired | Functions: sage
- **`test_search_tools.py`** (624 lines) — Tests for search tools module. | Classes: TestSearchResultDataclass, TestSearchPosthogDocs, TestSearchDiscourse, TestWebSearch, TestFetchUrlContent, TestRankResults, TestSearchToolsInit, TestFetchOfficialDocs | Functions: make_result
- **`test_vox.py`** (515 lines) — Tests for the Vox video tutorial agent — ScriptParser and dataclasses. | Classes: TestTutorialStep, TestVideoTutorial, TestScriptParser, TestTTSEngine, TestBrowserAction, TestBrowserRecorder, TestOverlayConfig, TestOverlayRenderer, TestVideoAssembler, TestVoxAgent, TestAtlasIntegration, TestDesktopAction, TestDesktopRecorder, TestGetFFmpegInputFormat

### `tools/`

- **`__init__.py`** (10 lines) — Tools module — API clients, GitHub integration, search, and MCP server.
- **`api_client.py`** (358 lines) — PostHog API v2 async client (legacy — retained for interface compatibility). | Classes: InsightQuery, FeatureFlag, Experiment, PostHogClient
- **`code_validator.py`** (302 lines) — Code Validator — Syntax validation for code snippets in generated content. | Classes: CodeBlock, ValidationResult, ValidationReport, CodeValidator
- **`github_tools.py`** (288 lines) — GitHub Tools — Issue, PR, and contributor analysis for openclaw/openclaw. | Classes: GitHubIssue, ContributorProfile, GitHubTools
- **`mcp_server.py`** (580 lines) — MCP Server — Model Context Protocol server exposing agent tools. | Classes: ToolDefinition, MCPServer | Functions: main
- **`search_tools.py`** (316 lines) — Search Tools — Web search, content retrieval, and documentation lookup. | Classes: SearchResult, DocSection, SearchTools
