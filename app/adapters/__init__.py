"""Discover adapters declaring KINDS and scan; adding one needs no dispatch edit."""

from functools import lru_cache
from importlib import import_module
from pkgutil import iter_modules

from .interfaces import Adapter


@lru_cache(maxsize=1)
def registry() -> dict[str, Adapter]:
    result = {}
    for info in sorted(iter_modules(__path__), key=lambda item: item.name):
        if info.name.startswith("_"):
            continue
        module = import_module(f"{__name__}.{info.name}")
        for kind in getattr(module, "KINDS", ()):
            if kind in result:
                raise ValueError(f"Duplicate adapter kind: {kind}")
            result[kind] = module.scan
    return result
