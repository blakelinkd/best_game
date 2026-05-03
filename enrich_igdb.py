#!/usr/bin/env python3
"""Fill null metadata fields in cache/retro_games.json using IGDB.

IGDB authenticates through Twitch OAuth, so this reuses the existing
TWITCH_CLIENT_ID / TWITCH_CLIENT_SECRET from .env. For every entry with at
least one null among (description, developer, publisher, genre, release_date),
we batch-query IGDB by exact name within the entry's platform and merge any
non-null IGDB fields into the corresponding entries.

Run after retro_collector.py. Writes back to cache/retro_games.json in place.
"""
from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

CACHE = Path("cache")
INPUT = CACHE / "retro_games.json"
TOKEN_FILE = CACHE / "igdb_token.json"

# Output key in retro_games.json -> IGDB platform ID
# https://api-docs.igdb.com/#platform-enums
IGDB_PLATFORMS = {
    "nes":       18,
    "snes":      19,
    "n64":        4,
    "gamecube":  21,
    "gameboy":   33,
    "gbc":       22,
    "gba":       24,
    "genesis":   29,
    "saturn":    32,
    "dreamcast": 23,
    "ps1":        7,
    "ps2":        8,
    "3do":       50,
}

NAMES_PER_BATCH = 200          # IGDB query length cap
RATE_SLEEP = 0.30              # 4 req/sec hard limit; small headroom


# ---------- env loading ----------

def load_env(path: str = ".env") -> None:
    p = Path(path)
    if not p.exists():
        return
    for raw in p.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


# ---------- Twitch OAuth ----------

def get_token(client_id: str, client_secret: str) -> str:
    if TOKEN_FILE.exists():
        try:
            d = json.loads(TOKEN_FILE.read_text())
            if d.get("expires_at", 0) > time.time() + 60:
                return d["access_token"]
        except Exception:
            pass
    body = urllib.parse.urlencode({
        "client_id": client_id,
        "client_secret": client_secret,
        "grant_type": "client_credentials",
    }).encode("utf-8")
    req = urllib.request.Request(
        "https://id.twitch.tv/oauth2/token",
        data=body,
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=30) as r:
        d = json.loads(r.read())
    d["expires_at"] = time.time() + d.get("expires_in", 3600) - 60
    TOKEN_FILE.write_text(json.dumps(d))
    return d["access_token"]


# ---------- IGDB query ----------

def igdb_query(client_id: str, token: str, body: str, retries: int = 3) -> list:
    req = urllib.request.Request(
        "https://api.igdb.com/v4/games",
        data=body.encode("utf-8"),
        method="POST",
        headers={
            "Client-ID": client_id,
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
            "Content-Type": "text/plain",
        },
    )
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                return json.loads(r.read())
        except urllib.error.HTTPError as e:
            if e.code in (429, 502, 503, 504) and attempt < retries - 1:
                time.sleep(2 ** attempt)
                continue
            try:
                err_body = e.read().decode("utf-8", errors="replace")
            except Exception:
                err_body = ""
            print(f"  IGDB HTTP {e.code}: {err_body[:300]}", file=sys.stderr)
            raise


def igdb_quote(s: str) -> str:
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'


def chunks(seq, n):
    for i in range(0, len(seq), n):
        yield seq[i:i + n]


# ---------- result -> metadata ----------

FIELDS = (
    "fields name,summary,storyline,first_release_date,"
    "involved_companies.company.name,involved_companies.developer,"
    "involved_companies.publisher,genres.name;"
)


def extract_metadata(result: dict) -> dict:
    summary = result.get("summary") or result.get("storyline")
    devs, pubs = [], []
    for ic in result.get("involved_companies") or []:
        company = (ic.get("company") or {}).get("name")
        if not company:
            continue
        if ic.get("developer"):
            devs.append(company)
        if ic.get("publisher"):
            pubs.append(company)
    genres = [g.get("name") for g in (result.get("genres") or []) if g.get("name")]
    ts = result.get("first_release_date")
    release = (
        datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d")
        if ts
        else None
    )
    return {
        "description":  summary,
        "developer":    ", ".join(devs) if devs else None,
        "publisher":    ", ".join(pubs) if pubs else None,
        "genre":        ", ".join(genres) if genres else None,
        "release_date": release,
    }


