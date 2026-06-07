from __future__ import annotations
"""
Reads ~/.pullwise.toml (or the path in PULLWISE_CONFIG env var).

Example config:
    [pullwise]
    api_key = "sk-..."
    model   = "gpt-4o-mini"
    base    = "main"
"""
import os
from pathlib import Path

_DEFAULTS = {
    "api_key": "",
    "model": "gpt-4o",
    "base": "main",
}

_cfg: dict[str, str] | None = None


def _config_path() -> Path:
    env = os.environ.get("PULLWISE_CONFIG")
    if env:
        return Path(env)
    return Path.home() / ".pullwise.toml"


def _load() -> dict[str, str]:
    global _cfg
    if _cfg is not None:
        return _cfg
    _cfg = dict(_DEFAULTS)
    p = _config_path()
    if not p.exists():
        return _cfg
    try:
        # Minimal TOML parser — only handles key = "value" lines in [pullwise]
        in_section = False
        for raw in p.read_text(errors="replace").splitlines():
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            if line.startswith("["):
                in_section = line.lower() in ("[pullwise]",)
                continue
            if in_section and "=" in line:
                key, _, val = line.partition("=")
                key = key.strip().lower()
                val = val.strip().strip('"').strip("'")
                if key in _cfg:
                    _cfg[key] = val
    except Exception:
        pass
    return _cfg


def get(key: str) -> str:
    """Return config value. Precedence: env vars > config file > built-in default."""
    # Env var overrides config file
    env_map = {
        "api_key": "OPENAI_API_KEY",
        "model":   "PULLWISE_MODEL",
        "base":    "PULLWISE_BASE",
    }
    env_val = os.environ.get(env_map.get(key, ""), "")
    if env_val:
        return env_val
    return _load().get(key, _DEFAULTS.get(key, ""))


def api_key() -> str:
    return get("api_key")


def default_model() -> str:
    return get("model")


def default_base() -> str:
    return get("base")
