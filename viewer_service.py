import json
import os
import re
import time
from typing import Callable, Dict, List, Optional, Set, Tuple
from urllib.parse import quote

from config import config
from steam_client import SteamClient
from twitch_client import TwitchClient
from platform_client import PlatformClient
from gog_client import GogClient
from epic_client import EpicClient

_job_progress = {}


def twitch_safe_tags(values: List[str], limit: int = 10) -> List[str]:
    tags = []
    seen = set()
    for value in values or []:
        words = re.findall(r"[A-Za-z0-9]+", str(value))
        tag = "".join(words).lower()[:25]
        key = tag.lower()
        if tag and key not in seen:
            tags.append(tag)
            seen.add(key)
        if len(tags) >= limit:
            break
    return tags


def calculate_discovery_score(viewer_count: int, stream_count: int) -> float:
    """
    Score a Twitch category by how discoverable it is for a small streamer.

    Optimized for streamers trying to grow from a few viewers — favors categories
    where they can land near the top of the directory and where there's enough
    audience that a click-through is plausible.
    """
    # Discoverability: full credit at <=20 active streams (you're on page 1),
    # decays to 0 by ~100 streams (effectively invisible to a casual browser).
    if stream_count <= 20:
        discoverability = 1.0
    else:
        discoverability = max(0.0, 1.0 - (stream_count - 20) / 80)

    # Demand: viewers per stream. 5+ viewers/stream is the target.
    viewers_per_stream = viewer_count / max(stream_count, 1)
    demand = min(viewers_per_stream / 5, 1.0)

    # Audience floor: zero out totally dead categories.
    audience_floor = 1.0 if viewer_count >= 3 else 0.0

    score = discoverability * 0.5 + demand * 0.4 + audience_floor * 0.1
    return round(score, 3)


