"""Detect the game the user is currently playing.

Two strategies, tried in order:

1. Steam Web API ``GetPlayerSummaries`` — exposes a ``gameid`` field whenever
   the configured Steam user is in-game. Authoritative for Steam, no local
   process scanning required, and works whether the dashboard runs on the
   same machine as Steam or not.

2. Window-title / process-name scan — best-effort fallback for non-Steam
   platforms (Epic, GOG, etc.). Uses ``psutil`` for process names plus a
   handful of OS-specific helpers (``wmctrl``/``xdotool`` on Linux,
   ``pygetwindow`` on Windows, AppleScript on macOS) when available.
"""

from __future__ import annotations

import platform
import re
import shutil
import subprocess
import sys
import unicodedata
from dataclasses import dataclass
from typing import Iterable, List, Optional

import requests

from config import config


PLAYER_SUMMARIES_URL = (
    "https://api.steampowered.com/ISteamUser/GetPlayerSummaries/v0002/"
)


@dataclass
class Detection:
    appid: Optional[str]
    name: Optional[str]
    platform: Optional[str]
    source: str  # "steam_api" | "window_title" | "process_name" | "none"
    raw: Optional[str] = None  # debug / matched signal


# ---------------------------------------------------------------------------
# Steam Web API
# ---------------------------------------------------------------------------


def detect_via_steam_api() -> Optional[Detection]:
    api_key = config.STEAM_API_KEY
    steamid = config.STEAMID64
    if not api_key or not steamid:
        return None
    try:
        resp = requests.get(
            PLAYER_SUMMARIES_URL,
            params={"key": api_key, "steamids": steamid},
            timeout=8,
        )
        resp.raise_for_status()
        players = resp.json().get("response", {}).get("players", []) or []
        if not players:
            return None
        player = players[0]
        appid = player.get("gameid")
        if not appid:
            return None
        return Detection(
            appid=str(appid),
            name=player.get("gameextrainfo") or None,
            platform="steam",
            source="steam_api",
            raw=str(player.get("gameextrainfo") or appid),
        )
    except Exception as e:  # network errors, key issues, etc.
        print(f"current-game: Steam API check failed: {e}")
        return None


# ---------------------------------------------------------------------------
# Window-title / process-name fallback
# ---------------------------------------------------------------------------


_NORMALIZE_STRIP = re.compile(r"[^a-z0-9]+")


def _normalize(value: str) -> str:
    if not value:
        return ""
    nfkd = unicodedata.normalize("NFKD", value)
    ascii_str = nfkd.encode("ascii", "ignore").decode("ascii")
    return _NORMALIZE_STRIP.sub("", ascii_str.lower())


def _list_window_titles() -> List[str]:
    titles: List[str] = []
    system = platform.system()
    try:
        if system == "Linux":
            if shutil.which("wmctrl"):
                out = subprocess.run(
                    ["wmctrl", "-l"], capture_output=True, text=True, timeout=3
                )
                for line in out.stdout.splitlines():
                    parts = line.split(None, 3)
                    if len(parts) >= 4:
                        titles.append(parts[3])
            elif shutil.which("xdotool"):
                out = subprocess.run(
                    ["xdotool", "search", "--name", ".+"],
                    capture_output=True,
                    text=True,
                    timeout=3,
                )
                for wid in out.stdout.split():
                    n = subprocess.run(
                        ["xdotool", "getwindowname", wid],
                        capture_output=True,
                        text=True,
                        timeout=2,
                    )
                    if n.stdout.strip():
                        titles.append(n.stdout.strip())
        elif system == "Windows":
            try:
                import pygetwindow  # type: ignore

                titles.extend(t for t in pygetwindow.getAllTitles() if t)
            except Exception:
                pass
        elif system == "Darwin":
            script = (
                'tell application "System Events" to get the name of every window of '
                "(every process whose background only is false)"
            )
            out = subprocess.run(
                ["osascript", "-e", script],
                capture_output=True,
                text=True,
                timeout=3,
            )
            if out.returncode == 0:
                titles.extend(
                    t.strip() for t in out.stdout.split(",") if t.strip()
                )
    except Exception as e:
        print(f"current-game: window-title scan failed: {e}")
    return titles


def _list_process_names() -> List[str]:
    try:
        import psutil  # type: ignore
    except ImportError:
        return []
    names: List[str] = []
    for proc in psutil.process_iter(attrs=["name"]):
        try:
            n = proc.info.get("name") or ""
        except Exception:
            continue
        if not n:
            continue
        # Strip extension and common executable suffixes.
        if n.lower().endswith(".exe"):
            n = n[:-4]
        names.append(n)
    return names


def detect_via_titles(library_games: Iterable[dict]) -> Optional[Detection]:
    """Match window titles / process names against library game names.

    Picks the longest match to avoid partial collisions ("Crab" vs.
    "Crab Champions"). Skips matches shorter than 3 normalized chars.
    """
    library = [g for g in library_games if g.get("name")]
    if not library:
        return None

    norm_library = [(_normalize(g["name"]), g) for g in library]
    candidates: List[tuple[str, str]] = []  # (signal_type, signal_text)
    for t in _list_window_titles():
        candidates.append(("window_title", t))
    for n in _list_process_names():
        candidates.append(("process_name", n))

    best: Optional[tuple[int, dict, str, str]] = None  # (match_len, game, source, raw)
    for source, signal in candidates:
        norm_signal = _normalize(signal)
        if len(norm_signal) < 3:
            continue
        for norm_name, game in norm_library:
            if len(norm_name) < 3:
                continue
            if norm_name in norm_signal:
                if best is None or len(norm_name) > best[0]:
                    best = (len(norm_name), game, source, signal)
    if not best:
        return None
    _, game, source, raw = best
    return Detection(
        appid=str(game.get("appid") or ""),
        name=game.get("name"),
        platform=game.get("platform"),
        source=source,
        raw=raw,
    )


# ---------------------------------------------------------------------------
# Public entrypoint
# ---------------------------------------------------------------------------


def detect_current_game(library_games: Iterable[dict]) -> Detection:
    """Return the current game detection. Always returns a Detection object;
    ``appid`` is None when nothing was found."""
    library_games = list(library_games)

    steam_hit = detect_via_steam_api()
    if steam_hit and steam_hit.appid:
        # Enrich name/platform from the library if we can.
        for g in library_games:
            if g.get("platform") == "steam" and str(g.get("appid")) == steam_hit.appid:
                steam_hit.name = g.get("name") or steam_hit.name
                break
        return steam_hit

    title_hit = detect_via_titles(library_games)
    if title_hit and title_hit.appid:
        return title_hit

    return Detection(appid=None, name=None, platform=None, source="none", raw=None)
