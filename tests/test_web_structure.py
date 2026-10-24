import ast
from pathlib import Path

from app import auth, main, web


def test_shared_renderer_and_small_composition_root():
    assert main.templates is auth.templates is web.templates
    assert len(Path(main.__file__).read_text().splitlines()) < 100
    tree = ast.parse(Path(main.__file__).read_text())
    assert not any(isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) for node in tree.body)


def test_all_routes_have_unique_method_path_pairs():
    pairs = [
        (method, route.path)
        for route in main.app.routes
        for method in getattr(route, "methods", ())
    ]
    assert len(pairs) == len(set(pairs))
