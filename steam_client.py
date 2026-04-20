import json
import os
import time
from typing import List, Dict, Optional
import requests
from ratelimit import limits, sleep_and_retry
from steam.webauth import WebAuth
from config import config


class SteamClient:
    """Client for interacting with Steam APIs"""

    OWNED_GAMES_URL = "https://api.steampowered.com/IPlayerService/GetOwnedGames/v0001/"
    STORE_APPDETAILS_URL = "https://store.steampowered.com/api/appdetails"
    
    def __init__(self, api_key: Optional[str] = None, username: Optional[str] = None, 
                 password: Optional[str] = None):
        """
        Initialize Steam client.
        
        Args:
            api_key: Steam WebAPI key (optional)
            username: Steam username (optional, for owned games)
            password: Steam password (optional, for owned games)
        """
        self.api_key = api_key or config.STEAM_API_KEY
        self.username = username
        self.password = password
        self.api = None
        self.auth = None

        if username and password:
            self._authenticate()

    def _safe_error_message(self, error: Exception) -> str:
        """Return an exception message with known Steam secrets redacted."""
        message = str(error)
        if self.api_key:
            message = message.replace(self.api_key, "[redacted]")
        return message

    def _request_with_backoff(self, method: str, url: str, **kwargs) -> requests.Response:
        """Make an HTTP request and honor 429 Retry-After responses."""
        kwargs.setdefault("timeout", 30)

        for attempt in range(config.RATE_LIMIT_RETRY_ATTEMPTS):
            response = requests.request(method, url, **kwargs)
            if response.status_code != 429:
                return response

            retry_after = response.headers.get("Retry-After")
            try:
                delay = int(retry_after) if retry_after else 60
            except ValueError:
                delay = 60

            if attempt == config.RATE_LIMIT_RETRY_ATTEMPTS - 1:
                return response

            print(f"Steam rate limit reached; waiting {delay} seconds before retrying.")
            time.sleep(delay)

        return response

    @sleep_and_retry
    @limits(calls=config.STEAM_RATE_LIMIT, period=300)
    def _steam_webapi_get(self, url: str, params: Optional[Dict] = None, timeout: int = 30) -> requests.Response:
        """Rate-limited Steam Web API GET request."""
        return self._request_with_backoff("GET", url, params=params, timeout=timeout)

    @sleep_and_retry
    @limits(calls=config.STEAM_STORE_RATE_LIMIT, period=60)
    def _steam_store_get(self, url: str, **kwargs) -> requests.Response:
        """Rate-limited Steam Store GET request."""
        return self._request_with_backoff("GET", url, **kwargs)
    
    def _authenticate(self):
        """Authenticate with Steam using WebAuth"""
        try:
            self.auth = WebAuth(self.username)
            self.auth.login(self.password)
            print(f"Successfully authenticated as {self.username}")
        except Exception as e:
            print(f"Authentication failed: {e}")
            print("You can still use public APIs, but owned games may not be accessible.")
    
    def get_owned_games_via_api(self, steam_id: Optional[str] = None) -> List[Dict]:
        """
        Get owned games using Steam WebAPI (requires API key).
        
        Args:
            steam_id: Steam ID to get games for (defaults to authenticated user)
            
        Returns:
            List of game dictionaries with appid, name, playtime, etc.
        """
        if not self.api_key:
            raise ValueError("Steam WebAPI key is required for this method")
        
        try:
            steam_id = steam_id or (str(self.auth.steam_id) if self.auth else None)
            if not steam_id:
                raise ValueError("Steam ID is required")

            response = self._steam_webapi_get(
                self.OWNED_GAMES_URL,
                params={
                    "key": self.api_key,
                    "steamid": steam_id,
                    "include_appinfo": 1,
                    "include_played_free_games": 1,
                    "format": "json",
                }
            )
            response.raise_for_status()
            
            data = response.json()
            games = data.get('response', {}).get('games', [])
            return self._normalize_owned_games(games)
            
        except Exception as e:
            print(f"Error fetching owned games via API: {self._safe_error_message(e)}")
            return []
    
    def get_owned_games_via_webauth(self) -> List[Dict]:
        """
        Get owned games using WebAuth (requires authentication).
        
        Returns:
            List of game dictionaries with appid and name
        """
        if not self.auth:
            raise ValueError("Authentication is required for this method")
        
        try:
            # Use the internal API endpoint that WebAuth can access
            session = self.auth.session
            response = session.get(
                "https://steamcommunity.com/actions/GetOwnedApps",
                params={'sessionid': self.auth.session_id}
            )
            
            if response.status_code == 200:
                games = response.json()
                return self._normalize_owned_games(games)
            else:
                print(f"Failed to get owned games: {response.status_code}")
                return []
                
        except Exception as e:
            print(f"Error fetching owned games via WebAuth: {e}")
            return []
    
    def get_owned_games(self) -> List[Dict]:
        """
        Get owned games using available methods.
        Tries Steam Web API first, then WebAuth if available.
        
        Returns:
            List of game dictionaries with at least appid and name
        """
        games = []
        
        # Try WebAPI first if available
        if self.api_key and config.STEAMID64:
            games = self.get_owned_games_via_api(config.STEAMID64)
        
        # Fall back to WebAuth if no games from API
        if not games and self.auth:
            games = self.get_owned_games_via_webauth()
        
        # Local Steam config records local app state, not a reliable ownership list.
        # Keep it opt-in for diagnostics only so the dashboard is not built from installed games.
        if not games and config.ALLOW_LOCAL_APP_FALLBACK:
            print("Falling back to local Steam app list because ALLOW_LOCAL_APP_FALLBACK is enabled.")
            games = self._get_games_from_local_config()

        if not games and not config.ALLOW_LOCAL_APP_FALLBACK:
            print("No owned games returned by Steam.")
            if not self.api_key:
                print("  Set STEAM_API_KEY so Steam Web API can fetch your library.")
            if not config.STEAMID64:
                print("  Set STEAM_USER_ID to your SteamID64 or userdata account ID.")
            print("  Local installed games were not used as a fallback.")
        
        return games

    def get_installed_appids(self) -> set:
        """Return app IDs installed in any local Steam library folder."""
        installed = set()
        try:
            import vdf

            library_path = os.path.join(config.STEAM_INSTALL_PATH, "steamapps", "libraryfolders.vdf")
            if not os.path.exists(library_path):
                return installed

            with open(library_path, "r", encoding="utf-8") as f:
                data = vdf.load(f)

            folders = data.get("libraryfolders", {})
            for folder in folders.values():
                if not isinstance(folder, dict):
                    continue
                for appid in (folder.get("apps") or {}).keys():
                    try:
                        installed.add(int(appid))
                    except (TypeError, ValueError):
                        continue
        except Exception as e:
            print(f"Error reading installed Steam apps: {e}")
        return installed

    def _normalize_owned_games(self, games: List[Dict]) -> List[Dict]:
        """
        Normalize owned game payloads from Steam endpoints.

        Returns only entries that have a usable appid and a non-empty name so
        Twitch matching is based on owned game titles, not local App_ placeholders.
        """
        normalized = []
        seen_appids = set()

        for game in games or []:
            appid = game.get('appid') or game.get('app_id') or game.get('appID')
            name = game.get('name') or game.get('title')

            try:
                appid = int(appid)
            except (TypeError, ValueError):
                continue

            if not name or appid in seen_appids:
                continue

            normalized_game = dict(game)
            normalized_game['appid'] = appid
            normalized_game['name'] = str(name).strip()
            normalized.append(normalized_game)
            seen_appids.add(appid)

        return normalized
    
    def _get_games_from_local_config(self) -> List[Dict]:
        """
        Extract app list from local sharedconfig.vdf file.
        This is not an ownership source. It is only used when explicitly enabled.
        
        Returns:
            List of game dictionaries with appid
        """
        try:
            import vdf
            if not config.SHARED_CONFIG_PATH or not os.path.exists(config.SHARED_CONFIG_PATH):
                return []
            
            with open(config.SHARED_CONFIG_PATH, 'r', encoding='utf-8') as f:
                data = vdf.load(f)
            
            games = []
            apps = data.get('UserLocalConfigStore', {}).get('Software', {}).get('Valve', {}).get('Steam', {}).get('Apps', {})
            
            for appid_str, app_data in apps.items():
                try:
                    appid = int(appid_str)
                    games.append({
                        'appid': appid,
                        'name': f"App_{appid}"  # We don't have names from config
                    })
                except ValueError:
                    continue
            
            return games
            
        except Exception as e:
            print(f"Error reading local config: {e}")
            return []
    
    def get_game_details(self, appid: int) -> Optional[Dict]:
        """
        Get detailed information about a game.
        
        Args:
            appid: Steam app ID
            
        Returns:
            Game details dictionary or None if not found
        """
        try:
            if self.api_key:
                response = self._steam_webapi_get(
                    "https://api.steampowered.com/ISteamApps/GetAppList/v2/",
                    params={"format": "json"}
                )
                response.raise_for_status()
                apps = response.json().get('applist', {}).get('apps', [])
                for app in apps:
                    if app.get('appid') == appid:
                        return app
            
            # Fallback: Try to get from store page
            from bs4 import BeautifulSoup
            
            url = f"https://store.steampowered.com/app/{appid}/"
            response = self._steam_store_get(url, headers={'User-Agent': 'Mozilla/5.0'})
            
            if response.status_code == 200:
                soup = BeautifulSoup(response.content, 'html.parser')
                title_elem = soup.find('div', {'class': 'apphub_AppName'})
                if title_elem:
                    return {
                        'appid': appid,
                        'name': title_elem.text.strip()
                    }
            
            return None
            
        except Exception as e:
            print(f"Error getting game details for appid {appid}: {e}")
            return None

    def get_store_metadata(self, appid: int) -> Dict:
        """
        Get lightweight Steam Store metadata for a game.

        The owned-games WebAPI does not include tags. Steam Store appdetails
        exposes structured genres and categories, which are useful tag seeds.
        """
        try:
            response = self._steam_store_get(
                self.STORE_APPDETAILS_URL,
                params={
                    "appids": appid,
                    "filters": "basic,genres,categories",
                },
                headers={"User-Agent": "Mozilla/5.0"},
            )
            response.raise_for_status()
            payload = response.json().get(str(appid), {})
            if not payload.get("success"):
                return {"genres": [], "categories": [], "tags": [], "short_description": ""}

            data = payload.get("data") or {}
            genres = [
                item.get("description", "").strip()
                for item in data.get("genres", [])
                if item.get("description")
            ]
            categories = [
                item.get("description", "").strip()
                for item in data.get("categories", [])
                if item.get("description")
            ]

            tags = []
            seen = set()
            for value in genres + categories:
                normalized = " ".join(value.split())
                key = normalized.lower()
                if normalized and key not in seen:
                    tags.append(normalized)
                    seen.add(key)
            
            # Extract short description (brief one-liner about the game)
            short_description = data.get("short_description", "").strip()
            # Limit length to avoid overly long descriptions
            if len(short_description) > 500:
                short_description = short_description[:497] + "..."

            return {
                "genres": genres,
                "categories": categories,
                "tags": tags,
                "short_description": short_description,
            }
        except Exception as e:
            print(f"Error getting Steam Store metadata for appid {appid}: {self._safe_error_message(e)}")
            return {"genres": [], "categories": [], "tags": [], "short_description": ""}
    
    def get_game_names(self, appids: List[int]) -> Dict[int, str]:
        """
        Get game names for a list of appids.
        
        Args:
            appids: List of Steam app IDs
            
        Returns:
            Dictionary mapping appid to game name
        """
        game_names = {}
        
        for appid in appids:
            details = self.get_game_details(appid)
            if details and 'name' in details:
                game_names[appid] = details['name']
            else:
                game_names[appid] = f"App_{appid}"
        
        return game_names
    
    def test_connection(self) -> bool:
        """Test if we can connect to Steam APIs"""
        try:
            if self.api_key:
                # Try a simple API call without constructing steam.webapi.WebAPI,
                # which fetches interface metadata during initialization.
                response = self._steam_webapi_get(
                    "https://api.steampowered.com/ISteamWebAPIUtil/GetServerInfo/v1/",
                    params={"key": self.api_key, "format": "json"},
                    timeout=15
                )
                response.raise_for_status()
                return True
            elif self.auth:
                # Check if authenticated
                return self.auth.logged_in
            elif config.ALLOW_LOCAL_APP_FALLBACK:
                # Check if we can access local config
                return os.path.exists(config.SHARED_CONFIG_PATH) if config.SHARED_CONFIG_PATH else False
            else:
                return False
        except:
            return False


if __name__ == "__main__":
    # Test the Steam client
    client = SteamClient()
    
    if client.test_connection():
        print("Steam connection test: PASSED")
        
        # Get owned games
        games = client.get_owned_games()
        print(f"Found {len(games)} owned games")
        
        if games:
            # Show first 5 games
            for game in games[:5]:
                print(f"  - {game.get('name', 'Unknown')} (AppID: {game.get('appid')})")
    else:
        print("Steam connection test: FAILED")
        print("Please check your configuration and authentication.")
