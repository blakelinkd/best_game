#!/usr/bin/env python3
"""Compile a master list of retro games (NES, SNES, Genesis, Dreamcast, PS1).

Output: cache/retro_games.json

Sources
-------
- Game list + box-art thumbnails: https://thumbnails.libretro.com/
- Metadata (description, developer, publisher, genre, release date):
  OpenVGDB v29.0 (https://github.com/OpenVGDB/OpenVGDB)

Stdlib only. Idempotent: safe to re-run; OpenVGDB is downloaded once.

Dedup: one entry per base title, region priority World > USA > Europe > Japan,
ties broken by shortest filename. Demos / betas / prototypes are dropped.

Note: OpenVGDB does not include Sega Dreamcast, so Dreamcast entries will have
null metadata fields. The list and thumbnails are still produced.
"""
from __future__ import annotations

import json
import re
import sqlite3
import sys
import urllib.parse
import urllib.request
import zipfile
from datetime import datetime, timezone
from pathlib import Path

CACHE_DIR = Path("cache")
OUTPUT = CACHE_DIR / "retro_games.json"
LIBRETRO_BASE = "https://thumbnails.libretro.com"
OPENVGDB_URL = (
    "https://github.com/OpenVGDB/OpenVGDB/releases/download/v29.0/openvgdb.zip"
)
OPENVGDB_VERSION = "v29.0"
USER_AGENT = "retro_collector/1.0 (+best_game)"

# (output_key, libretro path segment, openvgdb systemShortName or None)
# `None` for the third field means OpenVGDB has no metadata for that system
# (Dreamcast and PS2 — list and thumbnails still work, metadata fields will be null).
SYSTEMS = [
    ("nes",       "Nintendo - Nintendo Entertainment System",       "NES"),
    ("snes",      "Nintendo - Super Nintendo Entertainment System", "SNES"),
    ("n64",       "Nintendo - Nintendo 64",                         "N64"),
    ("gamecube",  "Nintendo - GameCube",                            "NGC"),
    ("gameboy",   "Nintendo - Game Boy",                            "GB"),
    ("gbc",       "Nintendo - Game Boy Color",                      "GBC"),
    ("gba",       "Nintendo - Game Boy Advance",                    "GBA"),
    ("genesis",   "Sega - Mega Drive - Genesis",                    "MD"),
    ("saturn",    "Sega - Saturn",                                  "Saturn"),
    ("dreamcast", "Sega - Dreamcast",                               None),
    ("ps1",       "Sony - PlayStation",                             "PSX"),
    ("ps2",       "Sony - PlayStation 2",                           None),
    ("3do",       "The 3DO Company - 3DO",                          "3DO"),
]

# English-language region tiers, in priority order (lower index = preferred).
# JP-US / US-JP NES dumps include the English ROM, so treat as World.
ENGLISH_TIERS = [
    ("World",  {"World", "JP-US", "US-JP"}),
    ("USA",    {"USA", "US", "North America", "NA", "Canada"}),
    ("Europe", {"Europe", "EU", "PAL", "UK", "Australia", "Ireland",
                "New Zealand"}),
]

# Single-language regions whose releases are typically NOT in English.
# An entry tagged ONLY with these regions is skipped.
NON_ENGLISH_REGIONS = {
    "Japan", "JP", "JPN",
    "Korea", "KR", "China", "CN", "Taiwan", "TW", "Asia", "Hong Kong", "HK",
    "Brazil", "BR", "Mexico", "MX",
    "France", "FR", "Germany", "DE", "Spain", "ES", "Italy", "IT",
    "Netherlands", "NL", "Sweden", "SE", "Russia", "RU", "Poland", "PL",
    "Scandinavia", "Greece", "Portugal", "Hungary", "Czech",
}

SKIP_TAGS = {
    "Demo", "Beta", "Proto", "Prototype", "Sample", "Unl",
    "Pirate", "Hack", "Aftermarket", "Test", "Bootleg",
    "Program", "Educational",
}

# GoodTools-style single-char bracket codes that indicate a non-canonical dump
# (hack, overdump, bad dump, translation, pirate, trainer, fixed, cracked, ...).
# `!` means good dump and is OK to keep. Anything else => skip the entry.
BRACKET_KEEP = {"!"}

HREF_RE = re.compile(r'href="([^"]+)"', re.IGNORECASE)
TAG_RE = re.compile(r"\(([^()]*)\)")
BRACKET_RE = re.compile(r"\[([^\[\]]*)\]")
PUNCT_RE = re.compile(r"[^a-z0-9]+")


