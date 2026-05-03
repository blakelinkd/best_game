import abc
import json
import os
import time
from typing import Dict, List, Optional, Set
import requests
from ratelimit import limits, sleep_and_retry


class PlatformClient(abc.ABC):
    """Abstract base class for game platform clients."""
    
    @property
    @abc.abstractmethod
    def platform_name(self) -> str:
        """Return the platform identifier (e.g., 'steam', 'gog', 'epic')."""
        pass
    
    @abc.abstractmethod
    def get_owned_games(self, force_refresh: bool = False) -> List[Dict]:
        """
        Get list of games owned on this platform.
        
        Returns:
            List of game dictionaries with at least:
                - appid: unique identifier on this platform (string or int)
                - name: game title
                - playtime_forever: optional total playtime in minutes
                - rtime_last_played: optional timestamp of last play
                - installed: optional boolean
        """
        pass
    
    @abc.abstractmethod
    def get_installed_appids(self) -> Set[str]:
        """Return set of installed app IDs on this platform."""
        pass
    
    def get_store_metadata(self, appid: str) -> Dict:
        """
        Get platform-specific store metadata for a game.
        
        Used for tag suggestions, genres, etc. Default implementation returns empty dict.
        
        Args:
            appid: Platform-specific game identifier
            
        Returns:
            Dictionary with metadata fields like genres, categories, tags, short_description, description
        """
        return {
            "genres": [],
            "categories": [],
            "tags": [],
            "short_description": "",
            "description": "",
            "release_date": "",
            "release_timestamp": 0,
            "release_coming_soon": False,
            "is_on_sale": False,
            "discount_percent": 0,
            "price_initial_formatted": "",
            "price_final_formatted": "",
        }
    
    @abc.abstractmethod
    def test_connection(self) -> bool:
        """Test if we can connect to this platform's APIs/data."""
        pass
    
    def get_image_url(self, appid: str) -> Optional[str]:
        """
        Get platform-specific image URL for a game.
        
        Args:
            appid: Platform-specific game identifier
            
        Returns:
            URL to game header/box art image, or None
        """
        return None
    
    def _normalize_game(self, game: Dict, default_name_prefix: str = "App_") -> Optional[Dict]:
        """
        Normalize a game dictionary to standard format.
        
        Args:
            game: Raw game data from platform
            default_name_prefix: Prefix for appid if name is missing
            
        Returns:
            Normalized game dict or None if invalid
        """
        # Extract appid from common field names
        appid = game.get('appid') or game.get('app_id') or game.get('appID') or game.get('id')
        if appid is None:
            return None
        
        # Extract name from common field names
        name = game.get('name') or game.get('title') or game.get('Title')
        if not name:
            name = f"{default_name_prefix}{appid}"
        
        # Convert appid to string for consistent caching
        appid_str = str(appid)
        
        normalized = {
            "appid": appid_str,
            "name": str(name).strip(),
            "platform": self.platform_name,
        }
        
        # Copy common optional fields
        for field in ['playtime_forever', 'rtime_last_played', 'last_played', 'playtime_2weeks']:
            if field in game:
                normalized[field] = game[field]
        
        # Set installed flag if available
        if 'installed' in game:
            normalized['installed'] = bool(game['installed'])
        
        return normalized
    
    def _safe_error_message(self, error: Exception) -> str:
        """Return an exception message with any API keys redacted."""
        return str(error)


class RateLimitedClientMixin:
    """Mixin for rate-limited HTTP requests."""
    
    def _request_with_backoff(self, method: str, url: str, max_retries: int = 3, **kwargs) -> requests.Response:
        """
        Make an HTTP request with backoff on 429 responses.
        
        Args:
            method: HTTP method
            url: Request URL
            max_retries: Maximum number of retry attempts
            **kwargs: Additional arguments for requests.request
            
        Returns:
            requests.Response object
        """
        kwargs.setdefault('timeout', 30)
        
        for attempt in range(max_retries):
            response = requests.request(method, url, **kwargs)
            if response.status_code != 429:
                return response
            
            retry_after = response.headers.get('Retry-After')
            try:
                delay = int(retry_after) if retry_after else 60
            except ValueError:
                delay = 60
            
            if attempt == max_retries - 1:
                return response
            
            print(f"Rate limit reached for {url}; waiting {delay} seconds before retrying.")
            time.sleep(delay)
        
        return response


# Platform-specific utility functions

def get_windows_appdata_paths() -> Dict[str, str]:
    """Get common Windows application data paths."""
    return {
        'local': os.environ.get('LOCALAPPDATA', ''),
        'roaming': os.environ.get('APPDATA', ''),
        'programdata': os.environ.get('ProgramData', ''),
        'program_files': os.environ.get('ProgramFiles', ''),
        'program_files_x86': os.environ.get('ProgramFiles(x86)', ''),
    }
