"""Tests for the CodeValidator — syntax validation of code blocks in markdown content."""

import pytest

from devrel_swarm.tools.code_validator import CodeBlock, CodeValidator


@pytest.fixture
def validator():
    return CodeValidator()


# ---------------------------------------------------------------------------
# Code block extraction
# ---------------------------------------------------------------------------


class TestExtractCodeBlocks:
    def test_extracts_python_block(self, validator):
        md = "Some text\n\n```python\nprint('hello')\n```\n\nMore text"
        blocks = validator.extract_code_blocks(md)
        assert len(blocks) == 1
        assert blocks[0].language == "python"
        assert blocks[0].code == "print('hello')"

    def test_extracts_multiple_blocks(self, validator):
        md = "```javascript\nconst x = 1;\n```\n\n```python\nx = 1\n```"
        blocks = validator.extract_code_blocks(md)
        assert len(blocks) == 2
        assert blocks[0].language == "javascript"
        assert blocks[1].language == "python"

    def test_extracts_block_without_language(self, validator):
        md = "```\nsome code\n```"
        blocks = validator.extract_code_blocks(md)
        assert len(blocks) == 1
        assert blocks[0].language == ""

    def test_empty_content_returns_no_blocks(self, validator):
        assert validator.extract_code_blocks("") == []
        assert validator.extract_code_blocks("No code blocks here") == []

    def test_tracks_line_number(self, validator):
        md = "Line 1\nLine 2\nLine 3\n\n```python\nx = 1\n```"
        blocks = validator.extract_code_blocks(md)
        assert blocks[0].line_number == 5  # block starts after 4 newlines

    def test_skips_empty_code_blocks(self, validator):
        md = "```python\n\n```"
        blocks = validator.extract_code_blocks(md)
        assert len(blocks) == 0


# ---------------------------------------------------------------------------
# Python validation
# ---------------------------------------------------------------------------


class TestPythonValidation:
    def test_valid_python(self, validator):
        block = CodeBlock(language="python", code="x = 1\nprint(x)", line_number=1)
        result = validator.validate_block(block)
        assert result.is_valid

    def test_valid_python_function(self, validator):
        code = "def hello(name):\n    return f'Hello, {name}!'"
        block = CodeBlock(language="python", code=code, line_number=1)
        result = validator.validate_block(block)
        assert result.is_valid

    def test_valid_python_class(self, validator):
        code = "class Foo:\n    def bar(self):\n        pass"
        block = CodeBlock(language="python", code=code, line_number=1)
        result = validator.validate_block(block)
        assert result.is_valid

    def test_invalid_python_syntax(self, validator):
        block = CodeBlock(language="python", code="def foo(\n  x = ", line_number=1)
        result = validator.validate_block(block)
        assert not result.is_valid
        assert "syntax error" in result.error.lower()

    def test_python_with_ellipsis_placeholder(self, validator):
        code = "def setup():\n    ...\n\nsetup()"
        block = CodeBlock(language="python", code=code, line_number=1)
        result = validator.validate_block(block)
        assert result.is_valid

    def test_py_alias(self, validator):
        block = CodeBlock(language="py", code="x = 1", line_number=1)
        result = validator.validate_block(block)
        assert result.is_valid

    def test_python_import_statement(self, validator):
        code = "import posthog\nposthog.capture('user_123', 'event_name')"
        block = CodeBlock(language="python", code=code, line_number=1)
        result = validator.validate_block(block)
        assert result.is_valid

    def test_python_unclosed_string(self, validator):
        block = CodeBlock(language="python", code='x = "unclosed', line_number=1)
        result = validator.validate_block(block)
        assert not result.is_valid

    def test_python_async_code(self, validator):
        code = "async def main():\n    await fetch_data()\n    return True"
        block = CodeBlock(language="python", code=code, line_number=1)
        result = validator.validate_block(block)
        assert result.is_valid


# ---------------------------------------------------------------------------
# JavaScript validation
# ---------------------------------------------------------------------------


