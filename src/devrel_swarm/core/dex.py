"""
Dex — Documentation Generator Agent

Reads source code from repositories and generates technical documentation:
architecture overviews, API references, module guides, and README content.
Uses AST parsing for Python and heuristic analysis for other languages.
"""

import ast
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from devrel_swarm.core.llm import LLMClient
from devrel_swarm.tools.api_client import PostHogClient

logger = logging.getLogger(__name__)

# File extensions Dex knows how to analyse
SUPPORTED_EXTENSIONS = {
    ".py": "python",
    ".js": "javascript",
    ".ts": "typescript",
    ".jsx": "javascript",
    ".tsx": "typescript",
}

# Directories to always skip
SKIP_DIRS = {
    "__pycache__",
    ".git",
    "node_modules",
    ".venv",
    "venv",
    "dist",
    "build",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".tox",
    "egg-info",
}

# Max file size to analyse (256 KB)
MAX_FILE_SIZE = 256 * 1024


@dataclass
class ParsedSymbol:
    """A single extracted symbol (class, function, variable)."""

    name: str
    kind: str  # 'class', 'function', 'method', 'constant'
    signature: str  # e.g. "def foo(x: int, y: str) -> bool"
    docstring: str
    line_number: int
    decorators: list[str] = field(default_factory=list)


@dataclass
class ParsedModule:
    """Analysis of a single source file."""

    path: str  # relative to repo root
    language: str
    imports: list[str]
    symbols: list[ParsedSymbol]
    line_count: int
    docstring: str  # module-level docstring


@dataclass
class RepoAnalysis:
    """Full analysis of a repository."""

    root: str
    modules: list[ParsedModule]
    total_files: int
    total_lines: int
    languages: dict[str, int]  # language → file count


