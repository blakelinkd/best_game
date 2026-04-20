import time
import requests
from typing import Dict, List, Optional, Tuple
from urllib.parse import urlencode
from ratelimit import limits, sleep_and_retry
from config import config

_app_token_cache = {}
_app_token_key = "app_token"


class TwitchClient:
    """Client for interacting with Twitch APIs"""

    def __init__(
        self,
        client_id: Optional[str] = None,
        client_secret: Optional[str] = None,
        ensure_app_token: bool = True,
    ):
        """
        Initialize Twitch client.
        
        Args:
            client_id: Twitch client ID
            client_secret: Twitch client secret
        """
        self.client_id = config.TWITCH_CLIENT_ID if client_id is None else client_id
        self.client_secret = config.TWITCH_CLIENT_SECRET if client_secret is None else client_secret
        if ensure_app_token:
            self._ensure_token_valid()

    def _safe_error_message(self, error: Exception) -> str:
        """Return an exception message with known Twitch credentials redacted."""
        message = str(error)
        for secret in (self.client_secret, self.access_token):
            if secret:
                message = message.replace(secret, "[redacted]")
        if config.TWITCH_USER_ACCESS_TOKEN:
            message = message.replace(config.TWITCH_USER_ACCESS_TOKEN, "[redacted]")
        return message

    def _rate_limit_delay(self, response: requests.Response) -> int:
        """Get wait time from Twitch rate-limit headers."""
        retry_after = response.headers.get('Retry-After')
        if retry_after:
            try:
                return max(int(retry_after), 1)
            except ValueError:
                pass

        reset = response.headers.get('Ratelimit-Reset')
        if reset:
            try:
                return max(int(float(reset) - time.time()), 1)
            except ValueError:
                pass

        return 60

    def _request_with_backoff(self, method: str, url: str, **kwargs) -> requests.Response:
        """Make an HTTP request and honor Twitch 429 rate-limit responses."""
        kwargs.setdefault('timeout', 30)

        for attempt in range(config.RATE_LIMIT_RETRY_ATTEMPTS):
            response = requests.request(method, url, **kwargs)
            if response.status_code != 429:
                return response

            delay = self._rate_limit_delay(response)
            if attempt == config.RATE_LIMIT_RETRY_ATTEMPTS - 1:
                return response

            print(f"Twitch rate limit reached; waiting {delay} seconds before retrying.")
            time.sleep(delay)

        return response
    
    @property
    def access_token(self) -> Optional[str]:
        cached = _app_token_cache.get(_app_token_key)
        if cached and time.time() < cached.get("expiry", 0) - 60:
            return cached.get("token")
        return None

    @access_token.setter
    def access_token(self, value: Optional[str]):
        if value:
            _app_token_cache[_app_token_key] = {
                "token": value,
                "expiry": time.time() + 3600,
            }

    @sleep_and_retry
    @limits(calls=config.TWITCH_AUTH_RATE_LIMIT, period=60)
    def _get_access_token(self) -> bool:
        """Get OAuth access token from Twitch"""
        if self.access_token:
            return True

        try:
            response = self._request_with_backoff(
                'POST',
                config.TWITCH_TOKEN_URL,
                data={
                    'client_id': self.client_id,
                    'client_secret': self.client_secret,
                    'grant_type': 'client_credentials'
                }
            )
            
            if response.status_code == 200:
                data = response.json()
                self.access_token = data['access_token']
                print("Successfully obtained Twitch access token")
                return True
            else:
                print(f"Failed to get Twitch token: {response.status_code}")
                return False
                
        except Exception as e:
            print(f"Error getting Twitch access token: {self._safe_error_message(e)}")
            return False
    
    def _ensure_token_valid(self) -> bool:
        """Ensure access token is valid, refresh if needed"""
        if not self.access_token:
            return self._get_access_token()
        return True

    def get_authorization_url(self, redirect_uri: str, state: str) -> str:
        query = urlencode({
            'response_type': 'code',
            'client_id': self.client_id,
            'redirect_uri': redirect_uri,
            'scope': config.TWITCH_AUTH_SCOPE,
            'state': state,
            'force_verify': 'true',
        })
        return f"{config.TWITCH_AUTHORIZE_URL}?{query}"

    def exchange_code_for_user_token(self, code: str, redirect_uri: str) -> Dict:
        response = self._request_with_backoff(
            'POST',
            config.TWITCH_TOKEN_URL,
            data={
                'client_id': self.client_id,
                'client_secret': self.client_secret,
                'code': code,
                'grant_type': 'authorization_code',
                'redirect_uri': redirect_uri,
            }
        )
        if response.status_code != 200:
            raise RuntimeError(f"Twitch token exchange failed ({response.status_code}): {response.text}")
        return response.json()

    def refresh_user_token(self, refresh_token: str) -> Dict:
        response = self._request_with_backoff(
            'POST',
            config.TWITCH_TOKEN_URL,
            data={
                'client_id': self.client_id,
                'client_secret': self.client_secret,
                'grant_type': 'refresh_token',
                'refresh_token': refresh_token,
            }
        )
        if response.status_code != 200:
            raise RuntimeError(f"Twitch token refresh failed ({response.status_code}): {response.text}")
        return response.json()

    def validate_user_token(self, access_token: str) -> Dict:
        response = self._request_with_backoff(
            'GET',
            config.TWITCH_VALIDATE_URL,
            headers={'Authorization': f'OAuth {access_token}'}
        )
        if response.status_code != 200:
            raise RuntimeError(f"Twitch token validation failed ({response.status_code}): {response.text}")
        return response.json()
    
    @sleep_and_retry
    @limits(calls=config.TWITCH_RATE_LIMIT, period=60)  # 800 calls per minute
    def _make_request(self, url: str, params: Optional[Dict] = None) -> Optional[Dict]:
        """Make authenticated request to Twitch API"""
        if not self._ensure_token_valid():
            return None
        
        headers = {
            'Client-ID': self.client_id,
            'Authorization': f'Bearer {self.access_token}'
        }
        
        try:
            response = self._request_with_backoff('GET', url, headers=headers, params=params)
            
            if response.status_code == 200:
                return response.json()
            elif response.status_code == 401:  # Token expired
                print("Token expired, refreshing...")
                if self._get_access_token():
                    # Retry with new token
                    headers['Authorization'] = f'Bearer {self.access_token}'
                    response = self._request_with_backoff('GET', url, headers=headers, params=params)
                    if response.status_code == 200:
                        return response.json()
            else:
                print(f"Twitch API error {response.status_code}: {response.text}")
                
        except Exception as e:
            print(f"Error making Twitch API request: {self._safe_error_message(e)}")
        
        return None

    @sleep_and_retry
    @limits(calls=config.TWITCH_RATE_LIMIT, period=60)
    def _make_user_request(
        self,
        method: str,
        url: str,
        access_token: str,
        params: Optional[Dict] = None,
        json_body: Optional[Dict] = None,
    ) -> requests.Response:
        headers = {
            'Client-ID': self.client_id,
            'Authorization': f'Bearer {access_token}',
        }
        if json_body is not None:
            headers['Content-Type'] = 'application/json'

        return self._request_with_backoff(
            method,
            url,
            headers=headers,
            params=params,
            json=json_body,
        )
    
    def search_games(self, game_name: str) -> List[Dict]:
        """
        Search for games on Twitch by name.
        
        Args:
            game_name: Game name to search for
            
        Returns:
            List of matching games with id, name, etc.
        """
        params = {
            'query': game_name,
            'first': 10  # Limit results
        }
        
        data = self._make_request(config.TWITCH_SEARCH_CATEGORIES_URL, params)
        if data and 'data' in data:
            return [game for game in data['data'] if game.get('id') and game.get('name')]
        return []

    def get_user_id(self, login: str) -> Optional[str]:
        if not login:
            return None

        data = self._make_request(config.TWITCH_USERS_URL, {'login': login.lower()})
        users = data.get('data', []) if data else []
        return users[0].get('id') if users else None

    def resolve_broadcaster_id(self, broadcaster: Optional[str] = None) -> Optional[str]:
        broadcaster = (broadcaster or config.TWITCH_BROADCASTER_ID or '').strip()
        if not broadcaster:
            return None
        if broadcaster.isdigit():
            return broadcaster
        return self.get_user_id(broadcaster)

    def get_channel_info(self, broadcaster_id: Optional[str] = None) -> Optional[Dict]:
        broadcaster_id = self.resolve_broadcaster_id(broadcaster_id)
        if not broadcaster_id:
            return None

        data = self._make_request(config.TWITCH_CHANNELS_URL, {'broadcaster_id': broadcaster_id})
        channels = data.get('data', []) if data else []
        return channels[0] if channels else None

    def update_channel_info(
        self,
        broadcaster_id: str,
        access_token: str,
        updates: Dict,
    ) -> bool:
        if not broadcaster_id:
            raise ValueError("TWITCH_BROADCASTER_ID is required to update stream info.")
        if not access_token:
            raise ValueError("TWITCH_USER_ACCESS_TOKEN is required to update stream info.")
        if not updates:
            raise ValueError("At least one stream info field is required.")

        response = self._make_user_request(
            'PATCH',
            config.TWITCH_CHANNELS_URL,
            access_token,
            params={'broadcaster_id': broadcaster_id},
            json_body=updates,
        )

        if response.status_code == 204:
            return True

        raise RuntimeError(f"Twitch update failed ({response.status_code}): {response.text}")
    
    def get_game_id(self, game_name: str) -> Optional[str]:
        """
        Get Twitch game ID for a game name.
        
        Args:
            game_name: Game name to look up
            
        Returns:
            Twitch game ID or None if not found
        """
        data = self._make_request(config.TWITCH_GAMES_URL, {'name': game_name})
        games = data.get('data', []) if data else []
        if games:
            return games[0]['id']

        games = self.search_games(game_name)
        if games:
            return games[0]['id']
        return None
    
    def get_game_viewer_count(self, game_id: str) -> int:
        """
        Get total viewer count for a specific game.
        
        Args:
            game_id: Twitch game ID
            
        Returns:
            Total number of viewers across all streams for this game
        """
        params = {
            'game_id': game_id,
            'first': 100  # Max per page
        }
        
        total_viewers = 0
        cursor = None
        
        while True:
            if cursor:
                params['after'] = cursor
            
            data = self._make_request(config.TWITCH_STREAMS_URL, params)
            if not data or 'data' not in data:
                break
            
            # Sum viewer counts for this page
            for stream in data['data']:
                total_viewers += stream.get('viewer_count', 0)
            
            # Check for more pages
            if 'pagination' in data and 'cursor' in data['pagination']:
                cursor = data['pagination']['cursor']
            else:
                break
        
        return total_viewers

    def get_game_viewer_counts(self, game_ids: List[str]) -> Dict[str, int]:
        """
        Get total viewer counts for multiple Twitch categories.

        Twitch Helix accepts up to 100 game_id query parameters per streams
        request. The response is paginated across all requested categories, so
        each chunk is paged until exhausted to keep totals exact.
        """
        unique_ids = []
        seen = set()
        for game_id in game_ids:
            game_id = str(game_id)
            if game_id and game_id not in seen:
                unique_ids.append(game_id)
                seen.add(game_id)

        viewer_counts = {game_id: 0 for game_id in unique_ids}
        chunk_size = 100

        for offset in range(0, len(unique_ids), chunk_size):
            chunk = unique_ids[offset:offset + chunk_size]
            cursor = None

            while True:
                params = [("first", 100)]
                params.extend(("game_id", game_id) for game_id in chunk)
                if cursor:
                    params.append(("after", cursor))

                data = self._make_request(config.TWITCH_STREAMS_URL, params)
                if not data or "data" not in data:
                    break

                for stream in data["data"]:
                    game_id = str(stream.get("game_id", ""))
                    if game_id in viewer_counts:
                        viewer_counts[game_id] += int(stream.get("viewer_count", 0) or 0)

                cursor = data.get("pagination", {}).get("cursor")
                if not cursor:
                    break

        return viewer_counts
    
    def get_top_games(self, limit: int = 100) -> List[Dict]:
        """
        Get top games by viewer count.
        
        Args:
            limit: Maximum number of games to return
            
        Returns:
            List of games with id, name, and viewer count
        """
        # First get all active streams
        params = {
            'first': 100  # Max per page
        }
        
        game_viewers = {}  # game_id -> total viewers
        cursor = None
        
        while True:
            if cursor:
                params['after'] = cursor
            
            data = self._make_request(config.TWITCH_STREAMS_URL, params)
            if not data or 'data' not in data:
                break
            
            # Aggregate viewer counts by game
            for stream in data['data']:
                game_id = stream.get('game_id')
                game_name = stream.get('game_name')
                viewer_count = stream.get('viewer_count', 0)
                
                if game_id and game_name:
                    if game_id not in game_viewers:
                        game_viewers[game_id] = {
                            'id': game_id,
                            'name': game_name,
                            'viewer_count': 0
                        }
                    game_viewers[game_id]['viewer_count'] += viewer_count
            
            # Check if we have enough games
            if len(game_viewers) >= limit * 2:  # Get extra for filtering
                break
            
            # Check for more pages
            if 'pagination' in data and 'cursor' in data['pagination']:
                cursor = data['pagination']['cursor']
            else:
                break
        
        # Convert to list and sort by viewer count
        games = list(game_viewers.values())
        games.sort(key=lambda x: x['viewer_count'], reverse=True)
        
        return games[:limit]
    
    def get_viewer_counts_for_games(self, game_names: List[str]) -> Dict[str, int]:
        """
        Get viewer counts for a list of game names.
        
        Args:
            game_names: List of game names to check
            
        Returns:
            Dictionary mapping game names to viewer counts
        """
        viewer_counts = {}
        
        for game_name in game_names:
            # First get the game ID
            game_id = self.get_game_id(game_name)
            if game_id:
                viewer_count = self.get_game_viewer_count(game_id)
                viewer_counts[game_name] = viewer_count
            else:
                viewer_counts[game_name] = 0
                print(f"Could not find Twitch game ID for: {game_name}")
            
            # Be nice to the API
            time.sleep(0.1)
        
        return viewer_counts
    
    def batch_get_viewer_counts(self, steam_games: List[Dict]) -> List[Dict]:
        """
        Get viewer counts for Steam games.
        
        Args:
            steam_games: List of Steam game dictionaries with appid and name
            
        Returns:
            List of games with added viewer_count field
        """
        results = []
        
        for game in steam_games:
            game_name = game.get('name', f"App_{game.get('appid')}")
            game_id = self.get_game_id(game_name)
            
            if game_id:
                viewer_count = self.get_game_viewer_count(game_id)
                game['viewer_count'] = viewer_count
                game['twitch_game_id'] = game_id
            else:
                game['viewer_count'] = 0
                game['twitch_game_id'] = None
            
            results.append(game)
            
            # Be nice to the API
            time.sleep(0.05)
        
        return results
    
    def test_connection(self) -> bool:
        """Test if we can connect to Twitch APIs"""
        try:
            if not self._ensure_token_valid():
                return False
            
            # Try a simple API call
            data = self._make_request(config.TWITCH_GAMES_URL, {'id': '493057'})  # PUBG
            return data is not None and 'data' in data
        except:
            return False


if __name__ == "__main__":
    # Test the Twitch client
    client = TwitchClient()
    
    if client.test_connection():
        print("Twitch connection test: PASSED")
        
        # Test getting viewer count for a popular game
        test_games = ["Counter-Strike: Global Offensive", "Dota 2", "Apex Legends"]
        
        for game_name in test_games:
            game_id = client.get_game_id(game_name)
            if game_id:
                viewers = client.get_game_viewer_count(game_id)
                print(f"{game_name}: {viewers:,} viewers")
            else:
                print(f"{game_name}: Not found on Twitch")
    else:
        print("Twitch connection test: FAILED")
        print("Please check your TWITCH_CLIENT_ID and TWITCH_CLIENT_SECRET in .env file")
