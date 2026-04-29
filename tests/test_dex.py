"""Tests for the Dex documentation generator agent."""

from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from devrel_swarm.core.dex import (
    Dex,
    ParsedModule,
    ParsedSymbol,
    RepoAnalysis,
    SKIP_DIRS,
    SUPPORTED_EXTENSIONS,
)


@pytest.fixture
def dex(posthog_client, knowledge_base_path):
    return Dex(
        api_client=posthog_client,
        knowledge_base_path=knowledge_base_path,
    )


@pytest.fixture
def sample_repo(tmp_path):
    """Create a small sample repo for scanning."""
    # Python module
    pkg = tmp_path / "mylib"
    pkg.mkdir()
    (pkg / "__init__.py").write_text('"""MyLib — a sample library."""\n')
    (pkg / "core.py").write_text(
        '"""Core module with main logic."""\n\n'
        "import os\n"
        "from pathlib import Path\n\n"
        "MAX_SIZE = 1024\n\n\n"
        "class Engine:\n"
        '    """Processes data."""\n\n'
        "    def __init__(self, name: str):\n"
        "        self.name = name\n\n"
        "    def run(self, data: list[str]) -> bool:\n"
        '        """Run the engine on data."""\n'
        "        return True\n\n"
        "    async def run_async(self, data: list[str]) -> bool:\n"
        '        """Async variant."""\n'
        "        return True\n\n"
        "    def _internal(self):\n"
        "        pass\n\n\n"
        "def helper(x: int) -> str:\n"
        '    """A helper function."""\n'
        '    return str(x)\n'
    )

    # JavaScript module
    (tmp_path / "index.js").write_text(
        "/** Main entry point */\n"
        "import { Engine } from './engine';\n"
        "const http = require('http');\n\n"
        "export function startServer(port) {\n"
        "  return http.createServer();\n"
        "}\n\n"
        "export class App extends EventEmitter {\n"
        "  constructor() { super(); }\n"
        "}\n\n"
        "const handler = (req, res) => {\n"
        "  res.end('ok');\n"
        "};\n"
    )

    # TypeScript module
    (tmp_path / "utils.ts").write_text(
        "export function formatDate(d: Date): string {\n"
        "  return d.toISOString();\n"
        "}\n\n"
        "export class Logger {\n"
        "  log(msg: string): void { console.log(msg); }\n"
        "}\n"
    )

    # Non-source file (should be ignored)
    (tmp_path / "README.md").write_text("# Sample project\n")

    # __pycache__ (should be skipped)
    cache = tmp_path / "__pycache__"
    cache.mkdir()
    (cache / "core.cpython-312.pyc").write_bytes(b"\x00" * 10)

    return tmp_path


# ---------------------------------------------------------------------------
# Python parsing
# ---------------------------------------------------------------------------


