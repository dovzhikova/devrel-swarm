"""Tests for src/devrel_swarm/tools/mcp_server.py — ToolDefinition and MCPServer."""

from dataclasses import dataclass
from unittest.mock import AsyncMock, patch

from devrel_swarm.tools.mcp_server import MCPServer, ToolDefinition

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _dummy_handler(**kwargs):
    return {"result": "ok"}


# ---------------------------------------------------------------------------
# TestToolDefinition
# ---------------------------------------------------------------------------


class TestToolDefinition:
    """Tests for the ToolDefinition class."""

    def test_to_manifest_returns_correct_keys(self):
        schema = {"type": "object", "properties": {"q": {"type": "string"}}}
        tool = ToolDefinition(
            name="my_tool",
            description="Does something useful",
            input_schema=schema,
            handler=_dummy_handler,
        )
        manifest = tool.to_manifest()
        assert manifest == {
            "name": "my_tool",
            "description": "Does something useful",
            "inputSchema": schema,
        }

    def test_to_manifest_excludes_handler(self):
        tool = ToolDefinition(
            name="no_handler_in_manifest",
            description="Check handler is private",
            input_schema={"type": "object"},
            handler=_dummy_handler,
        )
        manifest = tool.to_manifest()
        assert "handler" not in manifest

    def test_to_manifest_uses_input_schema_key_not_input_schema(self):
        """Verify the manifest key is 'inputSchema', not 'input_schema'."""
        tool = ToolDefinition(
            name="x", description="y", input_schema={"type": "object"}, handler=_dummy_handler
        )
        manifest = tool.to_manifest()
        assert "inputSchema" in manifest
        assert "input_schema" not in manifest


# ---------------------------------------------------------------------------
# TestMCPServerInit
# ---------------------------------------------------------------------------


@patch("devrel_swarm.tools.mcp_server.SearchTools")
@patch("devrel_swarm.tools.mcp_server.GitHubTools")
@patch("devrel_swarm.tools.mcp_server.PostHogClient")
class TestMCPServerInit:
    """Tests for MCPServer.__init__ and tool registration."""

    def test_init_no_keys_only_search_tools_registered(self, MockPostHog, MockGitHub, MockSearch):
        server = MCPServer()

        assert server._posthog is None
        assert server._github is None
        assert server._search is not None
        # Only the 4 search tools should be registered
        assert len(server._tools) == 4
        MockPostHog.assert_not_called()
        MockGitHub.assert_not_called()

    def test_init_all_keys_14_tools_registered(self, MockPostHog, MockGitHub, MockSearch):
        server = MCPServer(
            posthog_api_key="phx_key",
            posthog_project_id="123",
            github_token="ghp_token",
            firecrawl_api_key="fc_key",
        )

        assert server._posthog is not None
        assert server._github is not None
        assert len(server._tools) == 14

    def test_init_only_github_token_9_tools(self, MockPostHog, MockGitHub, MockSearch):
        server = MCPServer(github_token="ghp_token")

        assert server._github is not None
        assert server._posthog is None
        # 5 GitHub + 4 Search
        assert len(server._tools) == 9
        MockPostHog.assert_not_called()

    def test_init_only_posthog_keys_9_tools(self, MockPostHog, MockGitHub, MockSearch):
        server = MCPServer(posthog_api_key="phx_key", posthog_project_id="123")

        assert server._posthog is not None
        assert server._github is None
        # 5 PostHog + 4 Search
        assert len(server._tools) == 9
        MockGitHub.assert_not_called()

    def test_search_tool_always_initialised(self, MockPostHog, MockGitHub, MockSearch):
        MCPServer()
        MockSearch.assert_called_once()


# ---------------------------------------------------------------------------
# TestHandleRequest
# ---------------------------------------------------------------------------