# ---------- HTTP ----------

def http_get(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=120) as r:
        return r.read()


# ---------- libretro scraping ----------

def list_named_boxarts(system_path: str) -> list[str]:
    """Return list of PNG filenames (URL-decoded) under Named_Boxarts/."""
    url = f"{LIBRETRO_BASE}/{urllib.parse.quote(system_path)}/Named_Boxarts/"
    html = http_get(url).decode("utf-8", errors="replace")
    names: list[str] = []
    for m in HREF_RE.finditer(html):
        href = m.group(1)
        if href.startswith("?") or href in ("../", "/") or "/" in href:
            continue
        if not href.lower().endswith(".png"):
            continue
        names.append(urllib.parse.unquote(href))
    return names


# ---------- filename parsing ----------

def parse_filename(filename: str) -> dict:
    """Extract base_name, region, and skip reason from a libretro filename.

    Skip reasons:
      - parens-tag in SKIP_TAGS (Demo/Beta/Proto/Hack/...)
      - bracket-tag other than [!]  (GoodTools hack/overdump/pirate/...)
      - only non-English region tag(s) and no English region tag
    """
    stem = filename[:-4] if filename.lower().endswith(".png") else filename
    paren_tags = [t.strip() for t in TAG_RE.findall(stem)]
    bracket_tags = [t.strip() for t in BRACKET_RE.findall(stem)]

    base = re.sub(r"\s+", " ", BRACKET_RE.sub("", TAG_RE.sub("", stem))).strip()

    skip: str | None = None

    # Bracket tags: skip anything that isn't an explicit good-dump marker.
    for bt in bracket_tags:
        if bt not in BRACKET_KEEP:
            skip = f"bracket:{bt}"
            break

    # Paren skip tags (Demo/Beta/etc.)
    if not skip:
        for tag in paren_tags:
            for part in re.split(r"[,\s]+", tag):
                if part in SKIP_TAGS:
                    skip = part
                    break
            if skip:
                break

    # Region detection
    english_priority = 99
    english_canonical: str | None = None
    has_non_english = False
    for tag in paren_tags:
        parts = [p.strip() for p in tag.split(",")]
        for tier_idx, (canonical, aliases) in enumerate(ENGLISH_TIERS):
            if any(p in aliases for p in parts):
                if tier_idx < english_priority:
                    english_priority = tier_idx
                    english_canonical = canonical
        if any(p in NON_ENGLISH_REGIONS for p in parts):
            has_non_english = True

    # English-only filter: skip entries tagged ONLY with non-English regions.
    if not skip and has_non_english and english_canonical is None:
        skip = "non_english_only"

    return {
        "filename": filename,
        "base_name": base,
        "region": english_canonical,
        "region_priority": english_priority,
        "skip": skip,
    }


# ---------- dedup ----------

def dedup(parsed_entries: list[dict]) -> list[dict]:
    by_base: dict[str, dict] = {}
    for e in parsed_entries:
        if e["skip"]:
            continue
        key = e["base_name"].lower()
        existing = by_base.get(key)
        new_score = (e["region_priority"], len(e["filename"]))
        if existing is None or new_score < (existing["region_priority"], len(existing["filename"])):
            by_base[key] = e
    return sorted(by_base.values(), key=lambda x: x["base_name"].lower())


# ---------- OpenVGDB ----------

def ensure_openvgdb() -> Path:
    sqlite_path = CACHE_DIR / "openvgdb.sqlite"
    if sqlite_path.exists():
        return sqlite_path
    zip_path = CACHE_DIR / "openvgdb.zip"
    print(f"  downloading OpenVGDB {OPENVGDB_VERSION} -> {zip_path}")
    zip_path.write_bytes(http_get(OPENVGDB_URL))
    with zipfile.ZipFile(zip_path) as zf:
        zf.extract("openvgdb.sqlite", CACHE_DIR)
    return sqlite_path


def loose(s: str) -> str:
    return PUNCT_RE.sub("", s.lower())


