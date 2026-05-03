"""Retro console platform client.

Reads cache/retro_games.json (produced by retro_collector.py / enrich_igdb.py)
and exposes each console as a separate PlatformClient (nes, snes, n64, ...).

Games are reported as "owned but not installed" — the dashboard's value is
showing Twitch viewer counts for these titles, not launching them.
"""
from __future__ import annotations

import json
import os
from typing import Dict, List, Optional, Set

from platform_client import PlatformClient

_GAMES_PATH_DEFAULT = os.path.join("cache", "retro_games.json")

# Output keys in retro_games.json -> human-readable label for badges/UI.
SYSTEM_DISPLAY_NAMES: Dict[str, str] = {
    "nes":       "NES",
    "snes":      "SNES",
    "n64":       "N64",
    "gamecube":  "GameCube",
    "gameboy":   "Game Boy",
    "gbc":       "Game Boy Color",
    "gba":       "Game Boy Advance",
    "genesis":   "Genesis",
    "saturn":    "Saturn",
    "dreamcast": "Dreamcast",
    "ps1":       "PS1",
    "ps2":       "PS2",
    "3do":       "3DO",
}

ALL_SYSTEMS: List[str] = list(SYSTEM_DISPLAY_NAMES.keys())

_DATA_CACHE: Optional[Dict] = None


def _load_data(path: str = _GAMES_PATH_DEFAULT) -> Dict:
    global _DATA_CACHE
    if _DATA_CACHE is None:
        try:
            with open(path, "r", encoding="utf-8") as f:
                _DATA_CACHE = json.load(f)
        except FileNotFoundError:
            print(f"[retro_client] {path} not found; run retro_collector.py first.")
            _DATA_CACHE = {"platforms": {}}
        except Exception as e:
            print(f"[retro_client] error loading {path}: {e}")
            _DATA_CACHE = {"platforms": {}}
    return _DATA_CACHE


def _appid_from_filename(filename: str) -> str:
    """Use the libretro filename stem as the stable appid for an entry."""
    if filename.lower().endswith(".png"):
        return filename[:-4]
    return filename


class RetroClient(PlatformClient):
    """Single-system retro platform client. Reads from cache/retro_games.json."""

    def __init__(self, system: str, data_path: str = _GAMES_PATH_DEFAULT):
        if system not in SYSTEM_DISPLAY_NAMES:
            raise ValueError(f"unsupported retro system: {system!r}")
        self.system = system
        self._data_path = data_path
        self._index: Optional[Dict[str, Dict]] = None

    @property
    def platform_name(self) -> str:
        return self.system

    @property
    def display_name(self) -> str:
        return SYSTEM_DISPLAY_NAMES[self.system]

    def _ensure_index(self) -> Dict[str, Dict]:
        if self._index is None:
            data = _load_data(self._data_path)
            entries = (data.get("platforms") or {}).get(self.system) or []
            idx: Dict[str, Dict] = {}
            for e in entries:
                filename = e.get("filename") or ""
                if not filename:
                    continue
                idx[_appid_from_filename(filename)] = e
            self._index = idx
        return self._index

    def get_owned_games(self, force_refresh: bool = False) -> List[Dict]:
        out: List[Dict] = []
        for appid, entry in self._ensure_index().items():
            out.append({
                "appid": appid,
                "name": entry.get("name") or appid,
                "platform": self.system,
                "installed": False,
                "playtime_forever": 0,
                "rtime_last_played": 0,
                "header_image": entry.get("thumbnail_url"),
            })
        return out

    def get_installed_appids(self) -> Set[str]:
        return set()

    def get_image_url(self, appid: str) -> Optional[str]:
        entry = self._ensure_index().get(appid)
        return entry.get("thumbnail_url") if entry else None

    def get_store_metadata(self, appid: str) -> Dict:
        entry = self._ensure_index().get(appid)
        if not entry:
            return super().get_store_metadata(appid)
        # Genre arrives from OpenVGDB / IGDB as a comma-joined string.
        genres: List[str] = []
        if entry.get("genre"):
            genres = [g.strip() for g in str(entry["genre"]).split(",") if g.strip()]
        tags: List[str] = []
        if entry.get("developer"):
            tags.append(entry["developer"])
        if entry.get("publisher"):
            tags.append(entry["publisher"])
        tags.extend(genres)
        description = entry.get("description") or ""
        return {
            "genres": genres,
            "categories": [],
            "tags": tags,
            "short_description": description[:250],
            "description": description,
        }

    def test_connection(self) -> bool:
        return os.path.exists(self._data_path)


def create_retro_clients(systems: List[str], data_path: str = _GAMES_PATH_DEFAULT) -> List[RetroClient]:
    """Build one RetroClient per requested system. Unknown system names are skipped."""
    out: List[RetroClient] = []
    for s in systems:
        if s in SYSTEM_DISPLAY_NAMES:
            out.append(RetroClient(s, data_path=data_path))
        else:
            print(f"[retro_client] ignoring unknown system: {s!r}")
    return out


def is_retro_platform(platform: str) -> bool:
    return platform in SYSTEM_DISPLAY_NAMES
