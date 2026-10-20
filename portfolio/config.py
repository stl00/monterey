"""Configuration from environment variables, optionally loaded from a .env file."""
from __future__ import annotations

import os
from pathlib import Path


def load_dotenv(path: str | Path = ".env") -> None:
    """Minimal .env loader: KEY=VALUE lines; existing env vars win."""
    p = Path(path)
    if not p.exists():
        return
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def env(key: str, default: str | None = None) -> str | None:
    value = os.environ.get(key)
    return value if value not in (None, "") else default


def require(key: str) -> str:
    value = env(key)
    if value is None:
        raise RuntimeError(f"Missing required setting {key} (see .env.example)")
    return value


def data_dir() -> Path:
    d = Path(env("PORTFOLIO_DATA_DIR", "data"))
    d.mkdir(parents=True, exist_ok=True)
    return d


def base_currency() -> str:
    return env("PORTFOLIO_BASE_CCY", "HKD").upper()


def enabled_sources() -> list[str]:
    return [s.strip().lower() for s in env("PORTFOLIO_SOURCES", "schwab,okx,futu").split(",") if s.strip()]
