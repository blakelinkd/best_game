import sqlite3
import time
import os
from typing import Dict, List, Optional
from viewer_metrics import calculate_discovery_score


def _discovery_score(viewer_count: int, stream_count: int) -> float:
    return calculate_discovery_score(viewer_count, stream_count)


class HistoryStore:
    def __init__(self, db_path: str):
        self.db_path = os.path.abspath(db_path)
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=5000")
        return conn

    def _init_db(self):
        with self._connect() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS viewer_snapshots (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp REAL NOT NULL,
                    twitch_game_id TEXT NOT NULL,
                    twitch_game_name TEXT,
                    viewer_count INTEGER NOT NULL DEFAULT 0,
                    stream_count INTEGER NOT NULL DEFAULT 0
                )
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_snapshots_game_time
                ON viewer_snapshots(twitch_game_id, timestamp)
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS scan_backoff (
                    scope TEXT NOT NULL,
                    cache_key TEXT NOT NULL,
                    last_scanned_at REAL NOT NULL,
                    zero_streak INTEGER NOT NULL DEFAULT 0,
                    skip_runs_remaining INTEGER NOT NULL DEFAULT 0,
                    last_result TEXT,
                    updated_at REAL NOT NULL,
                    PRIMARY KEY (scope, cache_key)
                )
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_scan_backoff_scope
                ON scan_backoff(scope, cache_key)
            """)
            conn.commit()

    def get_scan_backoffs(self, scope: str, cache_keys: List[str]) -> Dict[str, Dict]:
        keys = [str(k) for k in cache_keys if k]
        if not keys:
            return {}
        result: Dict[str, Dict] = {}
        with self._connect() as conn:
            for offset in range(0, len(keys), 500):
                chunk = keys[offset:offset + 500]
                placeholders = ",".join("?" for _ in chunk)
                rows = conn.execute(
                    "SELECT cache_key, last_scanned_at, zero_streak, skip_runs_remaining, last_result "
                    "FROM scan_backoff "
                    f"WHERE scope = ? AND cache_key IN ({placeholders})",
                    [scope, *chunk],
                ).fetchall()
                for row in rows:
                    result[row[0]] = {
                        "last_scanned_at": float(row[1] or 0),
                        "zero_streak": int(row[2] or 0),
                        "skip_runs_remaining": int(row[3] or 0),
                        "last_result": row[4] or "",
                    }
        return result

    def decrement_scan_backoffs(self, scope: str, cache_keys: List[str]):
        keys = [str(k) for k in cache_keys if k]
        if not keys:
            return
        with self._connect() as conn:
            for offset in range(0, len(keys), 500):
                chunk = keys[offset:offset + 500]
                placeholders = ",".join("?" for _ in chunk)
                conn.execute(
                    "UPDATE scan_backoff "
                    "SET skip_runs_remaining = CASE "
                    "WHEN skip_runs_remaining > 0 THEN skip_runs_remaining - 1 ELSE 0 END, "
                    "updated_at = ? "
                    f"WHERE scope = ? AND cache_key IN ({placeholders})",
                    [time.time(), scope, *chunk],
                )
            conn.commit()

    def record_scan_results(
        self,
        scope: str,
        results: List[Dict],
        max_skip_runs: int = 64,
    ):
        if not results:
            return
        now = time.time()
        keys = [str(r.get("cache_key") or "") for r in results if r.get("cache_key")]
        existing = self.get_scan_backoffs(scope, keys)
        deletes = []
        upserts = []
        for result in results:
            cache_key = str(result.get("cache_key") or "")
            if not cache_key:
                continue
            is_empty = bool(result.get("empty"))
            if not is_empty:
                deletes.append((scope, cache_key))
                continue
            previous = existing.get(cache_key, {})
            zero_streak = int(previous.get("zero_streak") or 0) + 1
            skip_runs = min(max(1, 2 ** (zero_streak - 1)), max_skip_runs)
            upserts.append((
                scope,
                cache_key,
                now,
                zero_streak,
                skip_runs,
                str(result.get("last_result") or "empty"),
                now,
            ))

        with self._connect() as conn:
            if deletes:
                conn.executemany(
                    "DELETE FROM scan_backoff WHERE scope = ? AND cache_key = ?",
                    deletes,
                )
            if upserts:
                conn.executemany(
                    "INSERT INTO scan_backoff "
                    "(scope, cache_key, last_scanned_at, zero_streak, skip_runs_remaining, last_result, updated_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?) "
                    "ON CONFLICT(scope, cache_key) DO UPDATE SET "
                    "last_scanned_at = excluded.last_scanned_at, "
                    "zero_streak = excluded.zero_streak, "
                    "skip_runs_remaining = excluded.skip_runs_remaining, "
                    "last_result = excluded.last_result, "
                    "updated_at = excluded.updated_at",
                    upserts,
                )
            conn.commit()

    def save_snapshots(self, snapshots: List[Dict]):
        if not snapshots:
            return
        with self._connect() as conn:
            rows = [
                (
                    s["timestamp"],
                    s["twitch_game_id"],
                    s.get("twitch_game_name", ""),
                    s["viewer_count"],
                    s["stream_count"],
                )
                for s in snapshots
            ]
            conn.executemany(
                "INSERT INTO viewer_snapshots (timestamp, twitch_game_id, twitch_game_name, viewer_count, stream_count) "
                "VALUES (?, ?, ?, ?, ?)",
                rows,
            )
            conn.commit()

    def get_series(self, game_id: str, days: int = 7) -> List[Dict]:
        cutoff = time.time() - days * 86400
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT timestamp, viewer_count, stream_count "
                "FROM viewer_snapshots "
                "WHERE twitch_game_id = ? AND timestamp >= ? "
                "ORDER BY timestamp ASC",
                (str(game_id), cutoff),
            ).fetchall()
        return [
            {"timestamp": row[0], "viewer_count": row[1], "stream_count": row[2]}
            for row in rows
        ]

    def get_heatmap(self, game_id: str, days: int = 30) -> Dict:
        cutoff = time.time() - days * 86400
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT timestamp, viewer_count, stream_count "
                "FROM viewer_snapshots "
                "WHERE twitch_game_id = ? AND timestamp >= ?",
                (str(game_id), cutoff),
            ).fetchall()

        DAY_NAMES = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
        cells = [[None] * 24 for _ in range(7)]
        buckets = [[{"viewers": 0, "streams": 0, "count": 0}] * 24 for _ in range(7)]

        for ts, vc, sc in rows:
            lt = time.localtime(ts)
            dow = lt.tm_wday  # 0=Mon..6=Sun
            hour = lt.tm_hour
            bucket = buckets[dow][hour]
            buckets[dow][hour] = {
                "viewers": bucket["viewers"] + vc,
                "streams": bucket["streams"] + sc,
                "count": bucket["count"] + 1,
            }

        for d in range(7):
            for h in range(24):
                b = buckets[d][h]
                if b["count"] > 0:
                    avg_v = b["viewers"] / b["count"]
                    avg_s = b["streams"] / b["count"]
                    score = _discovery_score(int(avg_v), int(avg_s))
                    cells[d][h] = {
                        "avg_viewers": round(avg_v, 1),
                        "avg_streams": round(avg_s, 1),
                        "avg_discovery": score,
                        "count": b["count"],
                    }

        return {
            "days": DAY_NAMES,
            "hours": list(range(24)),
            "cells": cells,
            "game_id": game_id,
            "period_days": days,
        }

    def get_tracked_games(self) -> List[Dict]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT twitch_game_id, twitch_game_name, "
                "MIN(timestamp), MAX(timestamp), COUNT(*) "
                "FROM viewer_snapshots "
                "GROUP BY twitch_game_id "
                "ORDER BY COUNT(*) DESC"
            ).fetchall()
        return [
            {
                "twitch_game_id": row[0],
                "twitch_game_name": row[1] or row[0],
                "first_ts": row[2],
                "last_ts": row[3],
                "snapshot_count": row[4],
            }
            for row in rows
        ]

    def get_opportunities(
        self,
        min_peak: int = 50,
        max_avg_viewers_per_stream: float = 200.0,
        min_live_fraction: float = 0.1,
        min_snapshots: int = 30,
        sort_by: str = "avg_viewers_when_live",
        limit: int = 50,
    ) -> List[Dict]:
        """Games whose latest snapshot has 0 streams but have historical demand.

        Filters out whale-driven categories where the audience comes for one
        specific creator (high viewers-per-stream) rather than the directory.
        """
        with self._connect() as conn:
            latest_rows = conn.execute(
                "SELECT vs.twitch_game_id, vs.twitch_game_name, vs.timestamp, "
                "vs.viewer_count, vs.stream_count "
                "FROM viewer_snapshots vs "
                "JOIN (SELECT twitch_game_id, MAX(timestamp) AS max_ts "
                "      FROM viewer_snapshots GROUP BY twitch_game_id) latest "
                "ON vs.twitch_game_id = latest.twitch_game_id "
                "AND vs.timestamp = latest.max_ts "
                "WHERE vs.stream_count = 0"
            ).fetchall()

            candidates: List[Dict] = []
            for game_id, name, latest_ts, _, _ in latest_rows:
                rows = conn.execute(
                    "SELECT viewer_count, stream_count FROM viewer_snapshots "
                    "WHERE twitch_game_id = ?",
                    (str(game_id),),
                ).fetchall()
                total = len(rows)
                if total < min_snapshots:
                    continue
                live = [(v, s) for v, s in rows if s > 0]
                if not live:
                    continue
                live_count = len(live)
                live_fraction = live_count / total
                if live_fraction < min_live_fraction:
                    continue
                peak = max(v for v, _ in live)
                if peak < min_peak:
                    continue
                viewer_sum = sum(v for v, _ in live)
                stream_sum = sum(s for _, s in live)
                avg_v_when_live = viewer_sum / live_count
                avg_s_when_live = stream_sum / live_count
                avg_vps = viewer_sum / stream_sum if stream_sum else 0.0
                if max_avg_viewers_per_stream > 0 and avg_vps > max_avg_viewers_per_stream:
                    continue
                candidates.append({
                    "twitch_game_id": str(game_id),
                    "twitch_game_name": name or str(game_id),
                    "latest_ts": latest_ts,
                    "snapshots": total,
                    "live_snapshots": live_count,
                    "live_fraction": round(live_fraction, 3),
                    "peak_viewers": peak,
                    "avg_viewers_when_live": round(avg_v_when_live, 1),
                    "avg_streams_when_live": round(avg_s_when_live, 2),
                    "avg_viewers_per_stream": round(avg_vps, 1),
                })

        sort_keys = {
            "avg_viewers_when_live": lambda c: c["avg_viewers_when_live"],
            "peak_viewers": lambda c: c["peak_viewers"],
            "live_fraction": lambda c: c["live_fraction"],
            "avg_viewers_per_stream": lambda c: c["avg_viewers_per_stream"],
        }
        key = sort_keys.get(sort_by, sort_keys["avg_viewers_when_live"])
        candidates.sort(key=key, reverse=True)
        if limit and limit > 0:
            candidates = candidates[:limit]
        return candidates

    def cleanup_old(self, retention_days: int):
        if retention_days <= 0:
            return
        cutoff = time.time() - retention_days * 86400
        with self._connect() as conn:
            conn.execute(
                "DELETE FROM viewer_snapshots WHERE timestamp < ?",
                (cutoff,),
            )
            conn.commit()

    def snapshot_count(self) -> int:
        with self._connect() as conn:
            row = conn.execute("SELECT COUNT(*) FROM viewer_snapshots").fetchone()
            return row[0] if row else 0

    def get_game_name(self, game_id: str) -> str:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT twitch_game_name FROM viewer_snapshots WHERE twitch_game_id = ? LIMIT 1",
                (str(game_id),),
            ).fetchone()
            return row[0] if row else ""

    def get_all_series(
        self,
        days: int = 7,
        current_window_minutes: int = 0,
        min_avg_viewers: float = 0.0,
        min_avg_discovery: float = 0.0,
        max_avg_streams: float = 0.0,
        limit: int = 0,
    ) -> List[Dict]:
        cutoff = time.time() - days * 86400
        games = self.get_tracked_games()
        if not games:
            return []

        now_minutes = None
        if current_window_minutes > 0:
            lt = time.localtime()
            now_minutes = lt.tm_hour * 60 + lt.tm_min

        candidates = []
        for game in games:
            game_id = game["twitch_game_id"]
            with self._connect() as conn:
                rows = conn.execute(
                    "SELECT timestamp, viewer_count, stream_count "
                    "FROM viewer_snapshots "
                    "WHERE twitch_game_id = ? AND timestamp >= ? "
                    "ORDER BY timestamp ASC",
                    (str(game_id), cutoff),
                ).fetchall()
            if not rows:
                continue

            avg_viewers_now = None
            avg_streams_now = None
            avg_discovery_now = None
            if now_minutes is not None:
                viewer_total = 0
                stream_total = 0
                discovery_total = 0.0
                window_count = 0
                for ts, vc, sc in rows:
                    lt = time.localtime(ts)
                    sample_minutes = lt.tm_hour * 60 + lt.tm_min
                    diff = abs(sample_minutes - now_minutes)
                    diff = min(diff, 1440 - diff)
                    if diff <= current_window_minutes:
                        viewer_total += vc
                        stream_total += sc
                        discovery_total += _discovery_score(vc, sc)
                        window_count += 1
                if window_count == 0:
                    continue
                avg_viewers_now = viewer_total / window_count
                avg_streams_now = stream_total / window_count
                avg_discovery_now = discovery_total / window_count
                if avg_viewers_now < min_avg_viewers:
                    continue
                if avg_discovery_now < min_avg_discovery:
                    continue
                if max_avg_streams > 0 and avg_streams_now > max_avg_streams:
                    continue

            candidates.append(
                (game, rows, avg_viewers_now, avg_streams_now, avg_discovery_now)
            )

        if now_minutes is not None:
            candidates.sort(
                key=lambda c: c[4] if c[4] is not None else 0.0,
                reverse=True,
            )
            if limit and limit > 0:
                candidates = candidates[:limit]

        result = []
        hue_step = 360 / max(len(candidates), 1)
        for i, (game, rows, avg_viewers_now, avg_streams_now, avg_discovery_now) in enumerate(candidates):
            hue = int(i * hue_step) % 360
            color = f"hsl({hue}, 65%, 55%)"
            data = [
                {"timestamp": ts, "discovery": _discovery_score(vc, sc)}
                for ts, vc, sc in rows
            ]
            entry = {
                "twitch_game_id": game["twitch_game_id"],
                "twitch_game_name": game.get("twitch_game_name") or game["twitch_game_id"],
                "color": color,
                "data": data,
            }
            if avg_viewers_now is not None:
                entry["avg_viewers_now"] = round(avg_viewers_now, 1)
                entry["avg_streams_now"] = round(avg_streams_now, 1)
                entry["avg_discovery_now"] = round(avg_discovery_now, 3)
            result.append(entry)
        return result