class TestJavaScriptValidation:
    def test_valid_javascript(self, validator):
        code = "const x = 1;\nconsole.log(x);"
        block = CodeBlock(language="javascript", code=code, line_number=1)
        result = validator.validate_block(block)
        assert result.is_valid

    def test_valid_js_function(self, validator):
        code = "function greet(name) {\n  return `Hello, ${name}`;\n}"
        block = CodeBlock(language="javascript", code=code, line_number=1)
        result = validator.validate_block(block)
        assert result.is_valid

    def test_js_unmatched_brace(self, validator):
        code = "function foo() {\n  return 1;\n"
        block = CodeBlock(language="javascript", code=code, line_number=1)
        result = validator.validate_block(block)
        assert not result.is_valid
        assert "Unclosed" in result.error

    def test_js_unmatched_closing(self, validator):
        code = "const arr = [1, 2, 3]];"
        block = CodeBlock(language="javascript", code=code, line_number=1)
        result = validator.validate_block(block)
        assert not result.is_valid
        assert "Unmatched" in result.error or "Mismatched" in result.error

    def test_js_alias(self, validator):
        block = CodeBlock(language="js", code="let x = 1;", line_number=1)
        result = validator.validate_block(block)
        assert result.is_valid

    def test_js_with_strings_containing_braces(self, validator):
        code = 'const msg = "Use {name} here";\nconst obj = { key: msg };'
        block = CodeBlock(language="javascript", code=code, line_number=1)
        result = validator.validate_block(block)
        assert result.is_valid

    def test_js_arrow_function(self, validator):
        code = "const add = (a, b) => a + b;\nconsole.log(add(1, 2));"
        block = CodeBlock(language="javascript", code=code, line_number=1)
        result = validator.validate_block(block)
        assert result.is_valid

    def test_js_with_comments(self, validator):
        code = "// Set up PostHog\nconst posthog = require('posthog-js');\n/* init */\nposthog.init('key');"
        block = CodeBlock(language="javascript", code=code, line_number=1)
        result = validator.validate_block(block)
        assert result.is_valid

    def test_js_mismatched_delimiters(self, validator):
        code = "const arr = [1, 2, 3);"
        block = CodeBlock(language="javascript", code=code, line_number=1)
        result = validator.validate_block(block)
        assert not result.is_valid
        assert "Mismatched" in result.error


# ---------------------------------------------------------------------------
# JSON validation
# ---------------------------------------------------------------------------


class TestJsonValidation:
    def test_valid_json_object(self, validator):
        code = '{"event": "page_view", "properties": {"url": "/home"}}'
        block = CodeBlock(language="json", code=code, line_number=1)
        result = validator.validate_block(block)
        assert result.is_valid

    def test_valid_json_array(self, validator):
        code = '[1, 2, 3]'
        block = CodeBlock(language="json", code=code, line_number=1)
        result = validator.validate_block(block)
        assert result.is_valid

    def test_invalid_json_trailing_comma(self, validator):
        code = '{"key": "value",}'
        block = CodeBlock(language="json", code=code, line_number=1)
        result = validator.validate_block(block)
        assert not result.is_valid
        assert "Invalid JSON" in result.error

    def test_invalid_json_missing_quotes(self, validator):
        code = '{key: "value"}'
        block = CodeBlock(language="json", code=code, line_number=1)
        result = validator.validate_block(block)
        assert not result.is_valid


# ---------------------------------------------------------------------------
# HTML validation
# ---------------------------------------------------------------------------


class TestHtmlValidation:
    def test_valid_html(self, validator):
        code = "<div><p>Hello</p></div>"
        block = CodeBlock(language="html", code=code, line_number=1)
        result = validator.validate_block(block)
        assert result.is_valid

    def test_valid_html_with_void_elements(self, validator):
        code = "<div><img src='logo.png'><br><input type='text'></div>"
        block = CodeBlock(language="html", code=code, line_number=1)
        result = validator.validate_block(block)
        assert result.is_valid

    def test_unclosed_html_tag(self, validator):
        code = "<div><p>Hello</div>"
        block = CodeBlock(language="html", code=code, line_number=1)
        result = validator.validate_block(block)
        assert not result.is_valid
        assert "Mismatched" in result.error or "Unclosed" in result.error

    def test_html_self_closing_void(self, validator):
        code = "<img src='x.png'>"
        block = CodeBlock(language="html", code=code, line_number=1)
        result = validator.validate_block(block)
        assert result.is_valid


# ---------------------------------------------------------------------------
# SQL validation
# ---------------------------------------------------------------------------


class TestSqlValidation:
    def test_valid_select(self, validator):
        code = "SELECT * FROM users WHERE id = 1"
        block = CodeBlock(language="sql", code=code, line_number=1)
        result = validator.validate_block(block)
        assert result.is_valid

    def test_valid_create_table(self, validator):
        code = "CREATE TABLE events (\n  id UUID PRIMARY KEY,\n  name VARCHAR(255)\n)"
        block = CodeBlock(language="sql", code=code, line_number=1)
        result = validator.validate_block(block)
        assert result.is_valid

    def test_sql_unmatched_paren(self, validator):
        code = "SELECT * FROM (SELECT id FROM users"
        block = CodeBlock(language="sql", code=code, line_number=1)
        result = validator.validate_block(block)
        assert not result.is_valid
        assert "parenthesis" in result.error.lower()

    def test_sql_comment_start(self, validator):
        code = "-- This is a comment\nSELECT 1"
        block = CodeBlock(language="sql", code=code, line_number=1)
        result = validator.validate_block(block)
        assert result.is_valid


# ---------------------------------------------------------------------------
# Skip / unknown languages
# ---------------------------------------------------------------------------


