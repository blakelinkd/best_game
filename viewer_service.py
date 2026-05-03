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
from retro_client import create_retro_clients, is_retro_platform
from viewer_metrics import calculate_discovery_score as _calculate_discovery_score
from history_store import HistoryStore

_job_progress = {}
_TWITCH_SEARCH_BACKOFF_SCOPE = "twitch_search"
_VIEWER_STATS_BACKOFF_SCOPE = "viewer_stats"


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


def calculate_discovery_score(
    viewer_count: int,
    stream_count: int,
    adjusted_average_viewers_per_stream: Optional[float] = None,
    median_viewers_per_stream: Optional[float] = None,
    top_stream_viewer_share: Optional[float] = None,
) -> float:
    return _calculate_discovery_score(
        viewer_count=viewer_count,
        stream_count=stream_count,
        adjusted_average_viewers_per_stream=adjusted_average_viewers_per_stream,
        median_viewers_per_stream=median_viewers_per_stream,
        top_stream_viewer_share=top_stream_viewer_share,
    )


class ViewerService:
    """Fetch owned Steam games and enrich them with cached Twitch viewer data."""

    def __init__(
        self,
        platform_clients: Optional[List[PlatformClient]] = None,
        twitch_client: Optional[TwitchClient] = None,
        cache_file: Optional[str] = None,
        legacy_cache_file: str = "game_cache.json",
        asset_cache_dir: Optional[str] = None,
        scan_store: Optional[HistoryStore] = None,
    ):
        self._platform_clients = platform_clients or self._default_platform_clients()
        self._twitch_client = twitch_client
        self.project_root = os.path.dirname(os.path.abspath(__file__))
        self.cache_file = cache_file or os.path.join(config.CACHE_DIR, "viewer_cache.json")
        self.legacy_cache_file = legacy_cache_file
        self.asset_cache_dir = asset_cache_dir or config.ASSET_CACHE_DIR
        self.scan_store = scan_store or HistoryStore(config.HISTORY_DB_PATH)
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

        # Add retro console clients (one per system) if enabled.
        if config.RETRO_ENABLED:
            clients.extend(create_retro_clients(config.RETRO_SYSTEMS))

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
            "platform_metadata": {},
            "twitch_games": {},
            "viewer_counts": {},
            "game_tags": {},
            "missing_twitch_games": {},
            "steam_appid_lookup": {},
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

    def _backoff_scan_due(self, state: Optional[Dict], now: Optional[float] = None) -> bool:
        if not state:
            return True
        now = now or time.time()
        last_scanned_at = float(state.get("last_scanned_at") or 0)
        if not last_scanned_at:
            return True
        if now - last_scanned_at >= config.SCAN_BACKOFF_MAX_AGE:
            return True
        return int(state.get("skip_runs_remaining") or 0) <= 0

    def _game_scan_key(self, game: Dict) -> str:
        return self._twitch_cache_key(str(game.get("appid") or ""), game.get("platform", "steam"))

    def _record_scan_results(self, scope: str, results: List[Dict]) -> None:
        self.scan_store.record_scan_results(
            scope,
            results,
            max_skip_runs=config.SCAN_BACKOFF_MAX_SKIP_RUNS,
        )

    def _legacy_cache_key(self, appid: str, game_name: str, platform: str = "steam") -> str:
        return f"{platform}_{appid}_{game_name}"

    def _twitch_cache_key(self, appid: str, platform: str = "steam") -> str:
        return f"{platform}_{appid}"

    def _tag_cache_key(self, appid: str, platform: str = "steam") -> str:
        return f"{platform}_{appid}"

    def _unique_tags(self, *tag_lists: List[str]) -> List[str]:
        tags = []
        seen = set()
        for tag_list in tag_lists:
            for tag in twitch_safe_tags(tag_list or [], limit=100):
                if tag not in seen:
                    tags.append(tag)
                    seen.add(tag)
        return tags

    def _get_game_tags(self, appid: str, platform: str = "steam") -> Dict:
        key = self._tag_cache_key(appid, platform)
        tags = self.cache.setdefault("game_tags", {}).get(key)
        if isinstance(tags, dict):
            return tags
        tags = {"steam": [], "platform": [], "twitch": [], "ai": [], "cached_at": time.time()}
        self.cache.setdefault("game_tags", {})[key] = tags
        return tags

    def save_game_source_tags(self, appid: str, platform: str, source: str, tags: List[str]) -> List[str]:
        if source not in {"steam", "platform", "twitch", "ai"}:
            raise ValueError(f"Unsupported tag source: {source}")
        normalized = self._unique_tags(tags)
        record = self._get_game_tags(str(appid), platform or "steam")
        record[source] = normalized
        record["cached_at"] = time.time()
        self._save_cache()
        return normalized

    def get_game_generated_title(self, appid: str, platform: str = "steam") -> str:
        key = self._tag_cache_key(str(appid), platform or "steam")
        record = self.cache.get("generated_titles", {}).get(key)
        if isinstance(record, dict):
            return str(record.get("title") or "")
        return ""

    def save_game_generated_title(self, appid: str, platform: str, title: str) -> str:
        clean = (title or "").strip()
        if not clean:
            return ""
        key = self._tag_cache_key(str(appid), platform or "steam")
        store = self.cache.setdefault("generated_titles", {})
        store[key] = {"title": clean, "cached_at": time.time()}
        self._save_cache()
        return clean

    # Platforms whose ownership list is cheap enough to re-fetch on every
    # request so newly added games show up immediately. GOG/Epic/retro read
    # from local files; Steam costs a single Web API call (rate limit is 300
    # per 5 minutes), which is well within budget.
    _ALWAYS_REFRESH_PLATFORMS: Set[str] = {"gog", "epic", "steam"}

    def _get_owned_games(self, force_refresh: bool = False) -> List[Dict]:
        cached = self.cache.get("owned_games")
        cache_is_fresh = (
            not force_refresh
            and isinstance(cached, dict)
            and self._is_fresh(cached.get("cached_at"), config.OWNED_GAMES_CACHE_TTL)
        )

        if cache_is_fresh:
            cached_games: List[Dict] = cached.get("games", [])
            # Always re-fetch local-platform clients so that games purchased or
            # enabled after the cache was last written show up immediately.
            cached_keys = {(g.get("platform"), g.get("appid")) for g in cached_games}
            new_games: List[Dict] = []
            for client in self.platform_clients:
                always_refresh = (
                    client.platform_name in self._ALWAYS_REFRESH_PLATFORMS
                    or is_retro_platform(client.platform_name)
                )
                if not always_refresh:
                    continue
                try:
                    for game in client.get_owned_games(force_refresh=False):
                        key = (game.get("platform"), game.get("appid"))
                        if key not in cached_keys:
                            new_games.append(game)
                            cached_keys.add(key)
                except Exception as e:
                    print(f"Error refreshing {client.platform_name} games: {e}")
            if new_games:
                all_games = cached_games + new_games
                cached["games"] = all_games
                self._save_cache()
                return all_games
            return cached_games

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

        # Process the most relevant titles first across every platform. Retro
        # games stay in the same queue; they simply sort by whatever recency or
        # playtime data is available.
        owned_games.sort(key=lambda game: (
            -int(game.get("last_played") or 0),
            -int(game.get("playtime_forever") or 0),
            str(game.get("name", "")).lower(),
        ))
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

        now = time.time()
        search_states = self.scan_store.get_scan_backoffs(
            _TWITCH_SEARCH_BACKOFF_SCOPE,
            [self._game_scan_key(game) for game in owned_games],
        )
        cached_twitch_ids = []
        for game in owned_games:
            appid = game.get("appid")
            name = game.get("name")
            platform = game.get("platform", "steam")
            if not appid or not name:
                continue
            twitch_game = self._get_cached_twitch_game(appid, name, platform)
            if twitch_game and twitch_game.get("id"):
                cached_twitch_ids.append(str(twitch_game["id"]))
        viewer_states = self.scan_store.get_scan_backoffs(
            _VIEWER_STATS_BACKOFF_SCOPE,
            cached_twitch_ids,
        )

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
                if self._backoff_scan_due(search_states.get(self._game_scan_key(game)), now):
                    return False
                continue

            viewer_count = self.cache.get("viewer_counts", {}).get(str(twitch_game["id"]))
            if (
                not isinstance(viewer_count, dict)
                or not self._is_fresh(viewer_count.get("cached_at"), config.VIEWER_COUNT_CACHE_TTL)
            ):
                if not isinstance(viewer_count, dict):
                    return False
                has_no_audience = (
                    int(viewer_count.get("viewer_count", 0) or 0) <= 0
                    and int(viewer_count.get("stream_count", 0) or 0) <= 0
                )
                if not has_no_audience or self._backoff_scan_due(viewer_states.get(str(twitch_game["id"])), now):
                    return False

            if int(viewer_count.get("viewer_count", 0)) > 0 or include_zero:
                metadata = self.cache.get("platform_metadata", {}).get(f"{platform}_{appid}")
                if not metadata and platform == "steam":
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

    def _cache_twitch_game(self, appid: str, twitch_game: Optional[Dict], platform: str = "steam", save: bool = True):
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
        if save:
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

    def _find_twitch_game(
        self,
        game: Dict,
        force_refresh: bool = False,
        refresh_cached_missing: bool = False,
        save: bool = True,
    ) -> Optional[Dict]:
        appid = game["appid"]
        game_name = game["name"]
        platform = game.get("platform", "steam")

        if not force_refresh:
            cached = self._get_cached_twitch_game(appid, game_name, platform)
            if cached and cached.get("id"):
                return cached
            if self._has_cached_missing_twitch_game(appid, platform) and not refresh_cached_missing:
                return None

        twitch_games = self.twitch_client.search_games(game_name)
        twitch_game = self._match_twitch_game(game_name, twitch_games)
        self._cache_twitch_game(appid, twitch_game, platform, save=save)
        return twitch_game

    def _viewer_stats_from_data(self, data) -> Dict:
        if isinstance(data, dict):
            viewer_count = int(data.get("viewer_count", 0) or 0)
            stream_count = int(data.get("stream_count", 0) or 0)
            average = data.get("average_viewers_per_stream")
            adjusted_average = data.get("adjusted_average_viewers_per_stream")
            median = data.get("median_viewers_per_stream")
            top_count = data.get("top_stream_viewer_count")
            top_share = data.get("top_stream_viewer_share")
            adjusted_viewer_count = data.get("whale_adjusted_viewer_count")
            adjusted_stream_count = data.get("whale_adjusted_stream_count")
            twitch_tags = data.get("twitch_tags", [])
        elif isinstance(data, tuple):
            viewer_count, stream_count = data
            viewer_count = int(viewer_count or 0)
            stream_count = int(stream_count or 0)
            average = None
            adjusted_average = None
            median = None
            top_count = None
            top_share = None
            adjusted_viewer_count = None
            adjusted_stream_count = None
            twitch_tags = []
        else:
            viewer_count = int(data or 0)
            stream_count = 0
            average = None
            adjusted_average = None
            median = None
            top_count = None
            top_share = None
            adjusted_viewer_count = None
            adjusted_stream_count = None
            twitch_tags = []

        raw_average = viewer_count / max(stream_count, 1) if stream_count else 0.0
        return {
            "viewer_count": viewer_count,
            "stream_count": stream_count,
            "average_viewers_per_stream": round(float(average if average is not None else raw_average), 2),
            "adjusted_average_viewers_per_stream": round(float(adjusted_average if adjusted_average is not None else raw_average), 2),
            "median_viewers_per_stream": round(float(median if median is not None else raw_average), 2),
            "top_stream_viewer_count": int(top_count if top_count is not None else 0),
            "top_stream_viewer_share": round(float(top_share if top_share is not None else 0.0), 3),
            "whale_adjusted_viewer_count": int(adjusted_viewer_count if adjusted_viewer_count is not None else viewer_count),
            "whale_adjusted_stream_count": int(adjusted_stream_count if adjusted_stream_count is not None else stream_count),
            "twitch_tags": self._unique_tags(twitch_tags),
        }

    def _cache_viewer_stats(self, twitch_game_id: str, stats: Dict, cached_at: Optional[float] = None) -> None:
        viewer_count = int(stats.get("viewer_count", 0) or 0)
        self.cache.setdefault("viewer_counts", {})[str(twitch_game_id)] = {
            "cached_at": cached_at or time.time(),
            "viewer_count": viewer_count,
            "stream_count": int(stats.get("stream_count", 0) or 0),
            "peak_viewer_count": viewer_count,
            "average_viewers_per_stream": float(stats.get("average_viewers_per_stream", 0.0) or 0.0),
            "adjusted_average_viewers_per_stream": float(stats.get("adjusted_average_viewers_per_stream", 0.0) or 0.0),
            "median_viewers_per_stream": float(stats.get("median_viewers_per_stream", 0.0) or 0.0),
            "top_stream_viewer_count": int(stats.get("top_stream_viewer_count", 0) or 0),
            "top_stream_viewer_share": float(stats.get("top_stream_viewer_share", 0.0) or 0.0),
            "whale_adjusted_viewer_count": int(stats.get("whale_adjusted_viewer_count", viewer_count) or 0),
            "whale_adjusted_stream_count": int(stats.get("whale_adjusted_stream_count", stats.get("stream_count", 0)) or 0),
            "twitch_tags": self._unique_tags(stats.get("twitch_tags", [])),
        }

    def _get_viewer_count(self, twitch_game_id: str, force_refresh: bool = False) -> Tuple[int, int]:
        cached = self.cache.get("viewer_counts", {}).get(str(twitch_game_id))
        if (
            not force_refresh
            and isinstance(cached, dict)
            and self._is_fresh(cached.get("cached_at"), config.VIEWER_COUNT_CACHE_TTL)
        ):
            stats = self._viewer_stats_from_data(cached)
            return stats["viewer_count"], stats["stream_count"]

        if hasattr(self.twitch_client, "get_game_viewer_stats"):
            fresh_data = self.twitch_client.get_game_viewer_stats(twitch_game_id)
        else:
            fresh_data = self.twitch_client.get_game_viewer_count(twitch_game_id)
        stats = self._viewer_stats_from_data(fresh_data)
        self._cache_viewer_stats(str(twitch_game_id), stats)
        self._save_cache()
        return stats["viewer_count"], stats["stream_count"]

    def _get_viewer_counts(
        self,
        twitch_game_ids: List[str],
        force_refresh: bool = False,
        use_backoff: bool = False,
    ) -> Dict[str, Dict]:
        viewer_stream_counts = {}
        stale_ids = []
        now = time.time()
        viewer_states = {}
        skipped_ids = []
        if use_backoff and not force_refresh:
            viewer_states = self.scan_store.get_scan_backoffs(
                _VIEWER_STATS_BACKOFF_SCOPE,
                [str(twitch_game_id) for twitch_game_id in twitch_game_ids],
            )

        for twitch_game_id in twitch_game_ids:
            twitch_game_id = str(twitch_game_id)
            cached = self.cache.get("viewer_counts", {}).get(twitch_game_id)
            if (
                not force_refresh
                and isinstance(cached, dict)
                and self._is_fresh(cached.get("cached_at"), config.VIEWER_COUNT_CACHE_TTL)
            ):
                viewer_stream_counts[twitch_game_id] = self._viewer_stats_from_data(cached)
            else:
                if (
                    use_backoff
                    and not force_refresh
                    and isinstance(cached, dict)
                    and int(cached.get("viewer_count", 0) or 0) <= 0
                    and int(cached.get("stream_count", 0) or 0) <= 0
                    and not self._backoff_scan_due(viewer_states.get(twitch_game_id), now)
                ):
                    viewer_stream_counts[twitch_game_id] = self._viewer_stats_from_data(cached)
                    skipped_ids.append(twitch_game_id)
                    continue
                stale_ids.append(twitch_game_id)

        if skipped_ids:
            self.scan_store.decrement_scan_backoffs(_VIEWER_STATS_BACKOFF_SCOPE, skipped_ids)

        if stale_ids:
            if hasattr(self.twitch_client, "get_game_viewer_stats_for_games"):
                fresh_counts = self.twitch_client.get_game_viewer_stats_for_games(stale_ids)
            elif hasattr(self.twitch_client, "get_game_viewer_counts"):
                fresh_counts = self.twitch_client.get_game_viewer_counts(stale_ids)
            else:
                fresh_counts = {
                    twitch_game_id: self.twitch_client.get_game_viewer_count(twitch_game_id)
                    for twitch_game_id in stale_ids
                }

            now = time.time()
            scan_results = []
            for twitch_game_id in stale_ids:
                stats = self._viewer_stats_from_data(fresh_counts.get(twitch_game_id))
                viewer_stream_counts[twitch_game_id] = stats
                self._cache_viewer_stats(twitch_game_id, stats, cached_at=now)
                scan_results.append({
                    "cache_key": twitch_game_id,
                    "empty": int(stats.get("viewer_count", 0) or 0) <= 0 and int(stats.get("stream_count", 0) or 0) <= 0,
                    "last_result": f"{int(stats.get('viewer_count', 0) or 0)} viewers / {int(stats.get('stream_count', 0) or 0)} streams",
                })
            if use_backoff:
                self._record_scan_results(_VIEWER_STATS_BACKOFF_SCOPE, scan_results)
            self._save_cache()

        return viewer_stream_counts

    def _get_platform_metadata(self, appid: str, platform: str = "steam", force_refresh: bool = False) -> Dict:
        cache_key = f"{platform}_{appid}"
        cached = self.cache.get("platform_metadata", {}).get(cache_key)
        if (
            not force_refresh
            and isinstance(cached, dict)
            and self._is_fresh(cached.get("cached_at"), config.OWNED_GAMES_CACHE_TTL)
            and (
                platform != "steam"
                or all(key in cached for key in ("release_timestamp", "is_on_sale", "discount_percent"))
            )
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
                    "description": "",
                }

        metadata = platform_client.get_store_metadata(appid)
        cached_metadata = {
            "cached_at": time.time(),
            "genres": metadata.get("genres", []),
            "categories": metadata.get("categories", []),
            "tags": metadata.get("tags", []),
            "short_description": metadata.get("short_description", ""),
            "description": metadata.get("description", ""),
            "release_date": metadata.get("release_date", ""),
            "release_timestamp": int(metadata.get("release_timestamp", 0) or 0),
            "release_coming_soon": bool(metadata.get("release_coming_soon", False)),
            "is_on_sale": bool(metadata.get("is_on_sale", False)),
            "discount_percent": int(metadata.get("discount_percent", 0) or 0),
            "price_initial_formatted": metadata.get("price_initial_formatted", ""),
            "price_final_formatted": metadata.get("price_final_formatted", ""),
        }
        self.cache.setdefault("platform_metadata", {})[cache_key] = cached_metadata
        self._save_cache()
        return cached_metadata

    def _build_visible_game(self, game: Dict, twitch_game: Dict, viewer_count: int, force_refresh: bool = False, stream_count: Optional[int] = None, peak_viewer_count: Optional[int] = None, viewer_stats: Optional[Dict] = None) -> Dict:
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

        if viewer_stats is None:
            cached = self.cache.get("viewer_counts", {}).get(str(twitch_game["id"]))
            viewer_stats = self._viewer_stats_from_data(cached if isinstance(cached, dict) else (viewer_count, stream_count or 0))

        tag_source = "steam" if platform == "steam" else "platform"
        if platform_tags:
            self.save_game_source_tags(appid, platform, tag_source, platform_tags)
        twitch_tags = self._unique_tags(viewer_stats.get("twitch_tags", []))
        if twitch_tags:
            self.save_game_source_tags(appid, platform, "twitch", twitch_tags)
        source_tags = self._get_game_tags(appid, platform)
        twitch_tags = self._unique_tags(twitch_tags, source_tags.get("twitch", []))
        steam_tags = self._unique_tags(source_tags.get("steam", []))
        ai_tags = self._unique_tags(source_tags.get("ai", []))
        all_tags = self._unique_tags(ai_tags, twitch_tags, steam_tags, platform_tags, source_tags.get("platform", []))
        
        discovery_score = calculate_discovery_score(
            viewer_count=viewer_count,
            stream_count=stream_count,
            adjusted_average_viewers_per_stream=viewer_stats.get("adjusted_average_viewers_per_stream"),
            median_viewers_per_stream=viewer_stats.get("median_viewers_per_stream"),
            top_stream_viewer_share=viewer_stats.get("top_stream_viewer_share"),
        )
        
        return {
            "appid": appid,
            "name": name,
            "playtime_hours": round(game.get("playtime_forever", 0) / 60, 1),
            "viewer_count": viewer_count,
            "stream_count": stream_count,
            "peak_viewer_count": peak_viewer_count,
            "average_viewers_per_stream": viewer_stats.get("average_viewers_per_stream", 0.0),
            "adjusted_average_viewers_per_stream": viewer_stats.get("adjusted_average_viewers_per_stream", 0.0),
            "median_viewers_per_stream": viewer_stats.get("median_viewers_per_stream", 0.0),
            "top_stream_viewer_count": viewer_stats.get("top_stream_viewer_count", 0),
            "top_stream_viewer_share": viewer_stats.get("top_stream_viewer_share", 0.0),
            "whale_adjusted_viewer_count": viewer_stats.get("whale_adjusted_viewer_count", viewer_count),
            "whale_adjusted_stream_count": viewer_stats.get("whale_adjusted_stream_count", stream_count),
            "discovery_score": discovery_score,
            "twitch_game_id": twitch_game["id"],
            "twitch_name": twitch_name,
            "platform_tags": platform_tags,
            "twitch_tags": twitch_tags,
            "ai_tags": ai_tags,
            "all_tags": all_tags,
            "platform_genres": platform_metadata.get("genres", []),
            "platform_categories": platform_metadata.get("categories", []),
            "platform_short_description": platform_metadata.get("short_description", ""),
            "platform_description": platform_metadata.get("description", "") or platform_metadata.get("short_description", ""),
            "release_date": platform_metadata.get("release_date", ""),
            "release_timestamp": int(platform_metadata.get("release_timestamp", 0) or 0),
            "release_coming_soon": bool(platform_metadata.get("release_coming_soon", False)),
            "is_on_sale": bool(platform_metadata.get("is_on_sale", False)),
            "discount_percent": int(platform_metadata.get("discount_percent", 0) or 0),
            "price_initial_formatted": platform_metadata.get("price_initial_formatted", ""),
            "price_final_formatted": platform_metadata.get("price_final_formatted", ""),
            "installed": bool(game.get("installed")),
            "last_played": last_played,
            "twitch_box_art_url": self._cache_remote_image(
                box_art_url,
                self._asset_relative_path("twitch_box_art", f"{twitch_game['id']}.jpg"),
                force_refresh=force_refresh,
            ),
            "platform": platform,
            "platform_image_url": self._cache_platform_image(appid, platform, game_name=name, force_refresh=force_refresh),
            "platform_url": self._get_platform_store_url(appid, platform),
            "generated_title": self.get_game_generated_title(appid, platform),
            "twitch_url": f"https://www.twitch.tv/directory/category/{quote(twitch_name)}",
            # Backward compatibility fields (will be deprecated)
            "steam_header_url": self._cache_platform_image(appid, platform, force_refresh=force_refresh) if platform == "steam" else None,
            "steam_url": self._get_platform_store_url(appid, platform) if platform == "steam" else None,
            "steam_tags": steam_tags if platform == "steam" else [],
            "steam_genres": platform_metadata.get("genres", []) if platform == "steam" else [],
            "steam_categories": platform_metadata.get("categories", []) if platform == "steam" else [],
            "steam_short_description": platform_metadata.get("short_description", "") if platform == "steam" else "",
            "steam_description": (platform_metadata.get("description", "") or platform_metadata.get("short_description", "")) if platform == "steam" else "",
        }

    def _find_steam_appid_for_game(self, game_name: str) -> Optional[int]:
        """Return a Steam appid for a non-Steam game by name. Cached; 0 means already searched, not found."""
        lookup = self.cache.setdefault("steam_appid_lookup", {})
        if game_name in lookup:
            return lookup[game_name] or None
        steam_appid = None
        if self.steam_client and hasattr(self.steam_client, "find_appid_by_name"):
            steam_appid = self.steam_client.find_appid_by_name(game_name)
        lookup[game_name] = steam_appid or 0
        self._save_cache()
        return steam_appid

    def _get_platform_image_url(self, appid: str, platform: str = "steam", game_name: Optional[str] = None) -> Optional[str]:
        """Get image URL for a game. For non-Steam games, prefers the Steam CDN header image."""
        # Retro consoles ship their own libretro thumbnails; skip the Steam CDN
        # short-circuit so we don't fire ~13K Steam API lookups on first load.
        if platform != "steam" and game_name and not is_retro_platform(platform):
            steam_appid = self._find_steam_appid_for_game(game_name)
            if steam_appid:
                return f"https://cdn.cloudflare.steamstatic.com/steam/apps/{steam_appid}/header.jpg"

        for client in self.platform_clients:
            if client.platform_name == platform:
                url = client.get_image_url(appid)
                if url:
                    return url

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

    def _cache_platform_image(self, appid: str, platform: str = "steam", game_name: Optional[str] = None, force_refresh: bool = False) -> Optional[str]:
        image_url = self._get_platform_image_url(appid, platform, game_name=game_name)
        if not image_url:
            return None

        # Libretro hosts thousands of retro thumbnails; serving them directly
        # from their CDN avoids ballooning static/cache/ to multi-GB sizes.
        if image_url and "thumbnails.libretro.com" in image_url:
            return image_url

        # Create platform-specific cache path
        relative_path = self._asset_relative_path(f"{platform}_images", f"{appid}.jpg")
        local_path = self._absolute_path(relative_path)
        missing_path = local_path + ".missing"

        cached_paths = [(relative_path, local_path)]
        if platform == "steam":
            legacy_relative_path = self._asset_relative_path("steam_headers", f"{appid}.jpg")
            legacy_local_path = self._absolute_path(legacy_relative_path)
            cached_paths.append((legacy_relative_path, legacy_local_path))
            if force_refresh and os.path.exists(legacy_local_path + ".missing"):
                os.remove(legacy_local_path + ".missing")

        for cached_relative_path, cached_local_path in cached_paths:
            if os.path.exists(cached_local_path) and os.path.getsize(cached_local_path) > 0:
                return self._cached_static_path(cached_relative_path)

        if force_refresh and os.path.exists(missing_path):
            os.remove(missing_path)

        if os.path.exists(missing_path) and not force_refresh:
            return None

        response = None
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
                print(f"Skipping non-image {platform} image for appid {appid}: {content_type}")
                return None

            with open(local_path, "wb") as f:
                f.write(response.content)
            return self._cached_static_path(relative_path)
        except Exception as e:
            status_code = getattr(getattr(e, "response", None), "status_code", None)
            if status_code is None and response is not None:
                status_code = getattr(response, "status_code", None)
            if status_code == 404:
                print(f"No {platform} image found for appid {appid}: {image_url}")
                self._write_missing_sentinel(missing_path)
                return None
            print(f"Could not cache {platform} image for appid {appid}: {e}")
            return image_url

    @staticmethod
    def _write_missing_sentinel(path: str) -> None:
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w") as f:
                f.write("404")
        except OSError:
            pass

    def _twitch_box_art_url(self, box_art_url: Optional[str]) -> Optional[str]:
        if not box_art_url:
            return None
        return box_art_url.replace("{width}", "285").replace("{height}", "380")

    def _cache_remote_image(self, url: Optional[str], relative_path: str, force_refresh: bool = False) -> Optional[str]:
        if not url:
            return None

        local_path = self._absolute_path(relative_path)
        missing_path = local_path + ".missing"
        if os.path.exists(local_path) and os.path.getsize(local_path) > 0:
            return self._cached_static_path(relative_path)

        if force_refresh and os.path.exists(missing_path):
            os.remove(missing_path)

        if os.path.exists(missing_path) and not force_refresh:
            return None

        response = None
        try:
            os.makedirs(os.path.dirname(local_path), exist_ok=True)
            response = self.steam_client._steam_store_get(url, headers={"User-Agent": "Mozilla/5.0"})
            response.raise_for_status()
            content_type = response.headers.get("Content-Type", "")
            if content_type and not content_type.startswith("image/"):
                print(f"Skipping non-image remote asset {url}: {content_type}")
                return None

            with open(local_path, "wb") as f:
                f.write(response.content)
            return self._cached_static_path(relative_path)
        except Exception as e:
            status_code = getattr(getattr(e, "response", None), "status_code", None)
            if status_code is None and response is not None:
                status_code = getattr(response, "status_code", None)
            if status_code == 404:
                print(f"No remote image found at {url}")
                self._write_missing_sentinel(missing_path)
                return None
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

        platform_owned: Dict[str, int] = {}
        for g in owned_games:
            p = g.get("platform", "steam")
            platform_owned[p] = platform_owned.get(p, 0) + 1

        if limit is not None and limit > 0:
            owned_games = owned_games[:limit]

        candidates = []
        processed = 0
        matched = 0
        platform_processed: Dict[str, int] = {}
        platform_matched: Dict[str, int] = {}

        for game in owned_games:
            appid = game.get("appid")
            name = game.get("name")
            if not appid or not name:
                continue

            processed += 1
            p = game.get("platform", "steam")
            platform_processed[p] = platform_processed.get(p, 0) + 1

            _job_progress["processed"] = processed
            _job_progress["total_owned"] = total_owned

            twitch_game = self._find_twitch_game(game, force_refresh=force_refresh)
            if not twitch_game or not twitch_game.get("id"):
                continue

            matched += 1
            platform_matched[p] = platform_matched.get(p, 0) + 1
            candidates.append({
                "game": game,
                "appid": appid,
                "name": name,
                "twitch_game": twitch_game,
            })

        viewer_counts = self._get_viewer_counts(
            [candidate["twitch_game"]["id"] for candidate in candidates],
            force_refresh=force_refresh,
            use_backoff=True,
        )

        visible_games = []
        platform_shown: Dict[str, int] = {}
        for candidate in candidates:
            game = candidate["game"]
            appid = candidate["appid"]
            name = candidate["name"]
            twitch_game = candidate["twitch_game"]
            viewer_stats = self._viewer_stats_from_data(viewer_counts.get(str(twitch_game["id"])))
            viewer_count = viewer_stats["viewer_count"]
            stream_count = viewer_stats["stream_count"]
            if viewer_count <= 0 and not include_zero:
                continue

            visible_games.append(self._build_visible_game(game, twitch_game, viewer_count, force_refresh=force_refresh, stream_count=stream_count, viewer_stats=viewer_stats))
            p = game.get("platform", "steam")
            platform_shown[p] = platform_shown.get(p, 0) + 1

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
            "platform_owned": platform_owned,
            "platform_processed": platform_processed,
            "platform_matched": platform_matched,
            "platform_shown": platform_shown,
        }

    def process_games_incremental(
        self,
        include_zero: bool = False,
        force_refresh: bool = False,
        deferred_platforms: Optional[Set[str]] = None,
        deferred_platforms_getter: Optional[Callable[[], Set[str]]] = None,
        on_game: Optional[Callable[[Dict, Dict], None]] = None,
    ) -> Dict:
        owned_games = self._ordered_owned_games(force_refresh=force_refresh)
        total_owned = len(owned_games)
        processed = 0
        matched = 0
        visible_games = []

        platform_owned: Dict[str, int] = {}
        for g in owned_games:
            p = g.get("platform", "steam")
            platform_owned[p] = platform_owned.get(p, 0) + 1

        platform_processed: Dict[str, int] = {}
        platform_matched: Dict[str, int] = {}
        platform_shown: Dict[str, int] = {}

        stats = {
            "total_owned": total_owned,
            "processed": 0,
            "matched": 0,
            "shown": 0,
            "limited": False,
            "limit": None,
            "owned_cache_ttl": config.OWNED_GAMES_CACHE_TTL,
            "viewer_cache_ttl": config.VIEWER_COUNT_CACHE_TTL,
            "platform_owned": platform_owned,
            "platform_processed": {},
            "platform_matched": {},
            "platform_shown": {},
        }

        search_states = self.scan_store.get_scan_backoffs(
            _TWITCH_SEARCH_BACKOFF_SCOPE,
            [self._game_scan_key(game) for game in owned_games],
        )
        skipped_search_keys = []
        search_scan_results = []
        twitch_cache_dirty = False
        candidate_batch = []
        now = time.time()

        def current_deferred_platforms() -> Set[str]:
            platforms = deferred_platforms_getter() if deferred_platforms_getter else deferred_platforms
            return {str(platform).strip().lower() for platform in (platforms or set()) if str(platform).strip()}

        def pop_next_game(pending_games: List[Dict]) -> Dict:
            deferred = current_deferred_platforms()
            if deferred:
                for index, candidate in enumerate(pending_games):
                    if candidate.get("platform", "steam") not in deferred:
                        return pending_games.pop(index)
            return pending_games.pop(0)

        def flush_candidate_batch(force: bool = False) -> None:
            nonlocal candidate_batch, visible_games, platform_shown, stats
            if not candidate_batch:
                return

            batch = candidate_batch
            if not force:
                deferred = current_deferred_platforms()
                if deferred:
                    active_batch = [
                        candidate
                        for candidate in candidate_batch
                        if candidate["game"].get("platform", "steam") not in deferred
                    ]
                    if active_batch:
                        candidate_batch = [
                            candidate
                            for candidate in candidate_batch
                            if candidate["game"].get("platform", "steam") in deferred
                        ]
                        batch = active_batch
                    elif any(game.get("platform", "steam") not in deferred for game in pending_games):
                        return

            viewer_counts = self._get_viewer_counts(
                [candidate["twitch_game"]["id"] for candidate in batch],
                force_refresh=force_refresh,
                use_backoff=True,
            )

            for candidate in batch:
                game = candidate["game"]
                twitch_game = candidate["twitch_game"]
                p = game.get("platform", "steam")
                viewer_stats = self._viewer_stats_from_data(viewer_counts.get(str(twitch_game["id"])))
                viewer_count = viewer_stats["viewer_count"]
                stream_count = viewer_stats["stream_count"]
                if viewer_count <= 0 and not include_zero:
                    if on_game:
                        on_game(None, stats)
                    continue

                visible_game = self._build_visible_game(
                    game,
                    twitch_game,
                    viewer_count,
                    force_refresh=force_refresh,
                    stream_count=stream_count,
                    viewer_stats=viewer_stats,
                )
                visible_games.append(visible_game)
                platform_shown[p] = platform_shown.get(p, 0) + 1
                stats["shown"] = len(visible_games)
                stats["platform_shown"] = dict(platform_shown)
                if on_game:
                    on_game(visible_game, stats)

            if batch is candidate_batch:
                candidate_batch = []

        pending_games = list(owned_games)
        while pending_games:
            game = pop_next_game(pending_games)
            deferred_now = current_deferred_platforms()
            if (
                deferred_now
                and game.get("platform", "steam") in deferred_now
                and any(candidate["game"].get("platform", "steam") not in deferred_now for candidate in candidate_batch)
            ):
                flush_candidate_batch()

            appid = game.get("appid")
            name = game.get("name")
            if not appid or not name:
                continue

            processed += 1
            p = game.get("platform", "steam")
            platform_processed[p] = platform_processed.get(p, 0) + 1
            stats["processed"] = processed
            stats["platform_processed"] = dict(platform_processed)
            _job_progress["processed"] = processed
            _job_progress["total_owned"] = total_owned

            scan_key = self._game_scan_key(game)
            cached_twitch_game = None if force_refresh else self._get_cached_twitch_game(appid, name, p)
            has_cached_missing = self._has_cached_missing_twitch_game(appid, p)
            should_search = (
                force_refresh
                or not (cached_twitch_game and cached_twitch_game.get("id"))
                and (
                    not has_cached_missing
                    or self._backoff_scan_due(search_states.get(scan_key), now)
                )
            )

            if cached_twitch_game and cached_twitch_game.get("id") and not force_refresh:
                twitch_game = cached_twitch_game
            elif should_search:
                twitch_game = self._find_twitch_game(
                    game,
                    force_refresh=force_refresh,
                    refresh_cached_missing=True,
                    save=False,
                )
                twitch_cache_dirty = True
                search_scan_results.append({
                    "cache_key": scan_key,
                    "empty": not (twitch_game and twitch_game.get("id")),
                    "last_result": (twitch_game or {}).get("name") or "no_match",
                })
                if len(search_scan_results) >= 250:
                    self._record_scan_results(_TWITCH_SEARCH_BACKOFF_SCOPE, search_scan_results)
                    search_scan_results = []
                    self._save_cache()
                    twitch_cache_dirty = False
            else:
                twitch_game = None
                skipped_search_keys.append(scan_key)

            if not twitch_game or not twitch_game.get("id"):
                if on_game:
                    on_game(None, stats)
                continue

            matched += 1
            platform_matched[p] = platform_matched.get(p, 0) + 1
            stats["matched"] = matched
            stats["platform_matched"] = dict(platform_matched)
            candidate_batch.append({
                "game": game,
                "twitch_game": twitch_game,
            })
            if len(candidate_batch) >= 50:
                flush_candidate_batch()

        if search_scan_results:
            self._record_scan_results(_TWITCH_SEARCH_BACKOFF_SCOPE, search_scan_results)
        if skipped_search_keys:
            self.scan_store.decrement_scan_backoffs(_TWITCH_SEARCH_BACKOFF_SCOPE, skipped_search_keys)
        if twitch_cache_dirty:
            self._save_cache()

        flush_candidate_batch(force=True)

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
