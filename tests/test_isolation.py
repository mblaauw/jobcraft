from __future__ import annotations

import httpx
import pytest

from app import config


def test_real_network_is_blocked():
    with pytest.raises(RuntimeError, match="network disabled in tests"):
        httpx.get("https://example.org", timeout=1)


def test_module_paths_point_into_the_disposable_instance(isolated_paths):
    assert str(config.DATA_DIR).startswith(str(isolated_paths))
    assert str(config.DB_PATH).startswith(str(isolated_paths))
    assert str(config.RUNTIME_DIR).startswith(str(isolated_paths))