class TestSkipLanguages:
    def test_bash_is_skipped(self, validator):
        block = CodeBlock(language="bash", code="rm -rf /", line_number=1)
        result = validator.validate_block(block)
        assert result.is_valid  # skipped, not validated

    def test_yaml_is_skipped(self, validator):
        block = CodeBlock(language="yaml", code="key: value", line_number=1)
        result = validator.validate_block(block)
        assert result.is_valid

    def test_unknown_language_passes(self, validator):
        block = CodeBlock(language="rust", code="fn main() {}", line_number=1)
        result = validator.validate_block(block)
        assert result.is_valid

    def test_empty_language_skipped(self, validator):
        block = CodeBlock(language="", code="some code", line_number=1)
        result = validator.validate_block(block)
        assert result.is_valid


# ---------------------------------------------------------------------------
# Full content validation
# ---------------------------------------------------------------------------


class TestValidateContent:
    def test_all_valid_content(self, validator):
        md = """# Tutorial

## Step 1: Install

```bash
pip install posthog
```

## Step 2: Initialize

```python
import posthog
posthog.project_api_key = 'phc_xxx'
```

## Step 3: Track events

```javascript
posthog.capture('page_view', { url: window.location.href });
```
"""
        report = validator.validate_content(md)
        assert report.all_passed
        assert report.total_blocks == 3
        assert report.skipped == 1  # bash
        assert report.validated == 2  # python + javascript
        assert report.passed == 2
        assert report.failed == 0

    def test_content_with_invalid_block(self, validator):
        md = """# Tutorial

```python
def broken(
  x =
```

```javascript
const x = 1;
```
"""
        report = validator.validate_content(md)
        assert not report.all_passed
        assert report.failed == 1
        assert report.passed == 1
        assert len(report.errors) == 1
        assert report.errors[0].block.language == "python"

    def test_empty_content(self, validator):
        report = validator.validate_content("")
        assert report.all_passed
        assert report.total_blocks == 0

    def test_content_with_only_bash(self, validator):
        md = "```bash\nnpm install posthog-js\n```"
        report = validator.validate_content(md)
        assert report.all_passed
        assert report.skipped == 1
        assert report.validated == 0

    def test_report_includes_error_details(self, validator):
        md = '```json\n{invalid: json}\n```'
        report = validator.validate_content(md)
        assert report.failed == 1
        err = report.errors[0]
        assert err.block.language == "json"
        assert "Invalid JSON" in err.error


# ---------------------------------------------------------------------------
# Kai integration
# ---------------------------------------------------------------------------


class TestKaiCodeValidation:
    """Test that Kai includes code_validation in results when LLM generates content."""

    @pytest.fixture
    def wired_kai(self, posthog_client, knowledge_base_path, mock_llm_client):
        from unittest.mock import AsyncMock

        mock_llm_client.generate = AsyncMock(
            return_value=(
                "# Feature Flags Tutorial\n\n"
                "## Step 1: Install\n\n"
                "```bash\npip install posthog\n```\n\n"
                "## Step 2: Initialize\n\n"
                "```python\n"
                "import posthog\n"
                "posthog.project_api_key = 'phc_xxx'\n"
                "```\n\n"
                "## Step 3: Check flag\n\n"
                "```javascript\n"
                "if (posthog.isFeatureEnabled('new-ui')) {\n"
                "  showNewUI();\n"
                "}\n"
                "```\n"
            )
        )
        from devrel_swarm.core.kai import Kai

        return Kai(
            api_client=posthog_client,
            knowledge_base_path=knowledge_base_path,
            llm_client=mock_llm_client,
        )

    @pytest.fixture
    def broken_kai(self, posthog_client, knowledge_base_path, mock_llm_client):
        from unittest.mock import AsyncMock

        mock_llm_client.generate = AsyncMock(
            return_value=(
                "# Tutorial\n\n"
                "```python\n"
                "def broken(\n"
                "  x = \n"
                "```\n"
            )
        )
        from devrel_swarm.core.kai import Kai

        return Kai(
            api_client=posthog_client,
            knowledge_base_path=knowledge_base_path,
            llm_client=mock_llm_client,
        )

    @pytest.mark.asyncio
    async def test_execute_includes_code_validation(self, wired_kai):
        result = await wired_kai.execute("Write a tutorial")
        assert "code_validation" in result
        cv = result["code_validation"]
        assert cv["total_blocks"] == 3
        assert cv["all_passed"] is True
        assert cv["failed"] == 0

    @pytest.mark.asyncio
    async def test_execute_reports_invalid_code(self, broken_kai):
        result = await broken_kai.execute("Write a tutorial")
        assert "code_validation" in result
        cv = result["code_validation"]
        assert cv["all_passed"] is False
        assert cv["failed"] == 1
        assert len(cv["errors"]) == 1
        assert cv["errors"][0]["language"] == "python"
        assert "syntax error" in cv["errors"][0]["error"].lower()