@patch("devrel_swarm.tools.mcp_server.SearchTools")
@patch("devrel_swarm.tools.mcp_server.GitHubTools")
@patch("devrel_swarm.tools.mcp_server.PostHogClient")
class TestHandleRequest:
    """Tests for MCPServer._handle_request JSON-RPC routing."""

    async def test_initialize_returns_protocol_version_and_server_info(
        self, MockPostHog, MockGitHub, MockSearch
    ):
        server = MCPServer()
        response = await server._handle_request({"method": "initialize", "id": 1, "params": {}})

        assert response is not None
        result = response["result"]
        assert result["protocolVersion"] == MCPServer.PROTOCOL_VERSION
        assert result["serverInfo"]["name"] == MCPServer.SERVER_NAME
        assert result["serverInfo"]["version"] == MCPServer.SERVER_VERSION
        assert "capabilities" in result

    async def test_tools_list_returns_all_registered_tools(
        self, MockPostHog, MockGitHub, MockSearch
    ):
        server = MCPServer()
        response = await server._handle_request({"method": "tools/list", "id": 2, "params": {}})

        tools = response["result"]["tools"]
        assert isinstance(tools, list)
        assert len(tools) == len(server._tools)

    async def test_tools_list_manifest_shape(self, MockPostHog, MockGitHub, MockSearch):
        server = MCPServer()
        response = await server._handle_request({"method": "tools/list", "id": 3})
        tool = response["result"]["tools"][0]
        assert "name" in tool
        assert "description" in tool
        assert "inputSchema" in tool

    async def test_tools_call_unknown_tool_returns_error_32602(
        self, MockPostHog, MockGitHub, MockSearch
    ):
        server = MCPServer()
        response = await server._handle_request(
            {
                "method": "tools/call",
                "id": 4,
                "params": {"name": "nonexistent_tool", "arguments": {}},
            }
        )

        assert "error" in response
        assert response["error"]["code"] == -32602

    async def test_tools_call_success_returns_content(self, MockPostHog, MockGitHub, MockSearch):
        server = MCPServer()
        # Replace the search_posthog_docs tool's handler with a mock
        mock_handler = AsyncMock(return_value=[{"title": "doc", "url": "https://x.com"}])
        server._tools["search_devrel_ai_agents_docs"].handler = mock_handler

        response = await server._handle_request(
            {
                "method": "tools/call",
                "id": 5,
                "params": {
                    "name": "search_devrel_ai_agents_docs",
                    "arguments": {"query": "feature flags"},
                },
            }
        )

        assert "result" in response
        content = response["result"]["content"]
        assert isinstance(content, list)
        assert content[0]["type"] == "text"
        mock_handler.assert_awaited_once_with(query="feature flags")

    async def test_tools_call_handler_exception_returns_is_error(
        self, MockPostHog, MockGitHub, MockSearch
    ):
        server = MCPServer()
        broken_handler = AsyncMock(side_effect=RuntimeError("something broke"))
        server._tools["search_devrel_ai_agents_docs"].handler = broken_handler

        response = await server._handle_request(
            {
                "method": "tools/call",
                "id": 6,
                "params": {"name": "search_devrel_ai_agents_docs", "arguments": {"query": "test"}},
            }
        )

        assert response["result"]["isError"] is True
        assert "something broke" in response["result"]["content"][0]["text"]

    async def test_notifications_initialized_returns_none(
        self, MockPostHog, MockGitHub, MockSearch
    ):
        server = MCPServer()
        response = await server._handle_request({"method": "notifications/initialized", "id": None})
        assert response is None

    async def test_unknown_method_returns_error_32601(self, MockPostHog, MockGitHub, MockSearch):
        server = MCPServer()
        response = await server._handle_request(
            {"method": "totally/unknown", "id": 7, "params": {}}
        )

        assert "error" in response
        assert response["error"]["code"] == -32601


# ---------------------------------------------------------------------------
# TestRPCHelpers
# ---------------------------------------------------------------------------


class TestRPCHelpers:
    """Tests for _rpc_response and _rpc_error static methods."""

    def test_rpc_response_structure(self):
        result = MCPServer._rpc_response(42, {"key": "value"})
        assert result == {
            "jsonrpc": "2.0",
            "id": 42,
            "result": {"key": "value"},
        }

    def test_rpc_response_with_none_id(self):
        result = MCPServer._rpc_response(None, {})
        assert result["id"] is None
        assert result["jsonrpc"] == "2.0"

    def test_rpc_error_structure(self):
        result = MCPServer._rpc_error(99, -32600, "Invalid Request")
        assert result == {
            "jsonrpc": "2.0",
            "id": 99,
            "error": {"code": -32600, "message": "Invalid Request"},
        }

    def test_rpc_error_code_and_message_are_preserved(self):
        result = MCPServer._rpc_error(1, -32602, "Unknown tool: foo")
        assert result["error"]["code"] == -32602
        assert result["error"]["message"] == "Unknown tool: foo"


