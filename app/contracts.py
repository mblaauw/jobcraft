"""Shared row interface for persisted SQLite jobs and synthetic mapping inputs."""

import sqlite3
from collections.abc import Mapping
from typing import Any

type JobRow = sqlite3.Row | Mapping[str, Any]
