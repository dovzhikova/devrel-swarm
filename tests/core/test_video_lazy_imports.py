"""Lock in lazy-import contract for the video subpackage.

`openai`, `playwright`, and `pyautogui` are optional `[video]` extras as of
the move to `pyproject.optional-dependencies.video`. Core imports must not
trigger any of them at module load. A regression here would re-bloat the
default install with ~150MB of Playwright browsers + pyobjc.

Tests must NOT pop modules out of `sys.modules` — that breaks `unittest.mock.patch`
references in sibling tests. Instead inspect the already-loaded module
namespaces, and use `builtins.__import__` patching to simulate "openai
not installed" for the helpful-error test.
"""

import builtins
import importlib

import pytest


class TestTTSEngineLazyImport:
    def test_require_openai_raises_helpful_error_when_missing(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        """Block the openai import path; `_require_openai()` must surface a
        clear pip-install hint instead of the bare 'No module' traceback.
        """
        from devrel_origin.core.video import tts_engine

        real_import = builtins.__import__

        def fake_import(name: str, *args, **kwargs):
            if name == "openai":
                raise ImportError("No module named 'openai'")
            return real_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", fake_import)

        with pytest.raises(ImportError, match=r"devrel-origin\[video\]"):
            tts_engine._require_openai()

    def test_tts_engine_module_namespace_has_no_async_openai(self):
        """Module-load of tts_engine must not bind `AsyncOpenAI` at top level.

        If a maintainer accidentally re-adds `from openai import AsyncOpenAI`
        at the top of tts_engine.py, this assert catches it before any
        non-[video] user feels the regression.
        """
        from devrel_origin.core.video import tts_engine

        assert "AsyncOpenAI" not in vars(tts_engine), (
            "tts_engine.py eagerly imported openai.AsyncOpenAI — keep it lazy"
        )


class TestCoreImportsHaveNoVideoEagerLoad:
    @pytest.mark.parametrize(
        "module_path",
        [
            "devrel_origin",
            "devrel_origin.core.atlas",
            "devrel_origin.core.argus",
            "devrel_origin.core.kai",
            "devrel_origin.core.echo",
            "devrel_origin.cli",
        ],
    )
    def test_no_top_level_openai_async_client_in_namespace(self, module_path: str):
        """None of the core paths a non-[video] user touches should expose
        an `AsyncOpenAI` symbol in their module namespace; that would be the
        smoking gun for a top-level openai import.
        """
        module = importlib.import_module(module_path)
        assert "AsyncOpenAI" not in vars(module), (
            f"{module_path} eagerly imports openai.AsyncOpenAI — keep it lazy"
        )
