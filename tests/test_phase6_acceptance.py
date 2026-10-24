"""Milestone guards: bounded modules, isolated imports and typed public surfaces."""

import ast
import os
import subprocess
import sys
from pathlib import Path


def test_application_modules_remain_bounded():
    oversized = {
        str(path): len(path.read_text().splitlines())
        for path in Path("app").rglob("*.py")
        if len(path.read_text().splitlines()) > 400
    }
    assert not oversized
    assert len(Path("app/main.py").read_text().splitlines()) < 100


def test_every_application_module_imports_in_a_fresh_process():
    for path in sorted(Path("app").rglob("*.py")):
        parts = list(path.with_suffix("").parts)
        if parts[-1] == "__init__":
            parts.pop()
        module = ".".join(parts)
        result = subprocess.run(
            [sys.executable, "-c", f"import importlib; importlib.import_module({module!r})"],
            capture_output=True,
            text=True,
            timeout=20,
            env={**os.environ, "APP_SKIP_LLM": "1"},
        )
        assert result.returncode == 0, (module, result.stderr)


def test_public_scoring_store_llm_and_task_interfaces_are_annotated():
    paths = [
        *Path("app/scoring").glob("*.py"),
        *Path("app/store").glob("*.py"),
        Path("app/llm.py"),
        Path("app/llm_client.py"),
        Path("app/tasks.py"),
    ]
    missing = []
    for path in paths:
        for node in ast.walk(ast.parse(path.read_text())):
            if not isinstance(
                node, (ast.FunctionDef, ast.AsyncFunctionDef)
            ) or node.name.startswith("_"):
                continue
            args = [*node.args.posonlyargs, *node.args.args, *node.args.kwonlyargs]
            if node.returns is None or any(
                arg.annotation is None for arg in args if arg.arg not in ("self", "cls")
            ):
                missing.append((str(path), node.name))
    assert not missing
