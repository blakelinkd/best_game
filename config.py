import os
from dotenv import load_dotenv

load_dotenv()

class Config:
    # Steam API Configuration
    STEAM_API_KEY = os.getenv('STEAM_API_KEY')
    STEAM_USER_ID = os.getenv('STEAM_USER_ID')
    
    # Twitch API Configuration
    TWITCH_CLIENT_ID = os.getenv('TWITCH_CLIENT_ID')
    TWITCH_CLIENT_SECRET = os.getenv('TWITCH_CLIENT_SECRET')
    TWITCH_BROADCASTER_ID = os.getenv('TWITCH_BROADCASTER_ID')
    TWITCH_USER_ACCESS_TOKEN = os.getenv('TWITCH_USER_ACCESS_TOKEN')
    TWITCH_AUTH_SCOPE = os.getenv('TWITCH_AUTH_SCOPE', 'channel:manage:broadcast')
    
    # Steam Installation Path
    STEAM_INSTALL_PATH = os.getenv('STEAM_INSTALL_PATH', r'C:\Program Files (x86)\Steam')

    # The local Steam config app list is local app state, not an owned-games source.
    # Leave disabled so the dashboard is based on the Steam owned-games API.
    ALLOW_LOCAL_APP_FALLBACK = os.getenv('ALLOW_LOCAL_APP_FALLBACK', '').lower() in ('1', 'true', 'yes', 'on')
    
    # Steam ID conversion constants
    STEAMID64_BASE = 76561197960265728
    
    @property
    def STEAMID64(self):
        """Get SteamID64 from STEAM_USER_ID (handles both account_id and SteamID64)"""
        if not self.STEAM_USER_ID:
            return None
        try:
            user_id = int(self.STEAM_USER_ID)
            # If it's a large number (likely SteamID64), return as is
            if user_id > self.STEAMID64_BASE:
                return str(user_id)
            # Otherwise it's an account_id, convert to SteamID64
            return str(user_id + self.STEAMID64_BASE)
        except (ValueError, TypeError):
            # If not a number, return as is (might be a string ID)
            return self.STEAM_USER_ID
    
    @property
    def ACCOUNT_ID(self):
        """Get account_id (userdata folder name) from STEAM_USER_ID"""
        if not self.STEAM_USER_ID:
            return None
        try:
            user_id = int(self.STEAM_USER_ID)
            # If it's a large number (likely SteamID64), convert to account_id
            if user_id > self.STEAMID64_BASE:
                return str(user_id - self.STEAMID64_BASE)
            # Otherwise it's already an account_id
            return str(user_id)
        except (ValueError, TypeError):
            # If not a number, try to use as is
            return self.STEAM_USER_ID
    
    # File Paths
    @property
    def SHARED_CONFIG_PATH(self):
        if self.ACCOUNT_ID:
            return os.path.join(
                self.STEAM_INSTALL_PATH,
                'userdata',
                self.ACCOUNT_ID,
                '7',
                'remote',
                'sharedconfig.vdf'
            )
        return None
    
    @property
    def LOCAL_CONFIG_PATH(self):
        if self.ACCOUNT_ID:
            return os.path.join(
                self.STEAM_INSTALL_PATH,
                'userdata',
                self.ACCOUNT_ID,
                'config',
                'localconfig.vdf'
            )
        return None
    
    # API Endpoints
    TWITCH_TOKEN_URL = "https://id.twitch.tv/oauth2/token"
    TWITCH_AUTHORIZE_URL = "https://id.twitch.tv/oauth2/authorize"
    TWITCH_VALIDATE_URL = "https://id.twitch.tv/oauth2/validate"
    TWITCH_USERS_URL = "https://api.twitch.tv/helix/users"
    TWITCH_STREAMS_URL = "https://api.twitch.tv/helix/streams"
    TWITCH_GAMES_URL = "https://api.twitch.tv/helix/games"
    TWITCH_CHANNELS_URL = "https://api.twitch.tv/helix/channels"
    TWITCH_SEARCH_CATEGORIES_URL = "https://api.twitch.tv/helix/search/categories"
    
    # Rate Limiting
    STEAM_RATE_LIMIT = 300  # requests per 5 minutes
    STEAM_STORE_RATE_LIMIT = 60  # requests per minute for store page fallback
    TWITCH_RATE_LIMIT = 800  # requests per minute (app access token)
    TWITCH_AUTH_RATE_LIMIT = 20  # token requests per minute
    RATE_LIMIT_RETRY_ATTEMPTS = 3
    
    # Update Interval (seconds)
    UPDATE_INTERVAL = 3600  # 1 hour

    # Cache settings
    OWNED_GAMES_CACHE_TTL = int(os.getenv('OWNED_GAMES_CACHE_TTL', '86400'))  # 24 hours
    VIEWER_COUNT_CACHE_TTL = int(os.getenv('VIEWER_COUNT_CACHE_TTL', '600'))  # 10 minutes
    CACHE_DIR = os.getenv('CACHE_DIR', 'cache')
    ASSET_CACHE_DIR = os.getenv('ASSET_CACHE_DIR', os.path.join('static', 'cache'))
    
    # Game name matching settings
    GAME_NAME_MATCH_THRESHOLD = 0.8  # Fuzzy matching threshold (0-1)
    
    def validate(self):
        """Validate required configuration"""
        errors = []
        
        if not self.STEAM_USER_ID:
            errors.append("STEAM_USER_ID is required. Use either:")
            errors.append("  - Account ID (folder name from Steam/userdata/, e.g., 96799937)")
            errors.append("  - SteamID64 (64-bit Steam ID, e.g., 76561198057065665)")

        if not self.STEAM_API_KEY:
            errors.append("STEAM_API_KEY is required to fetch your owned games from Steam Web API.")
            errors.append("  Get one at: https://steamcommunity.com/dev/apikey")
        
        if not self.TWITCH_CLIENT_ID:
            errors.append("TWITCH_CLIENT_ID is required. Set as Windows environment variable or in .env file.")
            errors.append("  Get credentials from: https://dev.twitch.tv/console")
            errors.append("  Set in Windows: Win+X → System → Advanced → Environment Variables")
        
        if not self.TWITCH_CLIENT_SECRET:
            errors.append("TWITCH_CLIENT_SECRET is required. Set as Windows environment variable or in .env file.")
            errors.append("  Get credentials from: https://dev.twitch.tv/console")
            errors.append("  Set in Windows: Win+X → System → Advanced → Environment Variables")
        
        if errors:
            raise ValueError("\n".join(errors))
        
        return True

config = Config()
