import os
import ast
import sys
from pathlib import Path

try:
    from dotenv import load_dotenv
except ModuleNotFoundError:
    def load_dotenv(dotenv_path=None, override=False):
        path = Path(dotenv_path or ".env")
        if not path.exists():
            return False
        for raw_line in path.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            value = value.strip()
            if not key or (not override and key in os.environ):
                continue
            if value and value[0] in ("'", '"') and value[-1:] == value[0]:
                try:
                    value = ast.literal_eval(value)
                except (SyntaxError, ValueError):
                    value = value[1:-1]
            os.environ[key] = value
        return True

DOTENV_PATH = Path(__file__).resolve().parent / ".env"


def default_steam_install_path() -> str:
    if sys.platform.startswith("win"):
        return r"C:\Program Files (x86)\Steam"
    if sys.platform == "darwin":
        return os.path.expanduser("~/Library/Application Support/Steam")
    return os.path.expanduser("~/.steam/steam")


def _truthy(value: str) -> bool:
    return str(value or "").lower() in ("1", "true", "yes", "on")


def _int_env(name: str, default: str) -> int:
    try:
        return int(os.getenv(name, default))
    except (TypeError, ValueError):
        return int(default)


class Config:
    # Steam ID conversion constants
    STEAMID64_BASE = 76561197960265728

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

    # Game name matching settings
    GAME_NAME_MATCH_THRESHOLD = 0.8  # Fuzzy matching threshold (0-1)

    def __init__(self):
        self.reload()

    def reload(self):
        """Reload environment-backed settings from .env and process env."""
        load_dotenv(DOTENV_PATH, override=True)

        # Steam API Configuration
        self.STEAM_API_KEY = os.getenv("STEAM_API_KEY")
        self.STEAM_USER_ID = os.getenv("STEAM_USER_ID")

        # Twitch API Configuration
        self.TWITCH_CLIENT_ID = os.getenv("TWITCH_CLIENT_ID")
        self.TWITCH_CLIENT_SECRET = os.getenv("TWITCH_CLIENT_SECRET")
        self.TWITCH_BROADCASTER_ID = os.getenv("TWITCH_BROADCASTER_ID")
        self.TWITCH_USER_ACCESS_TOKEN = os.getenv("TWITCH_USER_ACCESS_TOKEN")
        self.TWITCH_AUTH_SCOPE = os.getenv("TWITCH_AUTH_SCOPE", "channel:manage:broadcast")
        self.TWITCH_REDIRECT_URI = os.getenv("TWITCH_REDIRECT_URI", "").strip()

        # Steam Installation Path
        self.STEAM_INSTALL_PATH = os.getenv("STEAM_INSTALL_PATH") or default_steam_install_path()

        # The local Steam config app list is local app state, not an owned-games source.
        # Leave disabled so the dashboard is based on the Steam owned-games API.
        self.ALLOW_LOCAL_APP_FALLBACK = _truthy(os.getenv("ALLOW_LOCAL_APP_FALLBACK", ""))

        # Multi-platform configuration
        self.ENABLED_PLATFORMS = [
            platform.strip()
            for platform in os.getenv("ENABLED_PLATFORMS", "steam,gog,epic").lower().split(",")
            if platform.strip()
        ]

        # Local LLM configuration for AI tag/title generation. Ollama defaults to port 11434.
        self.OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://127.0.0.1:11434").rstrip("/")
        self.OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "").strip()
        self.OLLAMA_TIMEOUT = _int_env("OLLAMA_TIMEOUT", "60")

        # GOG Galaxy configuration
        self.GOG_DB_PATH = os.getenv("GOG_DB_PATH", "")  # Optional override

        # Epic Games configuration
        self.EPIC_CATALOG_PATH = os.getenv("EPIC_CATALOG_PATH", "")  # Optional override
        self.EPIC_INSTALLS_PATH = os.getenv("EPIC_INSTALLS_PATH", "")  # Optional override

        # Cache settings
        self.OWNED_GAMES_CACHE_TTL = _int_env("OWNED_GAMES_CACHE_TTL", "86400")  # 24 hours
        self.VIEWER_COUNT_CACHE_TTL = _int_env("VIEWER_COUNT_CACHE_TTL", "600")  # 10 minutes
        self.SCAN_BACKOFF_MAX_AGE = _int_env("SCAN_BACKOFF_MAX_AGE", "604800")  # 7 days
        self.SCAN_BACKOFF_MAX_SKIP_RUNS = _int_env("SCAN_BACKOFF_MAX_SKIP_RUNS", "64")
        self.CACHE_DIR = os.getenv("CACHE_DIR", "cache")
        self.ASSET_CACHE_DIR = os.getenv("ASSET_CACHE_DIR", os.path.join("static", "cache"))

        # History / analytics
        self.FIRST_RUN = _truthy(os.getenv("FIRST_RUN", "true"))

        self.COLLECTION_INTERVAL = _int_env("COLLECTION_INTERVAL", "900")  # seconds
        self.HISTORY_RETENTION_DAYS = _int_env("HISTORY_RETENTION_DAYS", "90")  # 0 = keep forever
        self.HISTORY_DB_PATH = os.getenv(
            "HISTORY_DB_PATH",
            os.path.join(self.CACHE_DIR, "viewer_history.db"),
        )
        return self

    @property
    def GOG_ENABLED(self) -> bool:
        return "gog" in self.ENABLED_PLATFORMS

    @property
    def EPIC_ENABLED(self) -> bool:
        return "epic" in self.ENABLED_PLATFORMS

    @property
    def RETRO_SYSTEMS(self) -> list:
        """Comma-separated list of retro consoles to include, or 'all' for every supported system.
        Empty string disables retro entirely. Recognized values:
          nes, snes, n64, gamecube, gameboy, gbc, gba,
          genesis, saturn, dreamcast, ps1, ps2, 3do
        """
        raw = (os.getenv("RETRO_SYSTEMS", "") or "").strip().lower()
        if not raw:
            return []
        if raw == "all":
            return [
                "nes", "snes", "n64", "gamecube", "gameboy", "gbc", "gba",
                "genesis", "saturn", "dreamcast", "ps1", "ps2", "3do",
            ]
        return [system.strip() for system in raw.split(",") if system.strip()]

    @property
    def RETRO_ENABLED(self) -> bool:
        return bool(self.RETRO_SYSTEMS)

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
                "userdata",
                self.ACCOUNT_ID,
                "7",
                "remote",
                "sharedconfig.vdf",
            )
        return None

    @property
    def LOCAL_CONFIG_PATH(self):
        if self.ACCOUNT_ID:
            return os.path.join(
                self.STEAM_INSTALL_PATH,
                "userdata",
                self.ACCOUNT_ID,
                "config",
                "localconfig.vdf",
            )
        return None

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
            errors.append("TWITCH_CLIENT_ID is required. Set it in Settings or in the .env file.")
            errors.append("  Get credentials from: https://dev.twitch.tv/console")

        if not self.TWITCH_CLIENT_SECRET:
            errors.append("TWITCH_CLIENT_SECRET is required. Set it in Settings or in the .env file.")
            errors.append("  Get credentials from: https://dev.twitch.tv/console")

        if errors:
            raise ValueError("\n".join(errors))

        return True


config = Config()
