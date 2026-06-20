"""
In-memory session store for per-thread processing configurations.
Each Discord thread gets its own ProcessConfig that persists for the bot's lifetime.
"""

from __future__ import annotations

import asyncio
from typing import Optional

from processor import ProcessConfig, hex_to_rgb


# thread_id (int) -> (config, original_image_bytes)
_sessions: dict[int, tuple[ProcessConfig, bytes]] = {}
_lock = asyncio.Lock()


async def get_session(thread_id: int) -> Optional[tuple[ProcessConfig, bytes]]:
    async with _lock:
        return _sessions.get(thread_id)


async def create_session(thread_id: int, image_bytes: bytes) -> ProcessConfig:
    async with _lock:
        cfg = ProcessConfig()
        _sessions[thread_id] = (cfg, image_bytes)
        return cfg


async def update_session(thread_id: int, **kwargs) -> Optional[ProcessConfig]:
    """Update specific fields of a session's config."""
    async with _lock:
        entry = _sessions.get(thread_id)
        if entry is None:
            return None
        cfg, img_bytes = entry
        for k, v in kwargs.items():
            if hasattr(cfg, k):
                setattr(cfg, k, v)
        return cfg


async def reset_session(thread_id: int) -> Optional[ProcessConfig]:
    """Reset config to defaults while keeping original image."""
    async with _lock:
        entry = _sessions.get(thread_id)
        if entry is None:
            return None
        _, img_bytes = entry
        cfg = ProcessConfig()
        _sessions[thread_id] = (cfg, img_bytes)
        return cfg


async def add_remove_colour(thread_id: int, hex_str: str) -> Optional[ProcessConfig]:
    async with _lock:
        entry = _sessions.get(thread_id)
        if entry is None:
            return None
        cfg, img_bytes = entry
        try:
            rgb = hex_to_rgb(hex_str)
        except ValueError:
            return None
        if rgb not in cfg.remove_colours:
            cfg.remove_colours.append(rgb)
        return cfg


async def clear_remove_colours(thread_id: int) -> Optional[ProcessConfig]:
    async with _lock:
        entry = _sessions.get(thread_id)
        if entry is None:
            return None
        cfg, _ = entry
        cfg.remove_colours.clear()
        return cfg


async def delete_session(thread_id: int) -> None:
    async with _lock:
        _sessions.pop(thread_id, None)