def build_metadata_index(db_path: Path) -> dict[str, dict]:
    con = sqlite3.connect(db_path)
    indices: dict[str, dict] = {}
    needed = {short for _, _, short in SYSTEMS if short}
    for short in needed:
        rows = con.execute(
            """
            SELECT r.releaseTitleName, ro.romExtensionlessFileName,
                   r.releaseDescription, r.releaseDeveloper, r.releasePublisher,
                   r.releaseGenre, r.releaseDate
            FROM RELEASES r
            JOIN ROMs ro    ON r.romID = ro.romID
            JOIN SYSTEMS s  ON ro.systemID = s.systemID
            WHERE s.systemShortName = ?
            """,
            (short,),
        ).fetchall()
        idx_title: dict[str, dict] = {}
        idx_rom: dict[str, dict] = {}
        for title, romname, desc, dev, pub, genre, date in rows:
            md = {
                "description": desc,
                "developer": dev,
                "publisher": pub,
                "genre": genre,
                "release_date": date,
            }
            if title:
                idx_title.setdefault(title.lower(), md)
            if romname:
                idx_rom.setdefault(romname.lower(), md)
        idx_title_loose = {loose(k): v for k, v in idx_title.items()}
        indices[short] = {
            "title": idx_title,
            "rom": idx_rom,
            "title_loose": idx_title_loose,
        }
    con.close()
    return indices


def lookup_metadata(entry: dict, idx: dict | None) -> dict | None:
    if idx is None:
        return None
    base = entry["base_name"]
    stem = entry["filename"][:-4]
    return (
        idx["title"].get(base.lower())
        or idx["rom"].get(stem.lower())
        or idx["title_loose"].get(loose(base))
    )


# ---------- thumbnail URL ----------

def thumbnail_url(system_path: str, filename: str) -> str:
    # urllib.parse.quote with safe="" escapes everything including parens/spaces.
    return (
        f"{LIBRETRO_BASE}/"
        f"{urllib.parse.quote(system_path)}/Named_Boxarts/"
        f"{urllib.parse.quote(filename)}"
    )


# ---------- main ----------

def main() -> int:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)

    print("Step 1: ensure OpenVGDB sqlite is present")
    db_path = ensure_openvgdb()

    print("Step 2: build OpenVGDB metadata index")
    indices = build_metadata_index(db_path)
    for short, idx in indices.items():
        print(f"  {short:<6} releases={len(idx['title']):>5}  rom_filenames={len(idx['rom']):>5}")

    print("Step 3: scrape libretro thumbnails + dedup + enrich")
    out = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "sources": {
            "thumbnails": LIBRETRO_BASE + "/",
            "metadata": f"OpenVGDB {OPENVGDB_VERSION}",
        },
        "platforms": {},
    }

    summary = []
    for key, system_path, short in SYSTEMS:
        print(f"  {key}: GET {system_path}/Named_Boxarts/")
        raw_names = list_named_boxarts(system_path)
        parsed = [parse_filename(n) for n in raw_names]
        skipped = sum(1 for p in parsed if p["skip"])
        skip_buckets: dict[str, int] = {}
        for p in parsed:
            if not p["skip"]:
                continue
            bucket = p["skip"].split(":", 1)[0]
            skip_buckets[bucket] = skip_buckets.get(bucket, 0) + 1
        deduped = dedup(parsed)

        idx = indices.get(short) if short else None
        entries: list[dict] = []
        hits = 0
        for e in deduped:
            md = lookup_metadata(e, idx)
            if md:
                hits += 1
            entries.append({
                "platform": key,
                "name": e["base_name"],
                "filename": e["filename"],
                "region": e["region"],
                "thumbnail_url": thumbnail_url(system_path, e["filename"]),
                "description":  (md or {}).get("description"),
                "developer":    (md or {}).get("developer"),
                "publisher":    (md or {}).get("publisher"),
                "genre":        (md or {}).get("genre"),
                "release_date": (md or {}).get("release_date"),
            })
        out["platforms"][key] = entries
        summary.append((key, len(raw_names), skipped, len(entries), hits, short))
        miss = len(entries) - hits
        print(
            f"    raw={len(raw_names):>5}  skipped={skipped:>4}  kept={len(entries):>5}"
            f"  metadata_hits={hits}/{len(entries)} ({miss} miss)"
        )
        if skip_buckets:
            buckets = ", ".join(f"{k}={v}" for k, v in sorted(skip_buckets.items(), key=lambda kv: -kv[1]))
            print(f"    skip_reasons: {buckets}")

    OUTPUT.write_text(json.dumps(out, indent=2, ensure_ascii=False))
    print(f"\nWrote {OUTPUT} ({OUTPUT.stat().st_size:,} bytes)")

    print("\n=== Summary ===")
    print(f"{'system':<10} {'raw':>6} {'skipped':>8} {'kept':>6} {'meta_hits':>10} {'miss':>6}  source")
    for key, raw, skipped, kept, hits, short in summary:
        src = short or "(no metadata source)"
        miss = kept - hits
        print(f"{key:<10} {raw:>6} {skipped:>8} {kept:>6} {hits:>10} {miss:>6}  {src}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