# ---------------------------------------------------------------------------
# TestCleanup
# ---------------------------------------------------------------------------


@patch("devrel_swarm.tools.mcp_server.SearchTools")
@patch("devrel_swarm.tools.mcp_server.GitHubTools")
@patch("devrel_swarm.tools.mcp_server.PostHogClient")
class TestCleanup:
    """Tests for MCPServer._cleanup."""

    async def test_cleanup_all_clients_called(self, MockPostHog, MockGitHub, MockSearch):
        server = MCPServer(
            posthog_api_key="phx_key",
            posthog_project_id="123",
            github_token="ghp_token",
        )
        # Attach async close mocks to the instances returned by the mock constructors
        server._posthog.close = AsyncMock()
        server._github.close = AsyncMock()
        server._search.close = AsyncMock()

        await server._cleanup()

        server._posthog.close.assert_awaited_once()
        server._github.close.assert_awaited_once()
        server._search.close.assert_awaited_once()

    async def test_cleanup_no_posthog_no_github_only_search_closed(
        self, MockPostHog, MockGitHub, MockSearch
    ):
        server = MCPServer()  # no posthog, no github
        server._search.close = AsyncMock()

        await server._cleanup()

        server._search.close.assert_awaited_once()
        # posthog and github are None, so their close should never be called
        MockPostHog.return_value.close.assert_not_called()
        MockGitHub.return_value.close.assert_not_called()


# ---------------------------------------------------------------------------
# TestToolHandlerDelegation
# ---------------------------------------------------------------------------


@patch("devrel_swarm.tools.mcp_server.SearchTools")
@patch("devrel_swarm.tools.mcp_server.GitHubTools")
@patch("devrel_swarm.tools.mcp_server.PostHogClient")
class TestToolHandlerDelegation:
    """Tests verifying handlers correctly delegate to underlying clients."""

    async def test_handle_search_docs_delegates_correctly(
        self, MockPostHog, MockGitHub, MockSearch
    ):
        @dataclass
        class FakeResult:
            title: str
            url: str
            snippet: str
            source: str
            relevance_score: float = 0.0

        server = MCPServer()
        fake_results = [FakeResult("Doc", "https://posthog.com/docs", "snippet", "posthog_docs")]
        server._search.search_devrel_ai_agents_docs = AsyncMock(return_value=fake_results)

        result = await server._handle_search_docs(query="feature flags", limit=5)

        server._search.search_devrel_ai_agents_docs.assert_awaited_once_with(
            query="feature flags", limit=5
        )
        # Result is a list of dicts (asdict conversion)
        assert isinstance(result, list)
        assert result[0]["title"] == "Doc"

    async def test_handle_fetch_issues_returns_list_of_dicts(
        self, MockPostHog, MockGitHub, MockSearch
    ):
        from devrel_swarm.tools.github_tools import GitHubIssue

        server = MCPServer(github_token="ghp_token")
        sample_issue = GitHubIssue(
            number=42,
            title="Sample bug",
            body="Some body",
            author="dev",
            state="open",
            labels=["bug"],
            created_at="2026-01-01T00:00:00Z",
            updated_at="2026-01-01T00:00:00Z",
            comments_count=0,
            reactions_total=1,
            url="https://github.com/PostHog/posthog/issues/42",
        )
        server._github.fetch_recent_issues = AsyncMock(return_value=[sample_issue])

        result = await server._handle_fetch_issues(days=7, state="open")

        server._github.fetch_recent_issues.assert_awaited_once_with(
            days=7, state="open", labels=None
        )
        assert isinstance(result, list)
        assert result[0]["number"] == 42
        assert result[0]["title"] == "Sample bug"

    async def test_handle_web_search_delegates_correctly(self, MockPostHog, MockGitHub, MockSearch):
        from devrel_swarm.tools.search_tools import SearchResult

        server = MCPServer()
        fake_result = SearchResult(
            title="Web page", url="https://web.com", snippet="A snippet", source="web"
        )
        server._search.web_search = AsyncMock(return_value=[fake_result])

        result = await server._handle_web_search(query="posthog alternatives", limit=3)

        server._search.web_search.assert_awaited_once_with(query="posthog alternatives", limit=3)
        assert result[0]["title"] == "Web page"