class TestParsePython:
    def test_extracts_module_docstring(self, dex):
        source = '"""Module docs."""\n\nx = 1\n'
        result = dex._parse_python("test.py", source)
        assert result.docstring == "Module docs."

    def test_extracts_imports(self, dex):
        source = "import os\nfrom pathlib import Path\nfrom typing import Any, Optional\n"
        result = dex._parse_python("test.py", source)
        assert "os" in result.imports
        assert "pathlib.Path" in result.imports
        assert "typing.Any" in result.imports
        assert "typing.Optional" in result.imports

    def test_extracts_function(self, dex):
        source = 'def greet(name: str) -> str:\n    """Say hello."""\n    return f"Hi {name}"\n'
        result = dex._parse_python("test.py", source)
        funcs = [s for s in result.symbols if s.kind == "function"]
        assert len(funcs) == 1
        assert funcs[0].name == "greet"
        assert "name: str" in funcs[0].signature
        assert "-> str" in funcs[0].signature
        assert funcs[0].docstring == "Say hello."

    def test_extracts_async_function(self, dex):
        source = "async def fetch(url: str) -> bytes:\n    pass\n"
        result = dex._parse_python("test.py", source)
        funcs = [s for s in result.symbols if s.kind == "function"]
        assert len(funcs) == 1
        assert "async def" in funcs[0].signature

    def test_extracts_class_and_methods(self, dex):
        source = (
            "class Foo:\n"
            '    """A foo."""\n\n'
            "    def bar(self, x: int) -> None:\n"
            '        """Do bar."""\n'
            "        pass\n"
        )
        result = dex._parse_python("test.py", source)
        classes = [s for s in result.symbols if s.kind == "class"]
        methods = [s for s in result.symbols if s.kind == "method"]
        assert len(classes) == 1
        assert classes[0].name == "Foo"
        assert classes[0].docstring == "A foo."
        assert len(methods) == 1
        assert methods[0].name == "Foo.bar"

    def test_extracts_constants(self, dex):
        source = "MAX_SIZE = 1024\nDEBUG = True\nlocal_var = 42\n"
        result = dex._parse_python("test.py", source)
        constants = [s for s in result.symbols if s.kind == "constant"]
        names = [c.name for c in constants]
        assert "MAX_SIZE" in names
        assert "DEBUG" in names
        assert "local_var" not in names  # not ALL_CAPS

    def test_extracts_decorators(self, dex):
        source = "@staticmethod\ndef foo():\n    pass\n"
        result = dex._parse_python("test.py", source)
        funcs = [s for s in result.symbols if s.kind == "function"]
        assert funcs[0].decorators == ["staticmethod"]

    def test_handles_syntax_error(self, dex):
        source = "def broken(\n  x = \n"
        result = dex._parse_python("test.py", source)
        assert result.symbols == []
        assert result.line_count > 0

    def test_extracts_class_with_bases(self, dex):
        source = "class Foo(Bar, Baz):\n    pass\n"
        result = dex._parse_python("test.py", source)
        classes = [s for s in result.symbols if s.kind == "class"]
        assert "Bar" in classes[0].signature
        assert "Baz" in classes[0].signature

    def test_line_count(self, dex):
        source = "a = 1\nb = 2\nc = 3\n"
        result = dex._parse_python("test.py", source)
        assert result.line_count == 4  # 3 lines + trailing newline


# ---------------------------------------------------------------------------
# JavaScript / TypeScript parsing
# ---------------------------------------------------------------------------


class TestParseJsTs:
    def test_extracts_functions(self, dex):
        source = "function greet(name) {\n  return 'hi';\n}\n"
        result = dex._parse_js_ts("app.js", source, "javascript")
        funcs = [s for s in result.symbols if s.kind == "function"]
        assert len(funcs) == 1
        assert funcs[0].name == "greet"

    def test_extracts_export_function(self, dex):
        source = "export function start(port) {\n  listen(port);\n}\n"
        result = dex._parse_js_ts("app.js", source, "javascript")
        funcs = [s for s in result.symbols if s.kind == "function"]
        assert len(funcs) == 1
        assert funcs[0].name == "start"

    def test_extracts_async_function(self, dex):
        source = "export async function fetchData(url) {\n  return await fetch(url);\n}\n"
        result = dex._parse_js_ts("app.js", source, "javascript")
        funcs = [s for s in result.symbols if s.kind == "function"]
        assert len(funcs) == 1
        assert funcs[0].name == "fetchData"

    def test_extracts_classes(self, dex):
        source = "export class App extends Base {\n  run() {}\n}\n"
        result = dex._parse_js_ts("app.js", source, "javascript")
        classes = [s for s in result.symbols if s.kind == "class"]
        assert len(classes) == 1
        assert classes[0].name == "App"
        assert "extends Base" in classes[0].signature

    def test_extracts_arrow_functions(self, dex):
        source = "export const handler = (req, res) => {\n  res.end();\n};\n"
        result = dex._parse_js_ts("app.js", source, "javascript")
        funcs = [s for s in result.symbols if s.kind == "function"]
        assert len(funcs) == 1
        assert funcs[0].name == "handler"

    def test_extracts_imports(self, dex):
        source = (
            "import { Engine } from './engine';\n"
            "const http = require('http');\n"
        )
        result = dex._parse_js_ts("app.js", source, "javascript")
        assert "./engine" in result.imports
        assert "http" in result.imports

    def test_extracts_jsdoc(self, dex):
        source = (
            "/**\n"
            " * Start the server.\n"
            " * @param port - Port number.\n"
            " */\n"
            "function start(port) {}\n"
        )
        result = dex._parse_js_ts("app.js", source, "javascript")
        funcs = [s for s in result.symbols if s.kind == "function"]
        assert "Start the server" in funcs[0].docstring

    def test_typescript_language_set(self, dex):
        source = "export function foo(): void {}\n"
        result = dex._parse_js_ts("app.ts", source, "typescript")
        assert result.language == "typescript"