class ViewerService:
    """Fetch owned Steam games and enrich them with cached Twitch viewer data."""

    def __init__(
        self,
        platform_clients: Optional[List[PlatformClient]] = None,
        twitch_client: Optional[TwitchClient] = None,
        cache_file: Optional[str] = None,
        legacy_cache_file: str = "game_cache.json",
        asset_cache_dir: Optional[str] = None,
    ):
        self._platform_clients = platform_clients or self._default_platform_clients()
        self._twitch_client = twitch_client
        self.project_root = os.path.dirname(os.path.abspath(__file__))
        self.cache_file = cache_file or os.path.join(config.CACHE_DIR, "viewer_cache.json")
        self.legacy_cache_file = legacy_cache_file
        self.asset_cache_dir = asset_cache_dir or config.ASSET_CACHE_DIR
        self.cache = self._load_cache()
        self.legacy_game_cache = self._load_legacy_game_cache()

    def _default_platform_clients(self) -> List[PlatformClient]:
        """Create default platform clients based on configuration."""
        clients: List[PlatformClient] = []
        
        # Always include Steam client for backward compatibility
        clients.append(SteamClient())
        
        # Add GOG client if enabled
        if config.GOG_ENABLED:
            clients.append(GogClient(db_path=config.GOG_DB_PATH))
        
        # Add Epic client if enabled
        if config.EPIC_ENABLED:
            clients.append(EpicClient(
                catalog_path=config.EPIC_CATALOG_PATH,
                installs_path=config.EPIC_INSTALLS_PATH
            ))
        
        return clients

    @property
    def platform_clients(self) -> List[PlatformClient]:
        return self._platform_clients

    @property
    def twitch_client(self) -> TwitchClient:
        if self._twitch_client is None:
            self._twitch_client = TwitchClient()
        return self._twitch_client

    @property
    def steam_client(self):
        for c in self._platform_clients:
            if c.platform_name == "steam":
                return c
        return self._platform_clients[0] if self._platform_clients else None

    def _absolute_path(self, path: str) -> str:
        if os.path.isabs(path):
            return path
        return os.path.join(self.project_root, path)

    def _empty_cache(self) -> Dict:
        return {
            "owned_games": None,
            "steam_metadata": {},
            "twitch_games": {},
            "viewer_counts": {},
            "missing_twitch_games": {},
        }

    def _load_cache(self) -> Dict:
        try:
            cache_path = self._absolute_path(self.cache_file)
            if os.path.exists(cache_path):
                with open(cache_path, "r", encoding="utf-8") as f:
                    cached = json.load(f)
                    base = self._empty_cache()
                    base.update(cached)
                    return base
        except Exception as e:
            print(f"Error loading cache: {e}")
        return self._empty_cache()

    def _load_legacy_game_cache(self) -> Dict:
        try:
            cache_path = self._absolute_path(self.legacy_cache_file)
            if os.path.exists(cache_path):
                with open(cache_path, "r", encoding="utf-8") as f:
                    return json.load(f)
        except Exception as e:
            print(f"Error loading legacy game cache: {e}")
        return {}

    def _save_cache(self):
        try:
            cache_path = self._absolute_path(self.cache_file)
            os.makedirs(os.path.dirname(cache_path), exist_ok=True)
            with open(cache_path, "w", encoding="utf-8") as f:
                json.dump(self.cache, f, indent=2)
        except Exception as e:
            print(f"Error saving cache: {e}")

    def _is_fresh(self, cached_at: Optional[float], ttl: int) -> bool:
        if not cached_at:
            return False
        return time.time() - cached_at < ttl

    def _legacy_cache_key(self, appid: str, game_name: str, platform: str = "steam") -> str:
        return f"{platform}_{appid}_{game_name}"

    def _twitch_cache_key(self, appid: str, platform: str = "steam") -> str:
        return f"{platform}_{appid}"

    def _get_owned_games(self, force_refresh: bool = False) -> List[Dict]:
        cached = self.cache.get("owned_games")
        if (
            not force_refresh
            and isinstance(cached, dict)
            and self._is_fresh(cached.get("cached_at"), config.OWNED_GAMES_CACHE_TTL)
        ):
            return cached.get("games", [])

        owned_games = []
        for client in self.platform_clients:
            try:
                platform_games = client.get_owned_games(force_refresh=force_refresh)
                owned_games.extend(platform_games)
                print(f"Found {len(platform_games)} games from {client.platform_name}")
            except Exception as e:
                print(f"Error fetching games from {client.platform_name}: {e}")
        
        self.cache["owned_games"] = {
            "cached_at": time.time(),
            "games": owned_games,
        }
        self._save_cache()
        return owned_games

    def _ordered_owned_games(self, force_refresh: bool = False) -> List[Dict]:
        owned_games = list(self._get_owned_games(force_refresh=force_refresh))
        
        # Get installed app IDs from each platform client
        platform_installed: Dict[str, Set[str]] = {}
        for client in self.platform_clients:
            try:
                installed = client.get_installed_appids()
                platform_installed[client.platform_name] = installed
            except Exception as e:
                print(f"Error getting installed apps from {client.platform_name}: {e}")
                platform_installed[client.platform_name] = set()

        for game in owned_games:
            platform = game.get("platform", "steam")
            appid = game.get("appid", "")
            
            # Check if installed on its platform
            game["installed"] = appid in platform_installed.get(platform, set())
            
            # Convert last_played to int
            game["last_played"] = int(game.get("rtime_last_played") or game.get("last_played") or 0)

        # Sort by last played (most recent first), then playtime, then name
        owned_games.sort(
            key=lambda game: (
                int(game.get("last_played") or 0),
                int(game.get("playtime_forever") or 0),
                str(game.get("name", "")).lower(),
            ),
            reverse=True,
        )
        return owned_games

    def can_render_from_cache(self, limit: Optional[int] = None, include_zero: bool = False) -> bool:
        cached_owned = self.cache.get("owned_games")
        if (
            not isinstance(cached_owned, dict)
            or not self._is_fresh(cached_owned.get("cached_at"), config.OWNED_GAMES_CACHE_TTL)
        ):
            return False

        owned_games = cached_owned.get("games", [])
        if limit is not None and limit > 0:
            owned_games = owned_games[:limit]

        for game in owned_games:
            appid = game.get("appid")
            name = game.get("name")
            platform = game.get("platform", "steam")
            if not appid or not name:
                continue

            twitch_game = self._get_cached_twitch_game(appid, name, platform)
            if not twitch_game or not twitch_game.get("id"):
                if not self._has_cached_missing_twitch_game(appid, platform):
                    return False
                continue

            viewer_count = self.cache.get("viewer_counts", {}).get(str(twitch_game["id"]))
            if (
                not isinstance(viewer_count, dict)
                or not self._is_fresh(viewer_count.get("cached_at"), config.VIEWER_COUNT_CACHE_TTL)
            ):
                return False

            if int(viewer_count.get("viewer_count", 0)) > 0 or include_zero:
                metadata = self.cache.get("steam_metadata", {}).get(str(appid))
                if (
                    not isinstance(metadata, dict)
                    or not self._is_fresh(metadata.get("cached_at"), config.OWNED_GAMES_CACHE_TTL)
                ):
                    return False

        return True

    def _get_cached_twitch_game(self, appid: str, game_name: str, platform: str = "steam") -> Optional[Dict]:
        cached = self.cache.get("twitch_games", {}).get(self._twitch_cache_key(appid, platform))
        if isinstance(cached, dict):
            return cached

        legacy = self.legacy_game_cache.get(self._legacy_cache_key(appid, game_name, platform))
        if isinstance(legacy, dict):
            self._cache_twitch_game(appid, legacy, platform)
            return legacy
        if isinstance(legacy, str) and legacy:
            migrated = {"name": legacy}
            self._cache_twitch_game(appid, migrated, platform)
            return migrated
        return None

    def _has_cached_missing_twitch_game(self, appid: str, platform: str = "steam") -> bool:
        return self._twitch_cache_key(appid, platform) in self.cache.get("missing_twitch_games", {})

    def _cache_twitch_game(self, appid: str, twitch_game: Optional[Dict], platform: str = "steam"):
        key = self._twitch_cache_key(appid, platform)
        if twitch_game:
            self.cache.setdefault("twitch_games", {})[key] = {
                "id": twitch_game.get("id"),
                "name": twitch_game.get("name"),
                "box_art_url": twitch_game.get("box_art_url"),
                "cached_at": time.time(),
            }
            self.cache.setdefault("missing_twitch_games", {}).pop(key, None)
        else:
            self.cache.setdefault("missing_twitch_games", {})[key] = {
                "cached_at": time.time(),
            }
        self._save_cache()

    def _match_twitch_game(self, steam_name: str, twitch_games: List[Dict]) -> Optional[Dict]:
        if not twitch_games:
            return None

        steam_lower = steam_name.lower()
        for game in twitch_games:
            if game.get("name", "").lower() == steam_lower:
                return game

        for game in twitch_games:
            twitch_lower = game.get("name", "").lower()
            if not twitch_lower:
                continue

            if steam_lower in twitch_lower or twitch_lower in steam_lower:
                return game

            common_words = {"the", "and", "of", "in", "to", "for"}
            steam_words = [w for w in steam_lower.split() if w not in common_words]
            twitch_words = [w for w in twitch_lower.split() if w not in common_words]
            overlap = set(steam_words) & set(twitch_words)
            if overlap and len(overlap) / max(len(steam_words), 1) >= config.GAME_NAME_MATCH_THRESHOLD:
                return game

        return None

    def _find_twitch_game(self, game: Dict, force_refresh: bool = False) -> Optional[Dict]:
        appid = game["appid"]
        game_name = game["name"]
        platform = game.get("platform", "steam")

        if not force_refresh:
            cached = self._get_cached_twitch_game(appid, game_name, platform)
            if cached and cached.get("id"):
                return cached
            if self._has_cached_missing_twitch_game(appid, platform):
                return None

        twitch_games = self.twitch_client.search_games(game_name)
        twitch_game = self._match_twitch_game(game_name, twitch_games)
        self._cache_twitch_game(appid, twitch_game, platform)
        return twitch_game

    def _get_viewer_count(self, twitch_game_id: str, force_refresh: bool = False) -> Tuple[int, int]:
        cached = self.cache.get("viewer_counts", {}).get(str(twitch_game_id))
        if (
            not force_refresh
            and isinstance(cached, dict)
            and self._is_fresh(cached.get("cached_at"), config.VIEWER_COUNT_CACHE_TTL)
        ):
            viewer_count = int(cached.get("viewer_count", 0))
            stream_count = int(cached.get("stream_count", 0))
            peak_viewer_count = int(cached.get("peak_viewer_count", viewer_count))
            return viewer_count, stream_count

        viewer_count, stream_count = self.twitch_client.get_game_viewer_count(twitch_game_id)
        peak_viewer_count = viewer_count  # default to current viewers
        self.cache.setdefault("viewer_counts", {})[str(twitch_game_id)] = {
            "cached_at": time.time(),
            "viewer_count": viewer_count,
            "stream_count": stream_count,
            "peak_viewer_count": peak_viewer_count,
        }
        self._save_cache()
        return viewer_count, stream_count

    def _get_viewer_counts(self, twitch_game_ids: List[str], force_refresh: bool = False) -> Dict[str, Tuple[int, int]]:
        viewer_stream_counts = {}
        stale_ids = []

        for twitch_game_id in twitch_game_ids:
            twitch_game_id = str(twitch_game_id)
            cached = self.cache.get("viewer_counts", {}).get(twitch_game_id)
            if (
                not force_refresh
                and isinstance(cached, dict)
                and self._is_fresh(cached.get("cached_at"), config.VIEWER_COUNT_CACHE_TTL)
            ):
                viewer_count = int(cached.get("viewer_count", 0))
                stream_count = int(cached.get("stream_count", 0))
                viewer_stream_counts[twitch_game_id] = (viewer_count, stream_count)
            else:
                stale_ids.append(twitch_game_id)

        if stale_ids:
            if hasattr(self.twitch_client, "get_game_viewer_counts"):
                fresh_counts = self.twitch_client.get_game_viewer_counts(stale_ids)
            else:
                fresh_counts = {
                    twitch_game_id: self.twitch_client.get_game_viewer_count(twitch_game_id)
                    for twitch_game_id in stale_ids
                }

            now = time.time()
            for twitch_game_id in stale_ids:
                fresh_data = fresh_counts.get(twitch_game_id)
                if isinstance(fresh_data, tuple):
                    viewer_count, stream_count = fresh_data
                else:
                    # fallback: fresh_data is int viewer count (old format)
                    viewer_count = int(fresh_data or 0)
                    stream_count = 0
                peak_viewer_count = viewer_count  # default
                viewer_stream_counts[twitch_game_id] = (viewer_count, stream_count)
                self.cache.setdefault("viewer_counts", {})[twitch_game_id] = {
                    "cached_at": now,
                    "viewer_count": viewer_count,
                    "stream_count": stream_count,
                    "peak_viewer_count": peak_viewer_count,
                }
            self._save_cache()

        return viewer_stream_counts

    def _get_platform_metadata(self, appid: str, platform: str = "steam", force_refresh: bool = False) -> Dict:
        cache_key = f"{platform}_{appid}"
        cached = self.cache.get("platform_metadata", {}).get(cache_key)
        if (
            not force_refresh
            and isinstance(cached, dict)
            and self._is_fresh(cached.get("cached_at"), config.OWNED_GAMES_CACHE_TTL)
        ):
            return cached

        # Find the platform client
        platform_client = None
        for client in self.platform_clients:
            if client.platform_name == platform:
                platform_client = client
                break
        
        if not platform_client:
            # Fall back to Steam client for backward compatibility
            platform_client = next((c for c in self.platform_clients if c.platform_name == "steam"), None)
            if not platform_client:
                return {
                    "cached_at": time.time(),
                    "genres": [],
                    "categories": [],
                    "tags": [],
                    "short_description": "",
                }

        metadata = platform_client.get_store_metadata(appid)
        cached_metadata = {
            "cached_at": time.time(),
            "genres": metadata.get("genres", []),
            "categories": metadata.get("categories", []),
            "tags": metadata.get("tags", []),
            "short_description": metadata.get("short_description", ""),
        }
        self.cache.setdefault("platform_metadata", {})[cache_key] = cached_metadata
        self._save_cache()
        return cached_metadata

    def _build_visible_game(self, game: Dict, twitch_game: Dict, viewer_count: int, force_refresh: bool = False, stream_count: Optional[int] = None, peak_viewer_count: Optional[int] = None) -> Dict:
        appid = game["appid"]
        name = game["name"]
        platform = game.get("platform", "steam")
        platform_metadata = self._get_platform_metadata(appid, platform, force_refresh=force_refresh)
        twitch_name = twitch_game.get("name") or name
        box_art_url = self._twitch_box_art_url(twitch_game.get("box_art_url"))
        platform_tags = twitch_safe_tags(platform_metadata.get("tags", []))
        last_played = int(game.get("last_played") or game.get("rtime_last_played") or 0)
        
        # If stream_count or peak_viewer_count not provided, fetch from cache
        if stream_count is None or peak_viewer_count is None:
            cached = self.cache.get("viewer_counts", {}).get(str(twitch_game["id"]))
            if isinstance(cached, dict):
                if stream_count is None:
                    stream_count = int(cached.get("stream_count", 0))
                if peak_viewer_count is None:
                    peak_viewer_count = int(cached.get("peak_viewer_count", viewer_count))
            else:
                if stream_count is None:
                    stream_count = 0
                if peak_viewer_count is None:
                    peak_viewer_count = viewer_count
        
        discovery_score = calculate_discovery_score(
            viewer_count=viewer_count,
            stream_count=stream_count,
        )
        
        return {
            "appid": appid,
            "name": name,
            "playtime_hours": round(game.get("playtime_forever", 0) / 60, 1),
            "viewer_count": viewer_count,
            "stream_count": stream_count,
            "peak_viewer_count": peak_viewer_count,
            "discovery_score": discovery_score,
            "twitch_game_id": twitch_game["id"],
            "twitch_name": twitch_name,
            "platform_tags": platform_tags,
            "platform_genres": platform_metadata.get("genres", []),
            "platform_categories": platform_metadata.get("categories", []),
            "platform_short_description": platform_metadata.get("short_description", ""),
            "installed": bool(game.get("installed")),
            "last_played": last_played,
            "twitch_box_art_url": self._cache_remote_image(
                box_art_url,
                self._asset_relative_path("twitch_box_art", f"{twitch_game['id']}.jpg"),
            ),
            "platform": platform,
            "platform_image_url": self._cache_platform_image(appid, platform),
            "platform_url": self._get_platform_store_url(appid, platform),
            "twitch_url": f"https://www.twitch.tv/directory/category/{quote(twitch_name)}",
            # Backward compatibility fields (will be deprecated)
            "steam_header_url": self._cache_platform_image(appid, platform) if platform == "steam" else None,
            "steam_url": self._get_platform_store_url(appid, platform) if platform == "steam" else None,
            "steam_tags": platform_tags if platform == "steam" else [],
            "steam_genres": platform_metadata.get("genres", []) if platform == "steam" else [],
            "steam_categories": platform_metadata.get("categories", []) if platform == "steam" else [],
            "steam_short_description": platform_metadata.get("short_description", "") if platform == "steam" else "",
        }

    def _get_platform_image_url(self, appid: str, platform: str = "steam") -> Optional[str]:
        """Get platform-specific image URL for a game."""
        for client in self.platform_clients:
            if client.platform_name == platform:
                url = client.get_image_url(appid)
                if url:
                    return url
        # Fallback for Steam
        if platform == "steam":
            try:
                appid_int = int(appid)
                return f"https://cdn.cloudflare.steamstatic.com/steam/apps/{appid_int}/header.jpg"
            except (TypeError, ValueError):
                pass
        return None

    def _get_platform_store_url(self, appid: str, platform: str = "steam") -> str:
        """Get platform-specific store URL for a game."""
        if platform == "steam":
            try:
                appid_int = int(appid)
                return f"https://store.steampowered.com/app/{appid_int}/"
            except (TypeError, ValueError):
                return f"https://store.steampowered.com/"
        elif platform == "gog":
            return f"https://www.gog.com/game/{appid}"
        elif platform == "epic":
            # Epic doesn't have public store URLs for games
            return f"https://store.epicgames.com/"
        else:
            return ""

    def _cached_static_path(self, relative_path: str) -> str:
        return "/" + relative_path.replace(os.sep, "/")

    def _asset_relative_path(self, *parts: str) -> str:
        return os.path.join(self.asset_cache_dir, *parts)

    def _cache_platform_image(self, appid: str, platform: str = "steam") -> Optional[str]:
        # Get platform-specific image URL
        image_url = self._get_platform_image_url(appid, platform)
        if not image_url:
            return None
        
        # Create platform-specific cache path
        relative_path = self._asset_relative_path(f"{platform}_images", f"{appid}.jpg")
        local_path = self._absolute_path(relative_path)

        if os.path.exists(local_path) and os.path.getsize(local_path) > 0:
            return self._cached_static_path(relative_path)

        try:
            os.makedirs(os.path.dirname(local_path), exist_ok=True)
            # Use Steam client's request method for now (it has rate limiting)
            response = self.steam_client._steam_store_get(
                image_url,
                headers={"User-Agent": "Mozilla/5.0"},
            )
            response.raise_for_status()
            content_type = response.headers.get("Content-Type", "")
            if content_type and not content_type.startswith("image/"):
                return image_url

            with open(local_path, "wb") as f:
                f.write(response.content)
            return self._cached_static_path(relative_path)
        except Exception as e:
            print(f"Could not cache {platform} image for appid {appid}: {e}")
            return image_url

    def _twitch_box_art_url(self, box_art_url: Optional[str]) -> Optional[str]:
        if not box_art_url:
            return None
        return box_art_url.replace("{width}", "285").replace("{height}", "380")

    def _cache_remote_image(self, url: Optional[str], relative_path: str) -> Optional[str]:
        if not url:
            return None

        local_path = self._absolute_path(relative_path)
        if os.path.exists(local_path) and os.path.getsize(local_path) > 0:
            return self._cached_static_path(relative_path)

        try:
            os.makedirs(os.path.dirname(local_path), exist_ok=True)
            response = self.steam_client._steam_store_get(url, headers={"User-Agent": "Mozilla/5.0"})
            response.raise_for_status()
            content_type = response.headers.get("Content-Type", "")
            if content_type and not content_type.startswith("image/"):
                return url

            with open(local_path, "wb") as f:
                f.write(response.content)
            return self._cached_static_path(relative_path)
        except Exception as e:
            print(f"Could not cache image {url}: {e}")
            return url

    def get_games_with_viewers(
        self,
        limit: Optional[int] = None,
        include_zero: bool = False,
        force_refresh: bool = False,
    ) -> Dict:
        owned_games = self._ordered_owned_games(force_refresh=force_refresh)
        total_owned = len(owned_games)

        if limit is not None and limit > 0:
            owned_games = owned_games[:limit]

        candidates = []
        processed = 0
        matched = 0

        for game in owned_games:
            appid = game.get("appid")
            name = game.get("name")
            if not appid or not name:
                continue

            processed += 1

            _job_progress["processed"] = processed
            _job_progress["total_owned"] = total_owned

            twitch_game = self._find_twitch_game(game, force_refresh=force_refresh)
            if not twitch_game or not twitch_game.get("id"):
                continue

            matched += 1
            candidates.append({
                "game": game,
                "appid": appid,
                "name": name,
                "twitch_game": twitch_game,
            })

        viewer_counts = self._get_viewer_counts(
            [candidate["twitch_game"]["id"] for candidate in candidates],
            force_refresh=force_refresh,
        )

        visible_games = []
        for candidate in candidates:
            game = candidate["game"]
            appid = candidate["appid"]
            name = candidate["name"]
            twitch_game = candidate["twitch_game"]
            viewer_data = viewer_counts.get(str(twitch_game["id"]), (0, 0))
            if isinstance(viewer_data, tuple):
                viewer_count, stream_count = viewer_data
            else:
                viewer_count = viewer_data
                stream_count = 0
            if viewer_count <= 0 and not include_zero:
                continue

            visible_games.append(self._build_visible_game(game, twitch_game, viewer_count, force_refresh=force_refresh, stream_count=stream_count))

        visible_games.sort(key=lambda g: g["discovery_score"], reverse=True)

        return {
            "games": visible_games,
            "total_owned": total_owned,
            "processed": processed,
            "matched": matched,
            "shown": len(visible_games),
            "limited": limit is not None,
            "limit": limit,
            "owned_cache_ttl": config.OWNED_GAMES_CACHE_TTL,
            "viewer_cache_ttl": config.VIEWER_COUNT_CACHE_TTL,
        }

    def process_games_incremental(
        self,
        include_zero: bool = False,
        force_refresh: bool = False,
        on_game: Optional[Callable[[Dict, Dict], None]] = None,
    ) -> Dict:
        owned_games = self._ordered_owned_games(force_refresh=force_refresh)
        total_owned = len(owned_games)
        processed = 0
        matched = 0
        visible_games = []

        stats = {
            "total_owned": total_owned,
            "processed": 0,
            "matched": 0,
            "shown": 0,
            "limited": False,
            "limit": None,
            "owned_cache_ttl": config.OWNED_GAMES_CACHE_TTL,
            "viewer_cache_ttl": config.VIEWER_COUNT_CACHE_TTL,
        }

        for game in owned_games:
            appid = game.get("appid")
            name = game.get("name")
            if not appid or not name:
                continue

            processed += 1
            stats["processed"] = processed
            _job_progress["processed"] = processed
            _job_progress["total_owned"] = total_owned

            twitch_game = self._find_twitch_game(game, force_refresh=force_refresh)
            if not twitch_game or not twitch_game.get("id"):
                if on_game:
                    on_game(None, stats)
                continue

            matched += 1
            stats["matched"] = matched
            viewer_count, stream_count = self._get_viewer_count(twitch_game["id"], force_refresh=force_refresh)
            if viewer_count <= 0 and not include_zero:
                if on_game:
                    on_game(None, stats)
                continue

            visible_game = self._build_visible_game(game, twitch_game, viewer_count, force_refresh=force_refresh, stream_count=stream_count)
            visible_games.append(visible_game)
            stats["shown"] = len(visible_games)
            if on_game:
                on_game(visible_game, stats)

        return {
            "games": visible_games,
            **stats,
        }

    @staticmethod
    def get_job_progress() -> Dict:
        return {
            "processed": _job_progress.get("processed", 0),
            "total_owned": _job_progress.get("total_owned", 0),
        }
