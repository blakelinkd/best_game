# Twitch Streamer Co-Occurrence — Audience Overlap Feature

## Goal

For a given Twitch category X (the source game), surface other categories whose **audiences overlap** by measuring **streamer co-occurrence** — i.e. channels that have streamed both X and Y in their VOD history. Output: a ranked list of related categories with an "audience overlap score" between 0 and 1.

This complements the existing **Opportunities** tab (find empty categories with proven demand). Audience overlap answers: "if my game is X, what *other* categories share its audience?" — useful for stream planning, not just gap-spotting.

---

## Why this approach

The Twitch API has **no direct viewer-affinity endpoint** ("users who watched X also watched Y" doesn't exist). Steam has no co-play API either, and Steam co-ownership is the wrong graph anyway (someone who owns Stardew may never watch Stardew streams).

The strongest available proxy is the **streamer-rotation graph**: streamers tend to schedule games their existing followers will tolerate, so games that frequently appear in the same channels' VOD histories share an audience.

A content-similarity alternative (SteamSpy tag-vector / IGDB genre Jaccard) was discussed and **deferred** — it's purely semantic and doesn't capture behavioral signal. Could be added later as a secondary feature if useful.

---

## Existing context

- App is a Flask single-page tool at `main.py`, frontend in `templates/index.html` + `static/styles.css`.
- Tabs: **Library**, **Data**, **Opportunities** (the Opportunities tab was added in the same session — see `HistoryStore.get_opportunities()` and `/api/history/opportunities` for the established pattern).
- Twitch API client: `twitch_client.py` (`TwitchClient`). 800 req/min app-token rate limit, already wired through `_make_request` with `sleep_and_retry`.
- History DB: `cache/viewer_history.db` via `HistoryStore` (`history_store.py`). ~5 days of viewer/stream snapshots already collected, ~6,000 tracked games.

---

## Design decisions (with rationale, so you can deviate)

| Decision | Default | Why | When to change |
|---|---|---|---|
| Seed source | `GET /helix/videos?game_id=X&type=archive` (recent VOD broadcasters), not `/helix/streams` | `/streams` is currently-live only — empty for niche games, exactly the case we care about | If only currently-live streamers matter for some flow, add a separate seed mode |
| Seed size | 30 channels | Keeps cold-call latency under ~10 s at 13 req/s rate-limit pacing | Crank to 80–100 if results look thin; rate budget allows |
| VODs per channel | 100 (one paginated call per channel) | Captures recent broadcast variety, single API call per channel | Increase if channels rotate widely and 100 VODs misses key categories |
| Score formula | `shared_streamers / seed_streamers` (fraction, 0–1) | Avoids favoring giant categories that show up in many channels' rotations | Could weight by VOD count per channel for more nuance |
| Channel cache TTL | 7 days | Streamers don't change rotation faster than weekly | Shorten if results feel stale |
| Overlap result TTL | 12 hours | Source game's seed broadcasters change slowly | Match to channel TTL (7d) if cost is a concern |
| Sync vs background | **Synchronous** for first cut | Cold call ~10 s; cached calls instant. Frontend shows spinner | Move to job-id pattern (see `_start_background_job` in `main.py`) if cold latency hurts |
| Source game self-exclusion | Yes — drop X from results | Otherwise X always tops its own list | — |

---

## Files to touch

### 1. `config.py` — one new constant

```python
TWITCH_VIDEOS_URL = "https://api.twitch.tv/helix/videos"
```
Add next to the existing `TWITCH_*_URL` constants (~line 136).

### 2. `twitch_client.py` — two new methods on `TwitchClient`

```python
def get_recent_broadcasters_for_game(
    self,
    game_id: str,
    max_users: int = 30,
) -> List[Dict]:
    """Return up to `max_users` distinct (user_id, user_name) pairs that have
    archived VODs for this category. Paginates /helix/videos?game_id=...
    &type=archive&first=100 until enough unique broadcasters or no cursor."""
```

```python
def get_channel_vod_game_ids(
    self,
    user_id: str,
    max_videos: int = 100,
) -> Dict[str, int]:
    """Return {twitch_game_id: vod_count} for this channel's archived VODs.
    Single call to /helix/videos?user_id=...&type=archive&first=100 is enough
    for max_videos<=100."""
```

Both use the existing `self._make_request(...)` (already rate-limited, token-managed).

**Twitch Helix `videos` endpoint shape** — for reference:
- Request: `GET /helix/videos?game_id=...&type=archive&first=100&period=month` *(period optional; defaults to all)*. Or `?user_id=...&type=archive&first=100`.
- Response item fields used: `user_id`, `user_login`, `user_name`, `game_id` (only present in some responses — when filtering by `game_id`, every item shares it; when filtering by `user_id`, each item carries its own `game_id`).
- ⚠️ **Verify before relying on `game_id` in user-filtered responses.** Helix has historically been inconsistent about exposing `game_id` on `videos?user_id=...` responses. If it's missing, fall back to one of:
  1. `GET /helix/streams?user_id=...` for current category (only works if live),
  2. `GET /helix/channels?broadcaster_id=...` for the channel's *current* `game_id` (single value, less rich),
  3. Pulling clip game_ids via `GET /helix/clips?broadcaster_id=...`.

Test with a real call early. If `game_id` is reliably populated on user-VOD lookups, the design works as written.

### 3. `history_store.py` — three new tables + helpers

Append to the existing `_init_db()` block (it uses `CREATE TABLE IF NOT EXISTS`, so additive migration is safe).

```sql
CREATE TABLE IF NOT EXISTS channel_vod_games (
    user_id TEXT NOT NULL,
    twitch_game_id TEXT NOT NULL,
    vod_count INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (user_id, twitch_game_id)
);
CREATE INDEX IF NOT EXISTS idx_channel_vod_games_user
    ON channel_vod_games(user_id);
CREATE INDEX IF NOT EXISTS idx_channel_vod_games_game
    ON channel_vod_games(twitch_game_id);

CREATE TABLE IF NOT EXISTS channel_vod_fetched (
    user_id TEXT PRIMARY KEY,
    user_name TEXT,
    fetched_at REAL NOT NULL,
    vod_total INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS audience_overlap (
    source_game_id TEXT NOT NULL,
    related_game_id TEXT NOT NULL,
    related_game_name TEXT,
    shared_streamers INTEGER NOT NULL,
    seed_streamers INTEGER NOT NULL,
    score REAL NOT NULL,
    computed_at REAL NOT NULL,
    PRIMARY KEY (source_game_id, related_game_id)
);
CREATE INDEX IF NOT EXISTS idx_audience_overlap_source
    ON audience_overlap(source_game_id, score DESC);
```

New methods:

| Method | Signature | Purpose |
|---|---|---|
| `get_channels_to_refresh` | `(user_ids: List[str], stale_after_days: int) -> List[str]` | Filter: which user_ids are missing or stale in `channel_vod_fetched` |
| `save_channel_vods` | `(user_id: str, user_name: str, game_counts: Dict[str, int])` | Replace prior rows for this user, upsert `channel_vod_fetched` |
| `get_users_who_streamed` | `(game_id: str, user_ids: List[str]) -> Set[str]` | From `channel_vod_games`, the subset of `user_ids` with ≥1 VOD of `game_id` |
| `count_overlap_for_seed` | `(seed_user_ids: List[str]) -> List[Tuple[game_id, count]]` | `SELECT twitch_game_id, COUNT(DISTINCT user_id) FROM channel_vod_games WHERE user_id IN (...) GROUP BY twitch_game_id` |
| `save_audience_overlap` | `(source_game_id: str, rows: List[Dict])` | Replace prior overlap result for source_game_id |
| `get_audience_overlap` | `(source_game_id: str, max_age_hours: float) -> Optional[List[Dict]]` | Return cached rows if `computed_at` is within window, else `None` |

For the `IN (...)` clause use parameter chunking (500 at a time, same pattern as the existing `get_scan_backoffs` method).

### 4. `audience_service.py` — new file, ~80 lines

```python
from typing import Dict, List, Optional
import time

class AudienceService:
    def __init__(self, twitch_client, history_store):
        self.twitch = twitch_client
        self.history = history_store

    def compute_overlap(
        self,
        source_game_id: str,
        max_seed_channels: int = 30,
        vods_per_channel: int = 100,
        channel_cache_days: int = 7,
        result_cache_hours: float = 12.0,
        force_refresh: bool = False,
    ) -> Dict:
        # 1. Cache hit?
        if not force_refresh:
            cached = self.history.get_audience_overlap(source_game_id, result_cache_hours)
            if cached is not None:
                return {"games": cached, "from_cache": True, ...}

        # 2. Seed: recent broadcasters of source_game_id
        seed = self.twitch.get_recent_broadcasters_for_game(source_game_id, max_seed_channels)
        seed_user_ids = [s["user_id"] for s in seed]
        if not seed_user_ids:
            return {"games": [], "from_cache": False, "seed_size": 0, ...}

        # 3. Refresh stale channel VOD data
        stale = self.history.get_channels_to_refresh(seed_user_ids, channel_cache_days)
        for user in seed:
            if user["user_id"] in stale:
                game_counts = self.twitch.get_channel_vod_game_ids(user["user_id"], vods_per_channel)
                self.history.save_channel_vods(user["user_id"], user["user_name"], game_counts)

        # 4. Aggregate co-occurrence
        rows = self.history.count_overlap_for_seed(seed_user_ids)
        total_seed = len(seed_user_ids)
        result = []
        for game_id, count in rows:
            if game_id == source_game_id:
                continue
            score = count / total_seed
            # Optionally enrich with name from history snapshots (HistoryStore.get_game_name)
            name = self.history.get_game_name(game_id) or game_id
            result.append({
                "twitch_game_id": game_id,
                "twitch_game_name": name,
                "shared_streamers": count,
                "seed_streamers": total_seed,
                "score": round(score, 3),
            })
        result.sort(key=lambda r: r["score"], reverse=True)

        # 5. Persist
        self.history.save_audience_overlap(source_game_id, result)
        return {"games": result, "from_cache": False, "seed_size": total_seed, ...}
```

Keep it that simple. No threading inside the service — the endpoint can decide to background-it later.

### 5. `main.py` — one new endpoint

After `history_opportunities`:

```python
@app.route("/api/audience/overlap")
def audience_overlap():
    if not _history_store:
        return jsonify({"error": "history store not initialized"}), 500
    game_id = request.args.get("game_id", "").strip()
    if not game_id:
        return jsonify({"error": "game_id required"}), 400
    refresh = request.args.get("refresh") == "1"
    seed = request.args.get("seed", default=30, type=int)

    from audience_service import AudienceService
    service = AudienceService(TwitchClient(), _history_store)
    try:
        result = service.compute_overlap(
            source_game_id=game_id,
            max_seed_channels=seed,
            force_refresh=refresh,
        )
        return jsonify(result)
    except Exception as e:
        return jsonify({"error": str(e)}), 500
```

If cold-call latency becomes a problem, copy the `_start_background_job` / `/api/progress` pattern.

### 6. `templates/index.html` + JS — new tab

Pattern is established by the **Opportunities** tab — copy it.

- Add `<button class="tab" data-tab="audience">Audience Overlap</button>` to `nav#tab-nav`.
- Add `<div id="tab-audience" class="tab-panel" hidden>` after `tab-opportunities`. Inside:
  - Game search input (reuse the `history-game-search` widget that the Data tab already has — it autocompletes from `/api/history/games`).
  - Optional: seed-size number input (default 30).
  - "Compute" button + status indicator.
  - Results table (reuse `.opportunities-table` styling): columns Game / Shared streamers / Seed / Score / View history.
- In `switchTab`, add `tabAudience.hidden = tabName !== 'audience';` and a refresh hook.
- New JS function `refreshAudienceOverlap()`: `fetch('/api/audience/overlap?game_id=...&seed=...')`, render rows, show spinner during the cold call (~10 s).

The Data tab's `setHistoryGame()` is already a clean way to share a game-picker widget across tabs — consider lifting it into a small reusable helper, or just duplicate the markup with a different ID set if reuse is awkward.

### 7. `static/styles.css` — likely no new CSS

Reuse `.opportunities-table`, `.opportunities-table-wrapper`, `.opportunities-empty`, `.all-games-filters` (rename or alias if it confuses). Add minimal new selectors only if needed.

Note: a recent CSS edit (intentional) reorganized parts of `styles.css`. Don't revert it.

---

## API budget sanity check

For a fresh game with empty cache:
- 1 call: `videos?game_id=X` (paginate up to 30 unique users → 1–2 calls).
- 30 calls: one `videos?user_id=...` per channel.
- **~32 calls per cold lookup.** At 800/min limit, ~2.4 s of rate-limit budget. Wall-clock dominated by Twitch latency (~250–400 ms per call) → ~10 s.

Cached lookup: 0–1 calls (just the seed re-poll if its TTL elapsed). Sub-second.

Channel VODs are shared across source games — lookups for popular streamers warm the cache for all source games they touch.

---

## Testing

1. **Unit-style smoke** of new TwitchClient methods:
   ```bash
   .venv/bin/python -c "
   from twitch_client import TwitchClient
   tc = TwitchClient()
   # 32982 = GTA V, well-populated
   print(tc.get_recent_broadcasters_for_game('32982', 5))
   uid = tc.get_recent_broadcasters_for_game('32982', 1)[0]['user_id']
   print(tc.get_channel_vod_game_ids(uid, 100))
   "
   ```
   **Specifically verify** that `get_channel_vod_game_ids` returns a dict with multiple distinct `game_id` keys — if it returns only one or zero, the `videos?user_id=...` response isn't carrying `game_id` per-item. Pivot to one of the fallbacks listed in §2 before going further.

2. **End-to-end via endpoint**:
   ```bash
   .venv/bin/python main.py --no-browser --port 5057 &
   sleep 5
   curl -sS "http://127.0.0.1:5057/api/audience/overlap?game_id=32982&seed=20" | jq .
   ```
   Expect: 5–30 s on first call, then `from_cache: true` on the second.

3. **Sanity check the result**: pick a well-known game and eyeball overlap. E.g. *Hades* should overlap with *Hades II*, *Dead Cells*, *Slay the Spire*. *Stardew Valley* with *Animal Crossing*, *Coral Island*, *Cult of the Lamb*. Wildly off rankings = either the seed is bad (whale streamers) or `game_id` isn't reliably populated.

4. **UI smoke**: open the page, click the new tab, pick a game, confirm rendering and the "View history" jump.

---

## What's already done in this codebase (do not redo)

- `HistoryStore.get_opportunities()` and `/api/history/opportunities` endpoint — gap-finder for empty categories with proven demand. Same conversation; already shipped on `master`. Read it as a reference for the pattern.
- The "Opportunities" tab UI (table + filter row) — model your tab on it.

## What's been deliberately deferred

- **SteamSpy tag-vector / IGDB content-similarity** as a complement. Cheaper and content-based, but less behaviorally meaningful. Revisit only if streamer co-occurrence proves insufficient.
- **Backgrounded job pattern** for cold calls. Add only if 10 s synchronous calls become a UX problem.
- **Per-channel weighting** (e.g. score × log(vod_count)). Useful refinement but not needed for first cut.

---

## Open questions for the implementing agent

1. Is `game_id` reliably populated in `videos?user_id=...` responses today? **Test first.** If not, see fallbacks in §2.
2. Should the seed size be exposed in the UI, or hidden as a constant for now? Default: hide it for v1, surface only if early users ask.
3. Should the result table show *current viewer count* alongside score? It would require joining against the latest snapshot in `viewer_snapshots` — straightforward, makes the recommendation immediately actionable. Recommended: yes.
