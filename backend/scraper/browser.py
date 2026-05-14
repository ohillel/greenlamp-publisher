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
    p = session_path(site)
    if p.exists():
        return {"storage_state": str(p)}
    return {}


def screenshot(page, name: str, debug: bool):
    if debug:
        path = DEBUG_DIR / f"{name}.png"
        page.screenshot(path=str(path), full_page=True)
        print(f"  [screenshot] {path}")