# ---------------------------------------------------------------------------
# Repository scanning
# ---------------------------------------------------------------------------


class TestScanRepo:
    def test_scan_finds_all_source_files(self, dex, sample_repo):
        analysis = dex.scan_repo(sample_repo)
        paths = [m.path for m in analysis.modules]
        assert any("core.py" in p for p in paths)
        assert any("__init__.py" in p for p in paths)
        assert any("index.js" in p for p in paths)
        assert any("utils.ts" in p for p in paths)

    def test_scan_skips_non_source(self, dex, sample_repo):
        analysis = dex.scan_repo(sample_repo)
        paths = [m.path for m in analysis.modules]
        assert not any("README.md" in p for p in paths)

    def test_scan_skips_pycache(self, dex, sample_repo):
        analysis = dex.scan_repo(sample_repo)
        paths = [m.path for m in analysis.modules]
        assert not any("__pycache__" in p for p in paths)

    def test_scan_counts_languages(self, dex, sample_repo):
        analysis = dex.scan_repo(sample_repo)
        assert analysis.languages["python"] == 2  # __init__.py + core.py
        assert analysis.languages["javascript"] == 1
        assert analysis.languages["typescript"] == 1

    def test_scan_totals(self, dex, sample_repo):
        analysis = dex.scan_repo(sample_repo)
        assert analysis.total_files == 4
        assert analysis.total_lines > 0

    def test_scan_empty_dir(self, dex, tmp_path):
        analysis = dex.scan_repo(tmp_path)
        assert analysis.total_files == 0
        assert analysis.modules == []


# ---------------------------------------------------------------------------
# Documentation generation
# ---------------------------------------------------------------------------


class TestGenerateArchitectureDoc:
    def test_generates_markdown(self, dex, sample_repo):
        analysis = dex.scan_repo(sample_repo)
        doc = dex.generate_architecture_doc(analysis)
        assert "# Architecture Overview" in doc
        assert "## Languages" in doc
        assert "## Module Map" in doc

    def test_includes_file_info(self, dex, sample_repo):
        analysis = dex.scan_repo(sample_repo)
        doc = dex.generate_architecture_doc(analysis)
        assert "core.py" in doc
        assert "index.js" in doc

    def test_includes_language_table(self, dex, sample_repo):
        analysis = dex.scan_repo(sample_repo)
        doc = dex.generate_architecture_doc(analysis)
        assert "python" in doc.lower()
        assert "javascript" in doc.lower()


class TestGenerateApiReference:
    def test_generates_api_doc(self, dex, sample_repo):
        analysis = dex.scan_repo(sample_repo)
        doc = dex.generate_api_reference(analysis)
        assert "# API Reference" in doc

    def test_includes_public_symbols(self, dex, sample_repo):
        analysis = dex.scan_repo(sample_repo)
        doc = dex.generate_api_reference(analysis)
        assert "Engine" in doc
        assert "helper" in doc

    def test_excludes_private_symbols(self, dex, sample_repo):
        analysis = dex.scan_repo(sample_repo)
        doc = dex.generate_api_reference(analysis)
        assert "_internal" not in doc


