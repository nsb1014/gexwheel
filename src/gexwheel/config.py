"""Config loading. FULLY IMPLEMENTED.

Reads YAML, deep-merges over defaults from config.example.yaml semantics,
validates the few keys that must exist. Access pattern: cfg["filters"]["price_min"].
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml

REQUIRED_KEYS = ["db_path", "discord", "data", "reddit", "discovery", "filters", "alerts"]


def _deep_merge(base: dict, override: dict) -> dict:
    out = dict(base)
    for k, v in override.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def load_config(path: str | None = None) -> dict[str, Any]:
    """Load config.yaml. Resolution order:
    1. explicit `path` arg
    2. $GEXWHEEL_CONFIG
    3. ./config/config.yaml
    Falls back onto config.example.yaml defaults for any missing keys.
    """
    example = Path(__file__).resolve().parents[2] / "config" / "config.example.yaml"
    defaults: dict = {}
    if example.exists():
        defaults = yaml.safe_load(example.read_text()) or {}

    candidate = path or os.environ.get("GEXWHEEL_CONFIG") or "config/config.yaml"
    user_cfg: dict = {}
    p = Path(candidate)
    if p.exists():
        user_cfg = yaml.safe_load(p.read_text()) or {}

    cfg = _deep_merge(defaults, user_cfg)
    missing = [k for k in REQUIRED_KEYS if k not in cfg]
    if missing:
        raise ValueError(f"config missing required keys: {missing}")
    return cfg
