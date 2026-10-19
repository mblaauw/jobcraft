"""Create .env.local with the OpenCode Go key from the local OpenCode auth store.

Never prints the provider key. Prints the generated app password (local dev only).
"""

from __future__ import annotations

import json
import secrets
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
AUTH = Path.home() / ".local/share/opencode/auth.json"
TARGET = REPO / ".env.local"


def main() -> None:
    if not AUTH.exists():
        raise SystemExit(f"auth store not found: {AUTH}")
    auth = json.loads(AUTH.read_text())
    entry = auth.get("opencode-go") or {}
    key = entry.get("key")
    if not key:
        raise SystemExit("no opencode-go api key in the auth store")

    existing: dict[str, str] = {}
    if TARGET.exists():
        for line in TARGET.read_text().splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                name, _, value = line.partition("=")
                existing[name.strip()] = value.strip()

    password = existing.get("APP_PASSWORD") or secrets.token_urlsafe(18)
    TARGET.write_text(
        f"OPENCODE_GO_KEY={key}\nAPP_USER={existing.get('APP_USER', 'owner')}\n"
        f"APP_PASSWORD={password}\n"
    )
    TARGET.chmod(0o600)
    print(f"wrote {TARGET} (mode 600)")
    print("APP_USER=owner")
    print(f"APP_PASSWORD={password}")


if __name__ == "__main__":
    main()
