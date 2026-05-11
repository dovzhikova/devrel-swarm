"""
Code Validator — Syntax validation for code snippets in generated content.

Extracts fenced code blocks from markdown and validates syntax per language.
Supports: Python (ast.parse), JavaScript (esprima/basic checks), JSON (json.loads),
HTML (basic tag balance), and reports on unsupported languages without failing.
"""

import ast
import json
import logging
import re
import shlex
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

CODE_BLOCK_RE = re.compile(r"```(\w*)\n(.*?)```", re.DOTALL)

# Languages where we can validate syntax or safety heuristics
SUPPORTED_LANGUAGES = {
    "python",
    "py",
    "javascript",
    "js",
    "json",
    "html",
    "sql",
    "yaml",
    "yml",
    "bash",
    "sh",
    "shell",
    "zsh",
}

# Languages we skip validation for.
SKIP_LANGUAGES = {"toml", "nginx", "css", "text", ""}


@dataclass
class CodeBlock:
    """A single extracted code block."""

    language: str
    code: str
    line_number: int  # approximate line in the source markdown


@dataclass
class ValidationResult:
    """Result of validating a single code block."""

    block: CodeBlock
    is_valid: bool
    error: str = ""


@dataclass
class ValidationReport:
    """Aggregated validation results for a piece of content."""

    total_blocks: int = 0
    validated: int = 0
    passed: int = 0
    failed: int = 0
    skipped: int = 0
    errors: list[ValidationResult] = field(default_factory=list)

    @property
    def all_passed(self) -> bool:
        return self.failed == 0


