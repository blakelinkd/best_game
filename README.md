# Best Game — Find what to stream on Twitch

<p align="center">
  <a href="https://www.twitch.tv/biotachyonic" target="_blank" rel="noopener noreferrer">
    <img src="https://img.shields.io/badge/Twitch-biotachyonic-9146FF?style=for-the-badge&logo=twitch&logoColor=white&labelColor=1a1a2e" alt="Watch biotachyonic on Twitch">
  </a>
</p>

**Stop guessing what game to stream.** Best Game reads your Steam, GOG,
and Epic libraries, checks which of your games people are watching *right now*
on Twitch, and ranks them by discovery potential—so you always pick the game
that gives your stream the best shot at finding new viewers.

---

## What is Best Game?

You own hundreds of games. At any given moment a handful of them have real
audiences on Twitch while most have zero viewers. Best Game answers the
question every variety streamer asks before hitting "Go Live":

> *Which game in my library should I play today?*

It works by scanning your Steam library (plus GOG, Epic, and retro consoles
if you want), matching every game to its Twitch category, pulling live viewer
counts, and showing you a dashboard of game cards sorted by how likely a small
streamer is to pick up organic viewers.

It also tracks viewer history over time so you can spot patterns, discover
games with proven audiences that happen to have zero active streams right
now, and even update your Twitch stream info directly from the dashboard.

Everything runs **locally on your PC**. No accounts to create, no data leaves
your machine.

---

## Features

- **Multi-platform game library** — Steam, GOG, Epic Games, and retro
  consoles (NES, SNES, PS1, PS2, Genesis, N64, and more)
- **Live Twitch viewer counts** — See exactly how many people are watching
  each game in your library on Twitch right now
- **Discovery score** — Each game gets a score based on visibility, demand,
  audience depth, and competition; games with lower scores are hidden so you
  only see what's worth streaming
- **Historical charts** — Line charts and day-by-hour heatmaps so you can see
  *when* games peak on Twitch
- **Opportunity finder** — Games with proven historical viewership but zero
  active streams right now: the perfect time to jump in
- **Update your Twitch stream** — Change your game/category, stream title,
  tags, and language directly from the dashboard
- **AI tag suggestions** — Optional local LLM (Ollama) generates
  Twitch-friendly tags tailored to each game
- **Current game detection** — Dashboard highlights the game you're currently
  playing (reads Steam + window title)
- **Search and filter** — Search by title, tags, or description; filter by
  installed, on-sale, recently played
- **Tracking every 15 minutes** — Background collector records viewer counts
  so historical data builds up automatically while the app is running

---

## Quick Start

### 1. Download the project

Click the green **Code** button at the top of this page and select
**Download ZIP**. Extract the ZIP anywhere on your PC.

### 2. Run the setup script

Double-click **`setup_windows.bat`** (or `setup_windows_ps.bat` if you prefer
PowerShell).

The setup script will:
- Download a portable copy of Python 3.13 (nothing installs system-wide)
- Create an isolated virtual environment
- Install all required Python packages
- Install Playwright's Chromium browser (used for fetching game metadata)

> **Nothing is installed outside the project folder.** If you want to remove
> the app later, just delete the folder.

### 3. Launch the dashboard

Double-click **`run.bat`**. Your browser will open automatically to the
dashboard.

The first time you open it your browser may show a "connection is not
private" warning. Click **Advanced → Proceed to localhost**. This is normal:
the app runs over HTTPS locally because Twitch requires HTTPS for OAuth
logins.

---

## Setting Up Your Credentials

The dashboard needs a few free API keys to work. You set these once from the
**Settings** page (gear icon in the top-right corner of the dashboard).

### Steam Web API Key

