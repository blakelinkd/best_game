# Historical Data Tracking & Analytics — Implementation Plan

## Goal

Track Twitch viewer/streamer counts over time for all matched games, visualize with charts and heatmaps, and identify optimal streaming windows.

---

## New Files

### 1. `history_store.py` — SQLite Database Layer

**Schema:**

```sql
CREATE TABLE IF NOT EXISTS viewer_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp REAL NOT NULL,
    twitch_game_id TEXT NOT NULL,
    twitch_game_name TEXT,
    viewer_count INTEGER NOT NULL DEFAULT 0,
    stream_count INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_snapshots_game_time
    ON viewer_snapshots(twitch_game_id, timestamp);
```

**Methods:**

| Method | Signature | Purpose |
|---|---|---|
| `__init__` | `(db_path: str)` | Open/create SQLite DB |
| `save_snapshots` | `(snapshots: List[dict])` | Bulk insert snapshot rows |
| `get_series` | `(game_id: str, days: int) -> List[dict]` | Time-series rows for a game, `days`-window |
| `get_heatmap` | `(game_id: str, days: int) -> dict` | Day-of-week × hour-of-day aggregated data |
| `get_tracked_games` | `() -> List[dict]` | All games with ≥1 snapshot (id, name, count, range) |
| `cleanup_old` | `(retention_days: int)` | Delete snapshots older than retention window |

**Heatmap query logic:** For each snapshot, extract `day_of_week` (0=Mon…6=Sun) and `hour` (0–23) from the timestamp. Group by `(day_of_week, hour)`, compute `avg_viewers`, `avg_streams`, `avg_discovery_score` (calculated via the existing formula), and `count`.

---

### 2. `collector.py` — Background Snapshot Collector

**Behavior:**

- Daemon thread started at Flask app startup
- Waits for the initial game-processing background job to finish (polls `_background_jobs`)
- Every `COLLECTION_INTERVAL` minutes (default 15): fetches viewer/stream counts for ALL matched games, saves snapshots to SQLite
- Uses `threading.Lock` to prevent overlapping collection cycles
- Uses a dedicated `TwitchClient` instance (doesn't share the `ViewerService`'s client)
- Reads matched game IDs from the `viewer_cache.json` (which `ViewerService` writes)

**Public API:**

| Method/Property | Type | Purpose |
|---|---|---|
| `start()` | method | Launch the background loop |
| `stop()` | method | Signal shutdown |
| `trigger_snapshot()` | method | Run one collection cycle immediately (if not already running) |
| `is_running` | property | Whether a collection cycle is in progress |
| `last_collection_time` | property | Timestamp of last completed cycle |
| `total_snapshots` | property | Total snapshots stored |
| `status()` | method | Dict with all status fields |

**Collection cycle logic:**

```python
def _collect_cycle(self):
    # 1. Read twitch_games from cache
    # 2. Get all twitch_game_id values
    # 3. Call twitch_client.get_game_viewer_counts(all_ids)
    #    (batched in groups of 100, paginated per batch)
    # 4. Build snapshot list: [{timestamp, twitch_game_id, twitch_game_name,
    #       viewer_count, stream_count}, ...]
    # 5. history_store.save_snapshots(snapshots)
    # 6. Update status
```

---

### 3. New API Endpoints (in `main.py`)

| Route | Method | Request Params | Response | Purpose |
|---|---|---|---|---|
| `/api/history/series` | GET | `game_id`, `days` (default 7) | `[{timestamp, viewer_count, stream_count}, ...]` | Time-series for line charts |
| `/api/history/heatmap` | GET | `game_id`, `days` (default 30) | `[[{hour, avg_viewers, avg_streams, avg_discovery, count}], ...]` (7 rows × 24 cols) | Heatmap data |
| `/api/history/games` | GET | — | `[{twitch_game_id, twitch_game_name, snapshot_count, first_ts, last_ts}]` | Dropdown options |
| `/api/history/status` | GET | — | `{running, last_collection_time, total_snapshots, next_collection_time}` | Collector status |
| `/api/history/collect` | POST | — | `{success, message}` | Manual snapshot trigger |

---

### 4. `templates/index.html` — Tabbed UI + Data Page

Tab navigation added below the stats bar, switching between "Library" and "Data" views.

Data tab: game dropdown, time range pills (24h/7d/30d/All), Record Now button, status bar, line charts (viewers+streams, discovery score trend), and a day×hour heatmap.

Chart.js loaded from CDN: `https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js`

---

### 5. `static/styles.css` — New Styles

Tab bar, data controls layout, chart containers, heatmap grid with green→red color scale, time-range pill buttons, status badge, record button.

---

### 6. `config.py` — New Settings

```python
COLLECTION_INTERVAL = int(os.getenv('COLLECTION_INTERVAL', '900'))  # seconds (default 15 min)
HISTORY_RETENTION_DAYS = int(os.getenv('HISTORY_RETENTION_DAYS', '90'))  # 0 = keep forever
HISTORY_DB_PATH = os.getenv('HISTORY_DB_PATH', os.path.join(CACHE_DIR, 'viewer_history.db'))
```

---

### 7. `main.py` — Startup Wiring

Start collector thread after Flask is configured. Add five new `/api/history/*` routes.

---

## Implementation Order

1. `history_store.py`
2. `config.py` — add new settings
3. `collector.py`
4. `main.py` — new routes + startup wiring
5. `templates/index.html` — tab UI + charts + JS
6. `static/styles.css` — new styles
