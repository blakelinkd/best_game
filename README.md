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

`STEAM_USER_ID` may be either your SteamID64 or the numeric folder name under `Steam/userdata`.

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

To enable updates:

1. In your Twitch Developer Console, add this OAuth redirect URL:

```text
http://127.0.0.1:5000/auth/twitch/callback
```

2. Start the app and click **Connect Twitch**.
3. Approve the `channel:manage:broadcast` permission for your broadcaster account.

The app stores the user token and refresh token locally in `cache/twitch_user_token.json`, which is ignored by git. You do not need to paste `TWITCH_USER_ACCESS_TOKEN` manually.

Optionally, you may set a broadcaster login or ID as a fallback:

```env
TWITCH_BROADCASTER_ID=your_twitch_login_or_user_id_here
```

`TWITCH_BROADCASTER_ID` may be either your numeric Twitch user ID or your Twitch login name, for example `biotachyonic`. The app updates:

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
