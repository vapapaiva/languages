"""
Fluent path resolution — supports dual-mode (clone vs plugin install).

Data directory resolution precedence:
  1. $FLUENT_DATA_DIR if set (absolutized)
  2. $CLAUDE_PROJECT_DIR/data if that dir holds learner-profile.json (clone mode, non-repo cwd)
  3. ./data if ./data/learner-profile.json exists (clone mode, in-repo cwd)
  4. ~/.claude/fluent-data (plugin-mode fallback)

Plugin-root resolution precedence:
  1. $CLAUDE_PLUGIN_ROOT if set
  2. $CLAUDE_PROJECT_DIR if set
  3. parent of this file's .claude/ dir (dev-run fallback)

Pure resolvers (data_dir / plugin_root / backups_dir) do not create directories.
Call ensure_data_dir() before writing.
"""
from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path


@lru_cache(maxsize=1)
def data_dir() -> Path:
    """Resolve the runtime data directory (pure — does not create it)."""
    env = os.environ.get("FLUENT_DATA_DIR")
    if env:
        return Path(env).expanduser().resolve()

    project = os.environ.get("CLAUDE_PROJECT_DIR")
    if project:
        candidate = (Path(project) / "data").resolve()
        if (candidate / "learner-profile.json").exists():
            return candidate

    cwd_data = (Path.cwd() / "data").resolve()
    if (cwd_data / "learner-profile.json").exists():
        return cwd_data

    return (Path.home() / ".claude" / "fluent-data").resolve()


def ensure_data_dir() -> Path:
    """Resolve the data directory and create it if missing. Call before writing."""
    d = data_dir()
    d.mkdir(parents=True, exist_ok=True)
    return d


@lru_cache(maxsize=1)
def plugin_root() -> Path:
    """Resolve the plugin/repo root directory."""
    env = os.environ.get("CLAUDE_PLUGIN_ROOT")
    if env:
        return Path(env).resolve()
    env = os.environ.get("CLAUDE_PROJECT_DIR")
    if env:
        return Path(env).resolve()
    return Path(__file__).resolve().parents[2]


@lru_cache(maxsize=1)
def backups_dir() -> Path:
    """Resolve the backups directory. Always nested inside data_dir to avoid collisions
    when the fallback ~/.claude/fluent-data is used (the parent ~/.claude/ is shared
    across plugins)."""
    return data_dir() / ".backups"


def ensure_backups_dir() -> Path:
    """Resolve the backups directory and create it if missing."""
    b = backups_dir()
    b.mkdir(parents=True, exist_ok=True)
    return b
