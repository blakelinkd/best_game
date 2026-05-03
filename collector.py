import json
import os
import threading
import time
from typing import Callable, Dict, List, Optional

from config import config
from history_store import HistoryStore
from twitch_client import TwitchClient


class Collector:
    def __init__(
        self,
        db_path: Optional[str] = None,
        cache_file: Optional[str] = None,
        interval: Optional[int] = None,
        retention_days: Optional[int] = None,
        can_collect: Optional[Callable[[], bool]] = None,
    ):
        self.db_path = db_path or config.HISTORY_DB_PATH
        self.cache_file = cache_file or os.path.join(config.CACHE_DIR, "viewer_cache.json")
        self.interval = interval if interval is not None else config.COLLECTION_INTERVAL
        self.retention_days = retention_days if retention_days is not None else config.HISTORY_RETENTION_DAYS
        self._can_collect = can_collect
        self._lock = threading.Lock()
        self._running = False
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._last_collection_time: Optional[float] = None
        self._next_collection_time: Optional[float] = None
        self._last_error: Optional[str] = None
        self._total_snapshots = 0
        self._store = HistoryStore(self.db_path)
        self._load_snapshot_count()

    def _load_snapshot_count(self):
        try:
            self._total_snapshots = self._store.snapshot_count()
        except Exception:
            self._total_snapshots = 0

    @property
    def is_running(self) -> bool:
        return self._running

    @property
    def last_collection_time(self) -> Optional[float]:
        return self._last_collection_time

    @property
    def total_snapshots(self) -> int:
        return self._total_snapshots

    def status(self) -> Dict:
        return {
            "running": self._running,
            "last_collection_time": self._last_collection_time,
            "next_collection_time": self._next_collection_time,
            "total_snapshots": self._total_snapshots,
            "last_error": self._last_error,
            "interval": self.interval,
        }

    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def stop(self):
        self._stop_event.set()

    def trigger_snapshot(self) -> Dict:
        if self._running:
            return {"success": False, "message": "A collection cycle is already running."}
        thread = threading.Thread(target=self._run_collection_cycle, daemon=True)
        thread.start()
        return {"success": True, "message": "Snapshot collection started."}

    def _collect_allowed(self) -> bool:
        if self._can_collect is not None:
            return self._can_collect()
        return True

    def _loop(self):
        self._store.cleanup_old(self.retention_days)
        while not self._stop_event.is_set():
            if self._collect_allowed():
                self._run_collection_cycle()
            self._next_collection_time = time.time() + self.interval
            self._stop_event.wait(self.interval)

    def _run_collection_cycle(self):
        acquired = self._lock.acquire(blocking=False)
        if not acquired:
            return
        self._running = True
        self._last_error = None
        try:
            twitch_games = self._read_twitch_games_from_cache()
            if not twitch_games:
                return
            game_ids = list(twitch_games.keys())
            if not game_ids:
                return
            twitch = TwitchClient()
            counts = twitch.get_game_viewer_counts(game_ids)
            now = time.time()
            snapshots = []
            for game_id, (viewer_count, stream_count) in counts.items():
                name = twitch_games.get(game_id, {}).get("name", game_id)
                snapshots.append({
                    "timestamp": now,
                    "twitch_game_id": str(game_id),
                    "twitch_game_name": name,
                    "viewer_count": viewer_count,
                    "stream_count": stream_count,
                })
            if snapshots:
                self._store.save_snapshots(snapshots)
                self._total_snapshots += len(snapshots)
                self._last_collection_time = now
        except Exception as e:
            self._last_error = str(e)
            print(f"[collector] Error during collection cycle: {e}")
        finally:
            self._running = False
            self._lock.release()

    def _read_twitch_games_from_cache(self) -> Dict[str, Dict]:
        cache_path = self.cache_file
        if not os.path.exists(cache_path):
            return {}
        try:
            with open(cache_path, "r", encoding="utf-8") as f:
                cache = json.load(f)
        except Exception:
            return {}
        twitch_games = cache.get("twitch_games", {})
        result = {}
        for key, entry in twitch_games.items():
            if isinstance(entry, dict) and entry.get("id"):
                result[str(entry["id"])] = entry
        return result
