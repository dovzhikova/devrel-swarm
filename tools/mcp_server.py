"""
MCP Server — Model Context Protocol server exposing agent tools.

Registers all agent tools (PostHog API, GitHub, Search) as MCP-compatible
resources so external clients (Claude Desktop, IDE plugins) can invoke them.

Uses the standard MCP JSON-RPC transport over stdio.
"""

import asyncio
import json
import logging
import sys
from dataclasses import asdict
from typing import Any, Callable, Coroutine, Optional

from tools.api_client import InsightQuery, PostHogClient
from tools.github_tools import GitHubTools
from tools.search_tools import SearchTools

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Tool Registry
# ---------------------------------------------------------------------------

ToolHandler = Callable[..., Coroutine[Any, Any, Any]]


class ToolDefinition:
    """Schema for a single MCP tool."""

    def __init__(
        self,
        name: str,
        description: str,
        input_schema: dict[str, Any],
        handler: ToolHandler,
    ):
        self.name = name
        self.description = description
        self.input_schema = input_schema
        self.handler = handler

    def to_manifest(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "inputSchema": self.input_schema,
        }


# ---------------------------------------------------------------------------
# MCP Server
# ---------------------------------------------------------------------------


class MCPServer:
    """
    Model Context Protocol server for the DevTools Advocate Agent.

    Exposes agent tools over JSON-RPC stdio transport so that Claude Desktop,
    IDE plugins, or other MCP clients can invoke them directly.

    Usage::

        server = MCPServer(
            posthog_api_key="phx_...",
            posthog_project_id="12345",
            github_token="ghp_...",
            firecrawl_api_key="fc-...",
        )
        await server.run()  # Listens on stdin/stdout
    """

    SERVER_NAME = "devrel-swarm"
    SERVER_VERSION = "1.0.0"
    PROTOCOL_VERSION = "2024-11-05"

    def __init__(
        self,
        posthog_api_key: str = "",
        posthog_project_id: str = "",
        github_token: str = "",
        firecrawl_api_key: str = "",
        brave_api_key: str = "",
    ):
        # Initialize tool clients
        self._posthog = (
            PostHogClient(api_key=posthog_api_key, project_id=posthog_project_id)
            if posthog_api_key
            else None
        )

        self._github = GitHubTools(token=github_token) if github_token else None

        self._search = SearchTools(
            firecrawl_api_key=firecrawl_api_key,
            brave_api_key=brave_api_key,
        )

        # Build tool registry
        self._tools: dict[str, ToolDefinition] = {}
        self._register_tools()

    def _register_tools(self) -> None:
        """Register all available tools with their schemas."""

        # -- PostHog Tools --------------------------------------------------
        if self._posthog:
            self._tools["posthog_query_insights"] = ToolDefinition(
                name="posthog_query_insights",
                description=(
                    "Run a PostHog insight query (trends, funnels, retention, "
                    "paths, lifecycle). Returns time-series or aggregate data."
                ),
                input_schema={
                    "type": "object",
                    "properties": {
                        "insight": {
                            "type": "string",
                            "enum": ["TRENDS", "FUNNELS", "RETENTION", "PATHS", "LIFECYCLE"],
                            "description": "Type of insight to query",
                        },
                        "events": {
                            "type": "array",
                            "items": {"type": "object"},
                            "description": "Events to include, e.g. [{'id': '$pageview'}]",
                        },
                        "date_from": {
                            "type": "string",
                            "description": "Start date, e.g. '-7d' or '2024-01-01'",
                            "default": "-7d",
                        },
                        "date_to": {
                            "type": "string",
                            "description": "End date (optional)",
                        },
                        "interval": {
                            "type": "string",
                            "enum": ["hour", "day", "week", "month"],
                            "default": "day",
                        },
                        "breakdown": {
                            "type": "string",
                            "description": "Property to break down by (optional)",
                        },
                    },
                    "required": ["insight", "events"],
                },
                handler=self._handle_posthog_query,
            )

            self._tools["posthog_list_feature_flags"] = ToolDefinition(
                name="posthog_list_feature_flags",
                description="List all feature flags in the PostHog project.",
                input_schema={
                    "type": "object",
                    "properties": {
                        "limit": {"type": "integer", "default": 100},
                    },
                },
                handler=self._handle_list_flags,
            )

            self._tools["posthog_list_experiments"] = ToolDefinition(
                name="posthog_list_experiments",
                description="List all experiments in the PostHog project.",
                input_schema={
                    "type": "object",
                    "properties": {
                        "limit": {"type": "integer", "default": 100},
                    },
                },
                handler=self._handle_list_experiments,
            )

            self._tools["posthog_get_experiment_results"] = ToolDefinition(
                name="posthog_get_experiment_results",
                description="Fetch statistical results for a PostHog experiment.",
                input_schema={
                    "type": "object",
                    "properties": {
                        "experiment_id": {"type": "integer", "description": "Experiment ID"},
                    },
                    "required": ["experiment_id"],
                },
                handler=self._handle_experiment_results,
            )

            self._tools["posthog_capture_event"] = ToolDefinition(
                name="posthog_capture_event",
                description="Capture a single analytics event in PostHog.",
                input_schema={
                    "type": "object",
                    "properties": {
                        "distinct_id": {"type": "string"},
                        "event": {"type": "string"},
                        "properties": {"type": "object"},
                    },
                    "required": ["distinct_id", "event"],
                },
                handler=self._handle_capture_event,
            )

        # -- GitHub Tools ---------------------------------------------------
        if self._github:
            self._tools["github_fetch_recent_issues"] = ToolDefinition(
                name="github_fetch_recent_issues",
                description=(
                    "Fetch recent GitHub issues from the configured repository. "
                    "Useful for community triage and trend detection."
                ),
                input_schema={
                    "type": "object",
                    "properties": {
                        "days": {"type": "integer", "default": 7},
                        "state": {
                            "type": "string",
                            "enum": ["open", "closed", "all"],
                            "default": "open",
                        },
                        "labels": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "Filter by label names",
                        },
                    },
                },
                handler=self._handle_fetch_issues,
            )

            self._tools["github_get_issue"] = ToolDefinition(
                name="github_get_issue",
                description="Fetch a single GitHub issue by number.",
                input_schema={
                    "type": "object",
                    "properties": {
                        "issue_number": {"type": "integer"},
                    },
                    "required": ["issue_number"],
                },
                handler=self._handle_get_issue,
            )

            self._tools["github_search_similar_issues"] = ToolDefinition(
                name="github_search_similar_issues",
                description=(
                    "Search for GitHub issues matching a query. "
                    "Useful for duplicate detection and pattern finding."
                ),
                input_schema={
                    "type": "object",
                    "properties": {
                        "query": {"type": "string"},
                        "limit": {"type": "integer", "default": 5},
                    },
                    "required": ["query"],
                },
                handler=self._handle_search_issues,
            )

            self._tools["github_get_contributor_profile"] = ToolDefinition(
                name="github_get_contributor_profile",
                description="Get activity summary for a GitHub contributor.",
                input_schema={
                    "type": "object",
                    "properties": {
                        "username": {"type": "string"},
                    },
                    "required": ["username"],
                },
                handler=self._handle_contributor_profile,
            )

            self._tools["github_repo_stats"] = ToolDefinition(
                name="github_repo_stats",
                description="Get repository statistics (stars, forks, open issues).",
                input_schema={"type": "object", "properties": {}},
                handler=self._handle_repo_stats,
            )

        # -- Search Tools ---------------------------------------------------
        self._tools["search_devrel_ai_agents_docs"] = ToolDefinition(
            name="search_devrel_ai_agents_docs",
            description=(
                "Search OpenClaw documentation for a topic. "
                "Returns relevant doc pages with snippets."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "limit": {"type": "integer", "default": 10},
                },
                "required": ["query"],
            },
            handler=self._handle_search_docs,
        )

        self._tools["search_web"] = ToolDefinition(
            name="search_web",
            description=(
                "General web search via Firecrawl API. "
                "Useful for competitive analysis and trend research."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "limit": {"type": "integer", "default": 10},
                },
                "required": ["query"],
            },
            handler=self._handle_web_search,
        )

        self._tools["search_discourse"] = ToolDefinition(
            name="search_discourse",
            description="Search OpenClaw community forum.",
            input_schema={
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "limit": {"type": "integer", "default": 10},
                },
                "required": ["query"],
            },
            handler=self._handle_search_discourse,
        )

        self._tools["fetch_url_content"] = ToolDefinition(
            name="fetch_url_content",
            description="Fetch and extract text content from a URL.",
            input_schema={
                "type": "object",
                "properties": {
                    "url": {"type": "string"},
                    "max_chars": {"type": "integer", "default": 10000},
                },
                "required": ["url"],
            },
            handler=self._handle_fetch_url,
        )

        logger.info(f"Registered {len(self._tools)} MCP tools")

    # -- Tool Handlers -------------------------------------------------------

    async def _handle_posthog_query(self, **kwargs: Any) -> Any:
        query = InsightQuery(
            insight=kwargs.get("insight", "TRENDS"),
            events=kwargs.get("events", []),
            date_from=kwargs.get("date_from", "-7d"),
            date_to=kwargs.get("date_to"),
            interval=kwargs.get("interval", "day"),
            breakdown=kwargs.get("breakdown"),
        )
        return await self._posthog.query_insights(query)

    async def _handle_list_flags(self, **kwargs: Any) -> Any:
        return await self._posthog.list_feature_flags(limit=kwargs.get("limit", 100))

    async def _handle_list_experiments(self, **kwargs: Any) -> Any:
        return await self._posthog.list_experiments(limit=kwargs.get("limit", 100))

    async def _handle_experiment_results(self, **kwargs: Any) -> Any:
        return await self._posthog.get_experiment_results(kwargs["experiment_id"])

    async def _handle_capture_event(self, **kwargs: Any) -> Any:
        return await self._posthog.capture(
            distinct_id=kwargs["distinct_id"],
            event=kwargs["event"],
            properties=kwargs.get("properties", {}),
        )

    async def _handle_fetch_issues(self, **kwargs: Any) -> Any:
        issues = await self._github.fetch_recent_issues(
            days=kwargs.get("days", 7),
            state=kwargs.get("state", "open"),
            labels=kwargs.get("labels"),
        )
        return [asdict(i) for i in issues]

    async def _handle_get_issue(self, **kwargs: Any) -> Any:
        issue = await self._github.get_issue(kwargs["issue_number"])
        return asdict(issue)

    async def _handle_search_issues(self, **kwargs: Any) -> Any:
        issues = await self._github.search_similar_issues(
            query=kwargs["query"],
            limit=kwargs.get("limit", 5),
        )
        return [asdict(i) for i in issues]

    async def _handle_contributor_profile(self, **kwargs: Any) -> Any:
        profile = await self._github.get_contributor_profile(kwargs["username"])
        return asdict(profile)

    async def _handle_repo_stats(self, **kwargs: Any) -> Any:
        return await self._github.get_repo_stats()

    async def _handle_search_docs(self, **kwargs: Any) -> Any:
        results = await self._search.search_devrel_ai_agents_docs(
            query=kwargs["query"],
            limit=kwargs.get("limit", 10),
        )
        return [asdict(r) for r in results]

    async def _handle_web_search(self, **kwargs: Any) -> Any:
        results = await self._search.web_search(
            query=kwargs["query"],
            limit=kwargs.get("limit", 10),
        )
        return [asdict(r) for r in results]

    async def _handle_search_discourse(self, **kwargs: Any) -> Any:
        results = await self._search.search_discourse(
            query=kwargs["query"],
            limit=kwargs.get("limit", 10),
        )
        return [asdict(r) for r in results]

    async def _handle_fetch_url(self, **kwargs: Any) -> Any:
        content = await self._search.fetch_url_content(
            url=kwargs["url"],
            max_chars=kwargs.get("max_chars", 10000),
        )
        return {"content": content, "url": kwargs["url"]}

    # -- JSON-RPC Transport --------------------------------------------------

    async def run(self) -> None:
        """Run the MCP server on stdio (JSON-RPC over stdin/stdout)."""
        logger.info(f"Starting MCP server: {self.SERVER_NAME} v{self.SERVER_VERSION}")

        reader = asyncio.StreamReader()
        protocol = asyncio.StreamReaderProtocol(reader)
        await asyncio.get_event_loop().connect_read_pipe(lambda: protocol, sys.stdin.buffer)

        writer_transport, writer_protocol = await asyncio.get_event_loop().connect_write_pipe(
            asyncio.streams.FlowControlMixin, sys.stdout.buffer
        )
        writer = asyncio.StreamWriter(
            writer_transport, writer_protocol, None, asyncio.get_event_loop()
        )

        try:
            while True:
                line = await reader.readline()
                if not line:
                    break

                try:
                    request = json.loads(line.decode())
                    response = await self._handle_request(request)
                    if response is not None:
                        writer.write((json.dumps(response) + "\n").encode())
                        await writer.drain()
                except json.JSONDecodeError:
                    logger.warning("Invalid JSON received")
                except Exception as exc:
                    logger.error(f"Error handling request: {exc}")
        finally:
            await self._cleanup()

    async def _handle_request(self, request: dict[str, Any]) -> Optional[dict[str, Any]]:
        """Route a JSON-RPC request to the appropriate handler."""
        method = request.get("method", "")
        req_id = request.get("id")
        params = request.get("params", {})

        if method == "initialize":
            return self._rpc_response(
                req_id,
                {
                    "protocolVersion": self.PROTOCOL_VERSION,
                    "capabilities": {"tools": {"listChanged": False}},
                    "serverInfo": {
                        "name": self.SERVER_NAME,
                        "version": self.SERVER_VERSION,
                    },
                },
            )

        elif method == "tools/list":
            tools = [t.to_manifest() for t in self._tools.values()]
            return self._rpc_response(req_id, {"tools": tools})

        elif method == "tools/call":
            tool_name = params.get("name", "")
            arguments = params.get("arguments", {})

            if tool_name not in self._tools:
                return self._rpc_error(req_id, -32602, f"Unknown tool: {tool_name}")

            try:
                result = await self._tools[tool_name].handler(**arguments)
                return self._rpc_response(
                    req_id, {"content": [{"type": "text", "text": json.dumps(result, default=str)}]}
                )
            except Exception as exc:
                return self._rpc_response(
                    req_id,
                    {
                        "content": [{"type": "text", "text": f"Error: {exc}"}],
                        "isError": True,
                    },
                )

        elif method == "notifications/initialized":
            return None  # Notification, no response needed

        else:
            return self._rpc_error(req_id, -32601, f"Unknown method: {method}")

    @staticmethod
    def _rpc_response(req_id: Any, result: dict[str, Any]) -> dict[str, Any]:
        return {"jsonrpc": "2.0", "id": req_id, "result": result}

    @staticmethod
    def _rpc_error(req_id: Any, code: int, message: str) -> dict[str, Any]:
        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "error": {"code": code, "message": message},
        }

    async def _cleanup(self) -> None:
        """Close all underlying HTTP clients."""
        if self._posthog:
            await self._posthog.close()
        if self._github:
            await self._github.close()
        await self._search.close()


# ---------------------------------------------------------------------------
# CLI Entry Point
# ---------------------------------------------------------------------------


def main() -> None:
    """Launch the MCP server from the command line."""
    import argparse
    import os

    from dotenv import load_dotenv

    load_dotenv()

    parser = argparse.ArgumentParser(description="DevTools Advocate Agent MCP Server")
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
        stream=sys.stderr,  # Keep logs on stderr, JSON-RPC on stdout
    )

    server = MCPServer(
        posthog_api_key=os.environ.get("POSTHOG_API_KEY", ""),
        posthog_project_id=os.environ.get("POSTHOG_PROJECT_ID", ""),
        github_token=os.environ.get("GITHUB_TOKEN", ""),
        firecrawl_api_key=os.environ.get("FIRECRAWL_API_KEY", ""),
        brave_api_key=os.environ.get("BRAVE_API_KEY", ""),
    )

    asyncio.run(server.run())


if __name__ == "__main__":
    main()