class TestGenerateModuleGuide:
    def test_generates_guide(self, dex):
        module = ParsedModule(
            path="mylib/core.py",
            language="python",
            imports=["os", "pathlib.Path"],
            symbols=[
                ParsedSymbol(
                    name="Engine",
                    kind="class",
                    signature="class Engine",
                    docstring="Main engine.",
                    line_number=10,
                ),
                ParsedSymbol(
                    name="Engine.run",
                    kind="method",
                    signature="def run(self, data: list) -> bool",
                    docstring="Run the engine.",
                    line_number=15,
                ),
                ParsedSymbol(
                    name="helper",
                    kind="function",
                    signature="def helper(x: int) -> str",
                    docstring="A helper.",
                    line_number=30,
                ),
            ],
            line_count=35,
            docstring="Core module.",
        )
        doc = dex.generate_module_guide(module)
        assert "# Module: `mylib/core.py`" in doc
        assert "## Overview" in doc
        assert "## Dependencies" in doc
        assert "`os`" in doc
        assert "## Classes" in doc
        assert "Engine" in doc
        assert "## Functions" in doc
        assert "helper" in doc


# ---------------------------------------------------------------------------
# Agent execute()
# ---------------------------------------------------------------------------


class TestDexExecute:
    async def test_execute_returns_expected_structure(self, dex, sample_repo):
        result = await dex.execute(
            "Generate docs for the sample repo",
            context={"repo_path": str(sample_repo)},
        )
        assert result["agent"] == "dex"
        assert result["status"] == "generated"
        assert result["total_files"] == 4
        assert result["total_lines"] > 0
        assert "architecture_doc" in result
        assert "api_reference" in result
        assert isinstance(result["modules"], list)

    async def test_execute_default_repo_path(self, dex):
        result = await dex.execute("Generate docs")
        assert result["agent"] == "dex"
        assert result["total_files"] >= 0  # whatever is in cwd

    async def test_execute_with_llm_generates_summary(
        self, posthog_client, knowledge_base_path, mock_llm_client, sample_repo
    ):
        mock_llm_client.generate = AsyncMock(
            return_value="This project is a sample library with core processing logic."
        )
        dex = Dex(
            api_client=posthog_client,
            knowledge_base_path=knowledge_base_path,
            llm_client=mock_llm_client,
        )
        result = await dex.execute(
            "Generate docs",
            context={"repo_path": str(sample_repo)},
        )
        assert "llm_summary" in result
        assert "sample library" in result["llm_summary"]

    async def test_execute_without_llm_no_summary(self, dex, sample_repo):
        result = await dex.execute(
            "Generate docs",
            context={"repo_path": str(sample_repo)},
        )
        assert "llm_summary" not in result


# ---------------------------------------------------------------------------
# Atlas integration
# ---------------------------------------------------------------------------


class TestDexAtlasIntegration:
    def test_atlas_has_dex_agent(self, posthog_client, knowledge_base_path):
        from devrel_swarm.core.atlas import Atlas

        atlas = Atlas(
            api_client=posthog_client,
            knowledge_base_path=knowledge_base_path,
        )
        assert "dex" in atlas._agents
        assert hasattr(atlas, "dex")

    def test_shared_context_has_dex_field(self):
        from devrel_swarm.core.atlas import SharedContext

        ctx = SharedContext()
        assert hasattr(ctx, "dex_docs")
        assert ctx.dex_docs == {}

    def test_shared_context_to_dict_includes_dex(self):
        from devrel_swarm.core.atlas import SharedContext

        ctx = SharedContext(dex_docs={"total_files": 10})
        d = ctx.to_dict()
        assert "dex_docs" in d
        assert d["dex_docs"]["total_files"] == 10