# ---------- main ----------

def main() -> int:
    load_env()
    client_id = os.environ.get("TWITCH_CLIENT_ID")
    client_secret = os.environ.get("TWITCH_CLIENT_SECRET")
    if not client_id or not client_secret:
        print("ERROR: TWITCH_CLIENT_ID / TWITCH_CLIENT_SECRET not set in .env", file=sys.stderr)
        return 2

    if not INPUT.exists():
        print(f"ERROR: {INPUT} missing — run retro_collector.py first", file=sys.stderr)
        return 2

    print("Fetching IGDB access token via Twitch OAuth...")
    token = get_token(client_id, client_secret)

    data = json.loads(INPUT.read_text())
    fillable_keys = ("description", "developer", "publisher", "genre", "release_date")

    print("Per-platform enrichment:")
    grand_filled = {k: 0 for k in fillable_keys}
    grand_matched_entries = 0
    grand_target_entries = 0

    for platform, entries in data["platforms"].items():
        plat_id = IGDB_PLATFORMS.get(platform)
        if plat_id is None:
            print(f"  {platform:<10} no IGDB platform mapping, skipping")
            continue

        # Targets: entries with at least one null metadata field
        target_idx_by_name: dict[str, list[int]] = {}
        for i, e in enumerate(entries):
            if any(e.get(k) is None for k in fillable_keys):
                target_idx_by_name.setdefault(e["name"].lower(), []).append(i)
        unique_names = list(target_idx_by_name.keys())
        if not unique_names:
            print(f"  {platform:<10} no entries need enrichment")
            continue

        # We query by entry name (preserving case) — pick first variant per lc-key.
        case_by_lc: dict[str, str] = {}
        for i, e in enumerate(entries):
            lc = e["name"].lower()
            case_by_lc.setdefault(lc, e["name"])

        target_entries = sum(len(v) for v in target_idx_by_name.values())
        grand_target_entries += target_entries

        per_field: dict[str, int] = {k: 0 for k in fillable_keys}
        matched_entries = 0

        for batch_lc in chunks(unique_names, NAMES_PER_BATCH):
            batch_names = [case_by_lc[lc] for lc in batch_lc]
            quoted = ",".join(igdb_quote(n) for n in batch_names)
            body = (
                FIELDS
                + f"where platforms = ({plat_id}) & name = ({quoted});"
                + f"limit {NAMES_PER_BATCH * 2};"
            )
            results = igdb_query(client_id, token, body)
            time.sleep(RATE_SLEEP)

            seen_lc: set[str] = set()
            for r in results:
                name = r.get("name") or ""
                lc = name.lower()
                if lc in seen_lc:
                    continue
                seen_lc.add(lc)
                idxs = target_idx_by_name.get(lc)
                if not idxs:
                    continue
                md = extract_metadata(r)
                for i in idxs:
                    entry = entries[i]
                    entry_changed = False
                    for k in fillable_keys:
                        if entry.get(k) is None and md.get(k):
                            entry[k] = md[k]
                            per_field[k] += 1
                            entry_changed = True
                    if entry_changed:
                        matched_entries += 1

        grand_matched_entries += matched_entries
        for k, v in per_field.items():
            grand_filled[k] += v
        miss = target_entries - matched_entries
        fields_summary = " ".join(f"{k}={v}" for k, v in per_field.items())
        print(
            f"  {platform:<10} target={target_entries:>5} unique={len(unique_names):>5} "
            f"matched={matched_entries:>5} miss={miss:>5}  ({fields_summary})"
        )

    # Update sources field
    data.setdefault("sources", {})["metadata_supplement"] = "IGDB v4"
    data["enriched_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")

    INPUT.write_text(json.dumps(data, indent=2, ensure_ascii=False))
    print(f"\nWrote {INPUT} ({INPUT.stat().st_size:,} bytes)")
    print(
        f"Overall: {grand_matched_entries}/{grand_target_entries} entries enriched"
    )
    print("Per-field totals filled:")
    for k, v in grand_filled.items():
        print(f"  {k:<14} +{v}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
