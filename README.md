# Steam Twitch Viewer Dashboard

Shows your owned Steam games with current Twitch viewer counts in a local Flask web app.

## Features

- Fetches owned Steam games from Steam Web API.
- Matches Steam game names to Twitch categories.
- Shows only games with live Twitch viewers by default.
- Processes games in most-recently-played order and adds cards as each game finishes.
- Sorts visible cards by recently played, installed status, or viewer count.
- Displays responsive Steam-style game cards with Steam header images.
- Opens the dashboard in your browser when launched.
- Caches owned game data, Twitch category matches, short-lived viewer counts, and downloaded images.

## Requirements

- Python 3.8 or higher
- Steam Web API key
- Steam user ID
- Twitch Developer App client ID and secret

## Setup

Install dependencies:

```powershell
python setup.py
```

Create a `.env` file or set Windows environment variables:

```env
STEAM_USER_ID=your_steam_user_id_here
STEAM_API_KEY=your_steam_webapi_key_here
TWITCH_CLIENT_ID=your_twitch_client_id_here
TWITCH_CLIENT_SECRET=your_twitch_client_secret_here
```

You can also start the app and open **Settings** from the dashboard. The settings page writes credentials and local paths to `.env`, which is ignored by git so release packages do not include your personal keys.

`STEAM_USER_ID` may be your Steam custom profile name, Steam profile URL, SteamID64, or the numeric folder name under `Steam/userdata`. If you enter a custom profile name in Settings, the app resolves it with Steam's `ResolveVanityURL` API after your Steam Web API key is saved.

### Retro console libraries

Retro platforms are optional and are disabled unless `RETRO_SYSTEMS` is set. Build the local retro catalog first:

```powershell
python retro_collector.py
```

Then enable every supported system, or a comma-separated subset:

```env
RETRO_SYSTEMS=all
# RETRO_SYSTEMS=nes,snes,n64,genesis,ps1
```

The dashboard reads `cache/retro_games.json`, treats each console as its own platform, and uses libretro thumbnail URLs directly instead of downloading every retro image into `static/cache/`.

## Run

Start the app and open the browser:

```powershell
python main.py
```

Use a different port:

```powershell
python main.py --port 5050
```

Run without opening the browser:

```powershell
python main.py --no-browser
```

## Twitch Login And Stream Updates

Each game card has an **Update stream info** button. It opens a modal with the Twitch category filled from the matched game, title/language filled from the current channel when available, and Twitch-safe tags seeded from cached Steam Store metadata for that game. Tags are normalized to Twitch's channel tag rules: no spaces, no special characters, max 25 characters each, deduplicated, and capped at 10 tags.

The **AI tags** button uses LM Studio's OpenAI-compatible local server. Start the server in LM Studio before clicking it. By default the app connects to `http://127.0.0.1:1234` and uses the first model LM Studio reports as loaded. You can override either value in `.env`:

```env
LM_STUDIO_BASE_URL=http://127.0.0.1:1234
LM_STUDIO_MODEL=
LM_STUDIO_TIMEOUT=30
```

If the Flask app is running in Ubuntu but LM Studio is running somewhere else, set `LM_STUDIO_BASE_URL` to the address that Ubuntu can reach.

To enable updates:

1. Create a Twitch app in the Developer Console.
2. Add the OAuth redirect URL shown on the Settings page. The default local URL is:

```text
https://localhost:5000/auth/twitch/callback
```

The app runs locally over HTTPS by default because Twitch rejects non-HTTPS redirect URLs during application registration. Your browser may show a localhost certificate warning the first time you open the app. This is expected for the local self-signed certificate; continue to localhost to finish Twitch setup.

3. Keep the Twitch app client type set to **Confidential**.
4. Open the app's **Manage** page, copy the Client ID, click **New Secret**, and copy the secret.
5. Paste both values into this app's Settings page.
6. Start the app and click **Connect Twitch**.
7. Approve the stream update permission for your broadcaster account.

The app stores the Twitch login token locally in `cache/twitch_user_token.json`, which is ignored by git.

Optionally, you may set your Twitch username as a fallback:

```env
TWITCH_BROADCASTER_ID=your_twitch_username_here
```

For example, `TWITCH_BROADCASTER_ID=biotachyonic`. The app updates:

- Twitch category/game
- Stream title
- Broadcast language
- Tags
- Branded content flag

Twitch's official channel update API does not currently expose the Go Live Notification text, so the app cannot set that field through the same update request.

## Caching

The app keeps API and asset data locally so repeat loads avoid work that has already been done:

- Owned Steam games are cached for 24 hours by default.
- Steam Store genres/categories used for stream tag suggestions are cached for 24 hours by default.
- Twitch category matches are cached until you force a refresh.
- Twitch viewer counts are cached for 10 minutes by default.
- Steam header images and Twitch box art are downloaded once to `static/cache/`.

When the cache is warm and viewer counts are still fresh, the dashboard renders directly without starting a background scan. Otherwise the page renders immediately and cards are added as each game is processed.

Use the page's **Force API refresh** checkbox to bypass the data cache for a run. Cached images are still reused if the files already exist.

You can tune cache durations in `.env`:

```env
OWNED_GAMES_CACHE_TTL=86400
VIEWER_COUNT_CACHE_TTL=600
```

## How It Works

1. Steam Web API returns your owned games.
2. Games are processed in descending `rtime_last_played` order.
3. Steam Store metadata provides genre/category tag suggestions for each shown game.
4. Twitch Search Categories finds the matching Twitch category for each Steam title.
5. Twitch Streams returns current live viewer counts for each matched category.
6. Games with `0` viewers are filtered out by default.
7. The browser adds cards as they arrive and can sort them locally.

## Rate Limits

The clients throttle API calls and retry after `429` responses:

- Steam Web API: `300` requests per 5 minutes
- Steam Store fallback: `60` requests per minute
- Twitch Helix: `800` requests per minute
- Twitch auth token requests: `20` requests per minute

## Notes

- This app does not modify Steam files or collections.
- Large libraries can take a while on the first scan, but cached runs reuse owned games, Twitch matches, Steam metadata, and images.
- `cache/` and `static/cache/` are generated locally and ignored by git.