1. Go to [https://steamcommunity.com/dev/apikey](https://steamcommunity.com/dev/apikey)
2. Log in with your Steam account
3. Enter any domain name (e.g. `localhost`) and agree to the terms
4. Copy the key and paste it into Settings → **Steam API Key**

### Steam User ID

Your Steam User ID tells the app whose game library to fetch. You can use:

- Your **Steam profile name** (the custom URL name, e.g. `gabelogannewell`)
- Your **SteamID64** (a 17-digit number)
- The numeric folder name inside `Steam/userdata/`

The Settings page can resolve custom profile names automatically.

### Twitch Client ID & Secret

1. Go to [https://dev.twitch.tv/console](https://dev.twitch.tv/console)
2. Sign in with your Twitch account
3. Click **Register Your Application**
4. Give it any name (e.g. "Best Game")
5. Set the OAuth Redirect URL to: `https://localhost:5000/auth/twitch/callback`
6. Choose **Confidential** as the client type
7. After creating the app, click **New Secret** and copy both the Client ID
   and Client Secret
8. Paste them into Settings

### Connect Twitch (optional)

After entering your Twitch credentials, click the **Connect Twitch** button
in Settings. This lets the dashboard update your stream's game/category,
title, tags, and language with one click.

---

## Using the Dashboard

Once your credentials are saved, the dashboard loads automatically. Game
cards appear as they're processed—newest first, then your most recently
played games first.

**Each game card shows:**
- Game title and header image
- Current Twitch viewer count and stream count
- Discovery score (higher = better chance of organic viewers)
- Whether the game is installed
- Genre tags (click to filter)
- Sale status if the game is discounted

**Sort** cards by viewers, discovery score, recently played, name, or
installed status. **Search** by title, tags, or description to narrow down
your options.

**Tabs across the top:**
- **Live Games** — games in your library with active Twitch viewers
- **Charts** — historical viewer trends and heatmaps
- **Opportunities** — games with proven viewership but zero active streams

---

## For Advanced Users

### Running from the command line

```bash
python_venv\Scripts\python.exe main.py
# or activate the venv first:
python_venv\Scripts\activate.bat
python main.py
```

### Command-line options

| Flag | Description |
|------|-------------|
| `--port 5050` | Use a different port (default: 5000) |
| `--host 0.0.0.0` | Listen on all interfaces |
| `--no-browser` | Don't open the browser automatically |
| `--http` | Run without HTTPS (disables Twitch OAuth) |

### Environment variables (.env file)

All credentials and settings live in a `.env` file at the project root. The
Settings page writes this file automatically, but you can also create it
manually:

```env
STEAM_API_KEY=your_steam_webapi_key_here
STEAM_USER_ID=your_steam_profile_name_or_id_here
TWITCH_CLIENT_ID=your_twitch_client_id_here
TWITCH_CLIENT_SECRET=your_twitch_client_secret_here
```

**Optional platform settings:**

```env
ENABLED_PLATFORMS=steam,gog,epic
GOG_DB_PATH=C:\ProgramData\GOG.com\Galaxy\storage\galaxy-2.0.db
EPIC_CATALOG_PATH=C:\ProgramData\Epic\EpicGamesLauncher\Data\Catalog
RETRO_SYSTEMS=all
```

**Optional AI tag generation (requires [Ollama](https://ollama.com)):**

```env
OLLAMA_BASE_URL=http://127.0.0.1:11434
OLLAMA_MODEL=qwen3.5:4b
OLLAMA_TIMEOUT=60
```

**Cache tuning:**

```env
OWNED_GAMES_CACHE_TTL=86400
VIEWER_COUNT_CACHE_TTL=600
```

### Retro console libraries

Build the retro game catalog first, then enable it:

```bash
python_venv\Scripts\python.exe retro_collector.py
```

Then add to `.env`:

```env
RETRO_SYSTEMS=all
# or pick specific consoles: nes,snes,n64,genesis,ps1,ps2
```

### Data & cache locations

| Path | Contents |
|------|----------|
| `cache/viewer_cache.json` | Main game + viewer data cache |
| `cache/viewer_history.db` | SQLite historical snapshot database |
| `cache/twitch_user_token.json` | Twitch OAuth token (keep private) |
| `static/cache/` | Downloaded game images |
| `.env` | Your API keys and settings (keep private) |

All `cache/` and `.env` files are gitignored—your credentials and cache data
stay local.

---

## Setup Scripts Explained

| Script | What it does |
|--------|-------------|
| `setup_windows.bat` | Full setup in a command prompt: downloads portable Python 3.13, creates a virtual environment, installs all dependencies. **Recommended for most users.** |
| `setup_windows.ps1` | Same as above but in PowerShell with colored output. |
| `setup_windows_ps.bat` | Tiny helper that runs `setup_windows.ps1` with the correct execution policy. |
| `run.bat` | Launches the dashboard. Detects the virtual environment automatically. |
| `setup_deps.py` | Called by the scripts above. Upgrades pip, installs all packages from `requirements.txt`, installs Playwright Chromium. |

---

## How It Works

1. Steam Web API returns your owned games
2. Games are matched to Twitch categories via the Twitch Helix API
3. Live viewer counts are fetched for each matched category
4. Store metadata (genres, tags, descriptions) enriches each game card
5. A discovery score is calculated based on visibility, demand, audience
   depth, and top-stream concentration
6. Game cards are rendered in the browser, sorted by your chosen criteria
7. A background collector snapshots viewer counts every 15 minutes for
   historical charts

### API Rate Limits

The app throttles requests to stay within provider limits:

| Service | Limit |
|---------|-------|
| Steam Web API | 300 requests per 5 minutes |
| Steam Store | 60 requests per minute |
| Twitch Helix | 800 requests per minute |
| Twitch Auth | 20 requests per minute |

---

## Requirements

- Windows 10 or later (the setup scripts are Windows-only; Python source
  runs on any OS with Python 3.8+)
- A Steam account (free)
- A Twitch account (free)
- Internet connection

---

*This app does not modify Steam files or game collections. It only reads
your public game library and Twitch category data.*