class CodeValidator:
    """
    Validates code blocks extracted from markdown content.

    Performs syntax-level validation:
    - Python: ast.parse() for syntax correctness
    - JavaScript: checks balanced braces/brackets/parens and basic syntax
    - JSON: json.loads() for valid JSON
    - HTML: checks balanced tags

    Does NOT execute code or check runtime correctness.
    """

    def extract_code_blocks(self, markdown: str) -> list[CodeBlock]:
        """Extract all fenced code blocks from markdown content."""
        blocks = []
        for match in CODE_BLOCK_RE.finditer(markdown):
            lang = match.group(1).lower().strip()
            code = match.group(2).strip()
            # Approximate line number
            line_num = markdown[: match.start()].count("\n") + 1
            if code:
                blocks.append(CodeBlock(language=lang, code=code, line_number=line_num))
        return blocks

    def validate_block(self, block: CodeBlock) -> ValidationResult:
        """Validate a single code block based on its language."""
        lang = block.language

        if lang in SKIP_LANGUAGES:
            return ValidationResult(block=block, is_valid=True)

        if lang in ("python", "py"):
            return self._validate_python(block)
        elif lang in ("javascript", "js"):
            return self._validate_javascript(block)
        elif lang == "json":
            return self._validate_json(block)
        elif lang == "html":
            return self._validate_html(block)
        elif lang == "sql":
            return self._validate_sql(block)
        elif lang in ("yaml", "yml"):
            return self._validate_yaml(block)
        elif lang in ("bash", "sh", "shell", "zsh"):
            return self._validate_shell(block)
        else:
            # Unknown language — skip, don't fail
            return ValidationResult(block=block, is_valid=True)

    def validate_content(self, markdown: str) -> ValidationReport:
        """Validate all code blocks in a markdown document."""
        blocks = self.extract_code_blocks(markdown)
        report = ValidationReport(total_blocks=len(blocks))

        for block in blocks:
            if block.language in SKIP_LANGUAGES or block.language not in SUPPORTED_LANGUAGES:
                report.skipped += 1
                continue

            result = self.validate_block(block)
            report.validated += 1

            if result.is_valid:
                report.passed += 1
            else:
                report.failed += 1
                report.errors.append(result)
                logger.warning(
                    f"Code validation failed (line ~{block.line_number}, "
                    f"{block.language}): {result.error}"
                )

        return report

    def _validate_python(self, block: CodeBlock) -> ValidationResult:
        """Validate Python syntax using ast.parse()."""
        code = block.code

        # Strip common non-parseable patterns that are valid in tutorials
        # e.g., lines with "..." for ellipsis/placeholder
        lines = code.splitlines()
        cleaned_lines = []
        for line in lines:
            stripped = line.strip()
            # Skip pure ellipsis lines (common in tutorial snippets)
            if stripped == "...":
                cleaned_lines.append(line.replace("...", "pass"))
            # Skip lines that are just comments
            elif stripped.startswith("#"):
                cleaned_lines.append(line)
            else:
                cleaned_lines.append(line)
        cleaned = "\n".join(cleaned_lines)

        try:
            ast.parse(cleaned)
            return ValidationResult(block=block, is_valid=True)
        except SyntaxError as e:
            return ValidationResult(
                block=block,
                is_valid=False,
                error=f"Python syntax error at line {e.lineno}: {e.msg}",
            )

    def _validate_javascript(self, block: CodeBlock) -> ValidationResult:
        """Basic JavaScript syntax validation — checks balanced delimiters and common errors."""
        code = block.code

        # Strip string literals and comments to avoid false positives
        # Remove single-line comments
        stripped = re.sub(r"//.*$", "", code, flags=re.MULTILINE)
        # Remove multi-line comments
        stripped = re.sub(r"/\*.*?\*/", "", stripped, flags=re.DOTALL)
        # Remove template literals
        stripped = re.sub(r"`[^`]*`", '""', stripped)
        # Remove double-quoted strings
        stripped = re.sub(r'"(?:[^"\\]|\\.)*"', '""', stripped)
        # Remove single-quoted strings
        stripped = re.sub(r"'(?:[^'\\]|\\.)*'", "''", stripped)

        # Check balanced delimiters
        stack = []
        pairs = {")": "(", "]": "[", "}": "{"}
        for i, ch in enumerate(stripped):
            if ch in "({[":
                stack.append(ch)
            elif ch in ")}]":
                if not stack:
                    return ValidationResult(
                        block=block,
                        is_valid=False,
                        error=f"Unmatched closing '{ch}' at position {i}",
                    )
                if stack[-1] != pairs[ch]:
                    return ValidationResult(
                        block=block,
                        is_valid=False,
                        error=f"Mismatched '{stack[-1]}' and '{ch}' at position {i}",
                    )
                stack.pop()

        if stack:
            return ValidationResult(
                block=block,
                is_valid=False,
                error=f"Unclosed delimiter: '{stack[-1]}'",
            )

        return ValidationResult(block=block, is_valid=True)

    def _validate_yaml(self, block: CodeBlock) -> ValidationResult:
        """Validate YAML syntax and flag stale GitHub Actions examples."""
        if re.search(r"actions/(?:upload|download)-artifact@v3\b", block.code):
            return ValidationResult(
                block=block,
                is_valid=False,
                error="Deprecated GitHub Actions artifact action v3; use v4.",
            )

        try:
            import yaml
        except ImportError:
            return ValidationResult(
                block=block,
                is_valid=False,
                error="PyYAML is required for YAML validation.",
            )

        try:
            yaml.safe_load(block.code)
            return ValidationResult(block=block, is_valid=True)
        except yaml.YAMLError as e:
            return ValidationResult(block=block, is_valid=False, error=f"Invalid YAML: {e}")

    def _validate_shell(self, block: CodeBlock) -> ValidationResult:
        """Validate shell snippets for parseability and obvious destructive commands."""
        for offset, line in enumerate(block.code.splitlines(), start=1):
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue

            if re.search(r"\brm\s+-[rfRf-]*\s+/(?:\s|$)", stripped):
                return ValidationResult(
                    block=block,
                    is_valid=False,
                    error=f"Unsafe shell command at line {offset}: refuses to delete root.",
                )

            try:
                shlex.split(stripped)
            except ValueError as e:
                return ValidationResult(
                    block=block,
                    is_valid=False,
                    error=f"Shell parse error at line {offset}: {e}",
                )

        return ValidationResult(block=block, is_valid=True)

    def _validate_json(self, block: CodeBlock) -> ValidationResult:
        """Validate JSON using json.loads()."""
        try:
            json.loads(block.code)
            return ValidationResult(block=block, is_valid=True)
        except json.JSONDecodeError as e:
            return ValidationResult(
                block=block,
                is_valid=False,
                error=f"Invalid JSON at line {e.lineno}, col {e.colno}: {e.msg}",
            )

    def _validate_html(self, block: CodeBlock) -> ValidationResult:
        """Basic HTML validation — checks that opened tags are closed."""
        from html.parser import HTMLParser

        class TagChecker(HTMLParser):
            def __init__(self):
                super().__init__()
                self.stack: list[str] = []
                self.error: str = ""
                # Void elements that don't need closing tags
                self.void_tags = {
                    "area",
                    "base",
                    "br",
                    "col",
                    "embed",
                    "hr",
                    "img",
                    "input",
                    "link",
                    "meta",
                    "param",
                    "source",
                    "track",
                    "wbr",
                }

            def handle_starttag(self, tag, attrs):
                if tag.lower() not in self.void_tags:
                    self.stack.append(tag.lower())

            def handle_endtag(self, tag):
                tag = tag.lower()
                if tag in self.void_tags:
                    return
                if not self.stack:
                    self.error = f"Unexpected closing tag </{tag}>"
                elif self.stack[-1] != tag:
                    self.error = f"Mismatched tags: expected </{self.stack[-1]}>, got </{tag}>"
                else:
                    self.stack.pop()

        checker = TagChecker()
        try:
            checker.feed(block.code)
        except Exception as e:
            return ValidationResult(block=block, is_valid=False, error=f"HTML parse error: {e}")

        if checker.error:
            return ValidationResult(block=block, is_valid=False, error=checker.error)
        if checker.stack:
            return ValidationResult(
                block=block,
                is_valid=False,
                error=f"Unclosed tags: {', '.join(f'<{t}>' for t in checker.stack)}",
            )
        return ValidationResult(block=block, is_valid=True)

    def _validate_sql(self, block: CodeBlock) -> ValidationResult:
        """Basic SQL validation — checks balanced parentheses and statement structure."""
        code = block.code.strip()

        # Check balanced parentheses
        depth = 0
        for ch in code:
            if ch == "(":
                depth += 1
            elif ch == ")":
                depth -= 1
            if depth < 0:
                return ValidationResult(
                    block=block, is_valid=False, error="Unmatched closing parenthesis"
                )
        if depth != 0:
            return ValidationResult(
                block=block, is_valid=False, error="Unclosed parenthesis in SQL"
            )

        # Check that it starts with a known SQL keyword
        first_word = code.split()[0].upper() if code.split() else ""
        sql_keywords = {
            "SELECT",
            "INSERT",
            "UPDATE",
            "DELETE",
            "CREATE",
            "ALTER",
            "DROP",
            "WITH",
            "EXPLAIN",
            "SET",
            "GRANT",
            "REVOKE",
            "BEGIN",
            "COMMIT",
            "ROLLBACK",
            "TRUNCATE",
            "MERGE",
            "CALL",
            "DECLARE",
            "--",
        }
        if first_word and first_word not in sql_keywords:
            return ValidationResult(
                block=block,
                is_valid=False,
                error=f"SQL doesn't start with a known keyword: '{first_word}'",
            )

        return ValidationResult(block=block, is_valid=True)