class Dex:
    """
    Documentation Generator agent that reads source code and produces
    technical documentation.

    Capabilities:
    - Scan repository file trees and identify source modules
    - Parse Python files via AST for classes, functions, signatures, docstrings
    - Parse JavaScript/TypeScript files via heuristics for exports and functions
    - Generate architecture overviews, API references, and module guides
    - Optionally use an LLM to produce natural-language summaries
    """

    SYSTEM_PROMPT = """You are Dex, a technical documentation generator for developer tools.
Your role is to produce clear, accurate documentation from source code analysis.

Guidelines:
1. ACCURACY FIRST — Every function signature, parameter type, and return type must
   match the source code exactly. Never invent APIs that don't exist.
2. STRUCTURE — Use consistent heading hierarchy: H1 for the project, H2 for modules,
   H3 for classes/functions. Include a table of contents for documents > 500 words.
3. DEVELOPER AUDIENCE — Write for engineers who will use this code. Lead with what
   it does and how to use it, then cover internals.
4. CODE EXAMPLES — Include usage examples for public APIs. Show import paths.
5. CROSS-REFERENCES — Link related modules and classes to each other.

Output formats:
- Architecture overview: high-level module map, data flow, key patterns
- API reference: every public class/function with signature, params, return type, example
- Module guide: purpose, dependencies, key abstractions, usage patterns
- README: quick start, installation, project structure, contributing"""

    def __init__(
        self,
        api_client: PostHogClient,
        knowledge_base_path: Path,
        llm_client: Optional[LLMClient] = None,
    ):
        self.api_client = api_client
        self.knowledge_base_path = knowledge_base_path
        self.llm_client = llm_client

    # ------------------------------------------------------------------
    # Repository scanning
    # ------------------------------------------------------------------

    def scan_repo(self, repo_path: Path) -> RepoAnalysis:
        """Scan a repository and parse all supported source files."""
        repo_path = Path(repo_path)
        modules: list[ParsedModule] = []
        languages: dict[str, int] = {}

        for filepath in sorted(repo_path.rglob("*")):
            if not filepath.is_file():
                continue
            if any(skip in filepath.parts for skip in SKIP_DIRS):
                continue
            if filepath.stat().st_size > MAX_FILE_SIZE:
                continue

            ext = filepath.suffix
            if ext not in SUPPORTED_EXTENSIONS:
                continue

            language = SUPPORTED_EXTENSIONS[ext]
            languages[language] = languages.get(language, 0) + 1

            try:
                source = filepath.read_text(encoding="utf-8", errors="replace")
            except (OSError, UnicodeDecodeError):
                continue

            rel_path = str(filepath.relative_to(repo_path))

            if language == "python":
                module = self._parse_python(rel_path, source)
            else:
                module = self._parse_js_ts(rel_path, source, language)

            modules.append(module)

        total_lines = sum(m.line_count for m in modules)

        return RepoAnalysis(
            root=str(repo_path),
            modules=modules,
            total_files=len(modules),
            total_lines=total_lines,
            languages=languages,
        )

    # ------------------------------------------------------------------
    # Python parser (AST-based)
    # ------------------------------------------------------------------

    def _parse_python(self, rel_path: str, source: str) -> ParsedModule:
        """Parse a Python file using the ast module."""
        line_count = source.count("\n") + 1
        imports: list[str] = []
        symbols: list[ParsedSymbol] = []
        module_doc = ""

        try:
            tree = ast.parse(source)
        except SyntaxError:
            return ParsedModule(
                path=rel_path,
                language="python",
                imports=[],
                symbols=[],
                line_count=line_count,
                docstring="",
            )

        module_doc = ast.get_docstring(tree) or ""

        for node in ast.iter_child_nodes(tree):
            # Imports
            if isinstance(node, ast.Import):
                for alias in node.names:
                    imports.append(alias.name)
            elif isinstance(node, ast.ImportFrom):
                module_name = node.module or ""
                for alias in node.names:
                    imports.append(f"{module_name}.{alias.name}")

            # Functions
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                symbols.append(self._parse_python_func(node))

            # Classes
            elif isinstance(node, ast.ClassDef):
                class_doc = ast.get_docstring(node) or ""
                decorators = [self._decorator_name(d) for d in node.decorator_list]
                bases = [self._node_name(b) for b in node.bases]
                sig = f"class {node.name}"
                if bases:
                    sig += f"({', '.join(bases)})"

                symbols.append(
                    ParsedSymbol(
                        name=node.name,
                        kind="class",
                        signature=sig,
                        docstring=class_doc,
                        line_number=node.lineno,
                        decorators=decorators,
                    )
                )

                # Methods inside class — use ast.walk to capture nested
                # classes and decorated/conditionally-defined methods.
                for item in ast.walk(node):
                    if item is node:
                        continue
                    if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        method = self._parse_python_func(item, class_name=node.name)
                        symbols.append(method)

            # Module-level constants (ALL_CAPS assignments)
            elif isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Name) and target.id.isupper():
                        symbols.append(
                            ParsedSymbol(
                                name=target.id,
                                kind="constant",
                                signature=f"{target.id} = ...",
                                docstring="",
                                line_number=node.lineno,
                            )
                        )

            # Annotated module-level constants (e.g. `MAX_RETRIES: int = 5`)
            # — `ast.AnnAssign` has a single `target` (not `targets`), and we
            # only capture ALL_CAPS names so lowercase typed module vars
            # don't pollute the parsed symbol list.
            elif isinstance(node, ast.AnnAssign):
                if isinstance(node.target, ast.Name) and node.target.id.isupper():
                    symbols.append(
                        ParsedSymbol(
                            name=node.target.id,
                            kind="constant",
                            signature=f"{node.target.id} = ...",
                            docstring="",
                            line_number=node.lineno,
                        )
                    )

        return ParsedModule(
            path=rel_path,
            language="python",
            imports=imports,
            symbols=symbols,
            line_count=line_count,
            docstring=module_doc,
        )

    def _parse_python_func(
        self, node: ast.FunctionDef | ast.AsyncFunctionDef, class_name: str = ""
    ) -> ParsedSymbol:
        """Extract a ParsedSymbol from a function/method AST node."""
        decorators = [self._decorator_name(d) for d in node.decorator_list]
        params = self._format_params(node.args)
        returns = ""
        if node.returns:
            returns = f" -> {self._node_name(node.returns)}"

        prefix = "async def" if isinstance(node, ast.AsyncFunctionDef) else "def"
        sig = f"{prefix} {node.name}({params}){returns}"
        doc = ast.get_docstring(node) or ""

        kind = "method" if class_name else "function"
        name = f"{class_name}.{node.name}" if class_name else node.name

        return ParsedSymbol(
            name=name,
            kind=kind,
            signature=sig,
            docstring=doc,
            line_number=node.lineno,
            decorators=decorators,
        )

    @staticmethod
    def _format_params(args: ast.arguments) -> str:
        """Format function parameters into a readable signature string."""
        parts: list[str] = []
        defaults_offset = len(args.args) - len(args.defaults)

        for i, arg in enumerate(args.args):
            param = arg.arg
            if arg.annotation:
                param += f": {Dex._node_name(arg.annotation)}"
            default_idx = i - defaults_offset
            if default_idx >= 0 and default_idx < len(args.defaults):
                param += " = ..."
            parts.append(param)

        if args.vararg:
            va = f"*{args.vararg.arg}"
            if args.vararg.annotation:
                va += f": {Dex._node_name(args.vararg.annotation)}"
            parts.append(va)

        for i, arg in enumerate(args.kwonlyargs):
            param = arg.arg
            if arg.annotation:
                param += f": {Dex._node_name(arg.annotation)}"
            if i < len(args.kw_defaults) and args.kw_defaults[i] is not None:
                param += " = ..."
            parts.append(param)

        if args.kwarg:
            kw = f"**{args.kwarg.arg}"
            if args.kwarg.annotation:
                kw += f": {Dex._node_name(args.kwarg.annotation)}"
            parts.append(kw)

        return ", ".join(parts)

    @staticmethod
    def _node_name(node: ast.expr) -> str:
        """Best-effort extraction of a name from an AST expression."""
        if isinstance(node, ast.Name):
            return node.id
        elif isinstance(node, ast.Attribute):
            return f"{Dex._node_name(node.value)}.{node.attr}"
        elif isinstance(node, ast.Constant):
            return repr(node.value)
        elif isinstance(node, ast.Subscript):
            return f"{Dex._node_name(node.value)}[{Dex._node_name(node.slice)}]"
        elif isinstance(node, ast.Tuple):
            return ", ".join(Dex._node_name(e) for e in node.elts)
        elif isinstance(node, ast.BinOp) and isinstance(node.op, ast.BitOr):
            return f"{Dex._node_name(node.left)} | {Dex._node_name(node.right)}"
        return "..."

    @staticmethod
    def _decorator_name(node: ast.expr) -> str:
        """Extract decorator name."""
        if isinstance(node, ast.Name):
            return node.id
        elif isinstance(node, ast.Attribute):
            return f"{Dex._node_name(node.value)}.{node.attr}"
        elif isinstance(node, ast.Call):
            return Dex._decorator_name(node.func)
        return "..."

    # ------------------------------------------------------------------
    # JavaScript / TypeScript parser (heuristic)
    # ------------------------------------------------------------------

    # Regex patterns for JS/TS symbol extraction
    _JS_FUNC_RE = re.compile(
        r"^(?:export\s+)?(?:async\s+)?function\s+(\w+)\s*\(([^)]*)\)",
        re.MULTILINE,
    )
    _JS_CLASS_RE = re.compile(
        r"^(?:export\s+)?class\s+(\w+)(?:\s+extends\s+(\w+))?",
        re.MULTILINE,
    )
    _JS_CONST_FUNC_RE = re.compile(
        r"^(?:export\s+)?(?:const|let|var)\s+(\w+)\s*=\s*(?:async\s+)?\([^)]*\)\s*=>",
        re.MULTILINE,
    )
    _JS_EXPORT_RE = re.compile(
        r"^export\s+(?:default\s+)?(?:const|let|var|function|class)\s+(\w+)",
        re.MULTILINE,
    )

    def _parse_js_ts(self, rel_path: str, source: str, language: str) -> ParsedModule:
        """Heuristic parser for JavaScript/TypeScript files."""
        line_count = source.count("\n") + 1
        symbols: list[ParsedSymbol] = []
        imports: list[str] = []

        # Extract imports
        for match in re.finditer(
            r"(?:import\s+.*?from\s+['\"]([^'\"]+)['\"]|"
            r"(?:const|let|var)\s+.*?=\s*require\(['\"]([^'\"]+)['\"]\))",
            source,
        ):
            imp = match.group(1) or match.group(2)
            if imp:
                imports.append(imp)

        # Extract functions
        for match in self._JS_FUNC_RE.finditer(source):
            name = match.group(1)
            params = match.group(2).strip()
            line = source[: match.start()].count("\n") + 1
            symbols.append(
                ParsedSymbol(
                    name=name,
                    kind="function",
                    signature=f"function {name}({params})",
                    docstring=self._extract_jsdoc(source, match.start()),
                    line_number=line,
                )
            )

        # Extract classes
        for match in self._JS_CLASS_RE.finditer(source):
            name = match.group(1)
            extends = match.group(2)
            line = source[: match.start()].count("\n") + 1
            sig = f"class {name}"
            if extends:
                sig += f" extends {extends}"
            symbols.append(
                ParsedSymbol(
                    name=name,
                    kind="class",
                    signature=sig,
                    docstring=self._extract_jsdoc(source, match.start()),
                    line_number=line,
                )
            )

        # Extract arrow function exports
        for match in self._JS_CONST_FUNC_RE.finditer(source):
            name = match.group(1)
            line = source[: match.start()].count("\n") + 1
            symbols.append(
                ParsedSymbol(
                    name=name,
                    kind="function",
                    signature=f"const {name} = (...) => ...",
                    docstring=self._extract_jsdoc(source, match.start()),
                    line_number=line,
                )
            )

        # Module docstring: first block comment
        first_comment = re.match(r"\s*/\*\*(.*?)\*/", source, re.DOTALL)
        module_doc = first_comment.group(1).strip() if first_comment else ""

        return ParsedModule(
            path=rel_path,
            language=language,
            imports=imports,
            symbols=symbols,
            line_count=line_count,
            docstring=module_doc,
        )

    @staticmethod
    def _extract_jsdoc(source: str, pos: int) -> str:
        """Extract JSDoc comment immediately preceding position."""
        before = source[:pos].rstrip()
        match = re.search(r"/\*\*(.*?)\*/\s*$", before, re.DOTALL)
        if match:
            raw = match.group(1)
            lines = [re.sub(r"^\s*\*\s?", "", line) for line in raw.splitlines()]
            return "\n".join(line for line in lines if line.strip()).strip()
        return ""

    # ------------------------------------------------------------------
    # Documentation generation
    # ------------------------------------------------------------------

    def generate_architecture_doc(self, analysis: RepoAnalysis) -> str:
        """Generate a markdown architecture overview from repo analysis."""
        lines: list[str] = []
        lines.append("# Architecture Overview\n")
        lines.append(f"**Root:** `{analysis.root}`\n")
        lines.append(f"**Files:** {analysis.total_files} | **Lines:** {analysis.total_lines}\n")

        # Language breakdown
        if analysis.languages:
            lines.append("## Languages\n")
            lines.append("| Language | Files |")
            lines.append("|----------|-------|")
            for lang, count in sorted(analysis.languages.items(), key=lambda x: -x[1]):
                lines.append(f"| {lang} | {count} |")
            lines.append("")

        # Module map
        lines.append("## Module Map\n")
        dirs: dict[str, list[ParsedModule]] = {}
        for mod in analysis.modules:
            parts = mod.path.split("/")
            dir_name = "/".join(parts[:-1]) if len(parts) > 1 else "."
            dirs.setdefault(dir_name, []).append(mod)

        for dir_name in sorted(dirs):
            lines.append(f"### `{dir_name}/`\n")
            for mod in sorted(dirs[dir_name], key=lambda m: m.path):
                filename = mod.path.split("/")[-1]
                summary = mod.docstring.split("\n")[0] if mod.docstring else ""
                classes = [s for s in mod.symbols if s.kind == "class"]
                funcs = [s for s in mod.symbols if s.kind == "function"]
                line = f"- **`{filename}`** ({mod.line_count} lines)"
                if summary:
                    line += f" — {summary}"
                if classes:
                    line += f" | Classes: {', '.join(c.name for c in classes)}"
                if funcs:
                    line += f" | Functions: {', '.join(f.name for f in funcs)}"
                lines.append(line)
            lines.append("")

        return "\n".join(lines)

    def generate_api_reference(self, analysis: RepoAnalysis) -> str:
        """Generate a markdown API reference from repo analysis."""
        lines: list[str] = []
        lines.append("# API Reference\n")

        for mod in sorted(analysis.modules, key=lambda m: m.path):
            public_symbols = [s for s in mod.symbols if not s.name.split(".")[-1].startswith("_")]
            if not public_symbols:
                continue

            lines.append(f"## `{mod.path}`\n")
            if mod.docstring:
                lines.append(f"{mod.docstring.split(chr(10))[0]}\n")

            for sym in public_symbols:
                if sym.kind == "constant":
                    lines.append(f"### `{sym.name}`\n")
                    lines.append(f"```{mod.language}\n{sym.signature}\n```\n")
                elif sym.kind == "class":
                    lines.append(f"### `{sym.name}`\n")
                    lines.append(f"```{mod.language}\n{sym.signature}\n```\n")
                    if sym.docstring:
                        lines.append(f"{sym.docstring}\n")
                elif sym.kind in ("function", "method"):
                    lines.append(f"#### `{sym.name}()`\n")
                    lines.append(f"```{mod.language}\n{sym.signature}\n```\n")
                    if sym.docstring:
                        lines.append(f"{sym.docstring}\n")
                    if sym.decorators:
                        lines.append(
                            f"Decorators: {', '.join(f'`@{d}`' for d in sym.decorators)}\n"
                        )

        return "\n".join(lines)

    def generate_module_guide(self, module: ParsedModule) -> str:
        """Generate a detailed guide for a single module."""
        lines: list[str] = []
        lines.append(f"# Module: `{module.path}`\n")
        lines.append(f"**Language:** {module.language} | **Lines:** {module.line_count}\n")

        if module.docstring:
            lines.append(f"## Overview\n\n{module.docstring}\n")

        if module.imports:
            lines.append("## Dependencies\n")
            for imp in module.imports:
                lines.append(f"- `{imp}`")
            lines.append("")

        classes = [s for s in module.symbols if s.kind == "class"]
        functions = [s for s in module.symbols if s.kind == "function"]
        constants = [s for s in module.symbols if s.kind == "constant"]

        if constants:
            lines.append("## Constants\n")
            for c in constants:
                lines.append(f"- `{c.signature}`")
            lines.append("")

        if classes:
            lines.append("## Classes\n")
            for cls in classes:
                lines.append(f"### `{cls.signature}`\n")
                if cls.docstring:
                    lines.append(f"{cls.docstring}\n")
                methods = [
                    s
                    for s in module.symbols
                    if s.kind == "method" and s.name.startswith(f"{cls.name}.")
                ]
                if methods:
                    lines.append("**Methods:**\n")
                    for m in methods:
                        short_name = m.name.split(".")[-1]
                        if short_name.startswith("_") and short_name != "__init__":
                            continue
                        lines.append(f"- `{m.signature}`")
                        if m.docstring:
                            first_line = m.docstring.split("\n")[0]
                            lines.append(f"  {first_line}")
                    lines.append("")

        if functions:
            lines.append("## Functions\n")
            for fn in functions:
                lines.append(f"### `{fn.signature}`\n")
                if fn.docstring:
                    lines.append(f"{fn.docstring}\n")

        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Agent interface (matches other agents)
    # ------------------------------------------------------------------

    async def execute(
        self,
        task: str,
        context: Optional[dict[str, Any]] = None,
    ) -> dict[str, Any]:
        """
        Execute a documentation generation task.

        Scans the target repo (defaults to this project's root), generates
        docs, and optionally uses an LLM for natural-language summaries.
        """
        logger.info(f"Dex executing: {task[:80]}...")

        # Determine repo path: from context, or default to .devrel project
        # root, falling back to cwd if no .devrel/config.toml is reachable.
        if context and "repo_path" in context:
            repo_path = Path(context["repo_path"])
        else:
            try:
                from devrel_swarm.project.paths import (
                    ProjectNotFoundError,
                    find_devrel_root,
                )

                try:
                    repo_path = find_devrel_root()
                except ProjectNotFoundError:
                    repo_path = Path(".")
            except Exception:
                repo_path = Path(".")

        # Scan and analyse
        analysis = self.scan_repo(repo_path)

        # Generate documentation artifacts
        architecture = self.generate_architecture_doc(analysis)
        api_reference = self.generate_api_reference(analysis)

        base_result: dict[str, Any] = {
            "agent": "dex",
            "task": task,
            "repo": str(repo_path),
            "total_files": analysis.total_files,
            "total_lines": analysis.total_lines,
            "languages": analysis.languages,
            "modules": [
                {
                    "path": m.path,
                    "language": m.language,
                    "line_count": m.line_count,
                    "symbols": len(m.symbols),
                    "docstring": m.docstring[:200] if m.docstring else "",
                }
                for m in analysis.modules
            ],
            "architecture_doc": architecture,
            "api_reference": api_reference,
            "status": "generated",
        }

        # Optionally use LLM for a high-level summary
        if self.llm_client:
            try:
                summary_prompt = (
                    f"Task: {task}\n\n"
                    f"Below is the architecture overview of a codebase. "
                    f"Write a concise technical summary (3-5 paragraphs) covering:\n"
                    f"- What this project does\n"
                    f"- Key architectural patterns\n"
                    f"- Main entry points and public APIs\n"
                    f"- Notable dependencies\n\n"
                    f"Architecture:\n{architecture[:4000]}"
                )
                summary = await self.llm_client.generate(
                    system_prompt=self.SYSTEM_PROMPT,
                    user_prompt=summary_prompt,
                    temperature=0.3,
                )
                base_result["llm_summary"] = summary
            except Exception as exc:
                logger.warning(f"LLM summary generation failed: {exc}")

        return base_result
