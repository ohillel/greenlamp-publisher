"""
Shared browser utilities: launch, session save/restore, screenshot helpers.
"""
import json
import os
from pathlib import Path

SESSION_DIR = Path(__file__).parent / "sessions"
DEBUG_DIR   = Path(__file__).parent / "debug_screenshots"
SESSION_DIR.mkdir(exist_ok=True)
DEBUG_DIR.mkdir(exist_ok=True)


def session_path(site: str) -> Path:
    return SESSION_DIR / f"{site}.json"


def save_session(context, site: str):
    storage = context.storage_state()
    session_path(site).write_text(json.dumps(storage))


def load_session_kwargs(site: str) -> dict:
    """
    Returns {"storage_state": path} if a session file exists and is valid JSON.
    A corrupt/partial file (e.g. from a Railway redeploy killing the process
    mid-write) is treated as "no session" rather than crashing the launch.
    """
    p = session_path(site)
    if not p.exists():
        return {}
    try:
        json.loads(p.read_text())
        return {"storage_state": str(p)}
    except Exception:
        print(f"  [{site}] session file is corrupt — ignoring and re-logging in")
        return {}


def clear_session(site: str):
    """Delete a stale/invalid session file so the next run logs in fresh."""
    p = session_path(site)
    try:
        p.unlink(missing_ok=True)
    except Exception:
        pass


# Per-action timeout applied to every Playwright wait/click/fill on a page.
# Without this, a single unexpected UI change can fall back to Playwright's
# default 30s per action and compound across many actions into minutes.
DEFAULT_ACTION_TIMEOUT_MS = 20_000


def apply_default_timeouts(context, page):
    context.set_default_timeout(DEFAULT_ACTION_TIMEOUT_MS)
    context.set_default_navigation_timeout(DEFAULT_ACTION_TIMEOUT_MS)
    page.set_default_timeout(DEFAULT_ACTION_TIMEOUT_MS)
    page.set_default_navigation_timeout(DEFAULT_ACTION_TIMEOUT_MS)


def screenshot(page, name: str, debug: bool = True):
    """
    Save a screenshot. Always saves on failure paths (debug=True passed
    explicitly by callers) so problems can be diagnosed from the last run
    without needing to reproduce with a manual debug flag.
    """
    if not debug:
        return
    try:
        path = DEBUG_DIR / f"{name}.png"
        page.screenshot(path=str(path), full_page=True)
        print(f"  [screenshot] {path}")
    except Exception as e:
        print(f"  [screenshot] failed to save {name}: {e}")
