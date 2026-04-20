import json
import os
import time
from typing import Dict, Optional

from config import config


class TwitchAuthStore:
    """Local storage for Twitch OAuth user tokens."""

    def __init__(self, token_file: Optional[str] = None):
        self.project_root = os.path.dirname(os.path.abspath(__file__))
        self.token_file = token_file or os.path.join(config.CACHE_DIR, "twitch_user_token.json")

    def _absolute_path(self, path: str) -> str:
        if os.path.isabs(path):
            return path
        return os.path.join(self.project_root, path)

    def load(self) -> Optional[Dict]:
        path = self._absolute_path(self.token_file)
        if not os.path.exists(path):
            return None
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            print(f"Error loading Twitch user token: {e}")
            return None

    def save(self, token_data: Dict):
        path = self._absolute_path(self.token_file)
        os.makedirs(os.path.dirname(path), exist_ok=True)

        existing = self.load() or {}
        merged = {**existing, **token_data}
        if "expires_in" in merged:
            merged["expires_at"] = time.time() + int(merged["expires_in"])

        with open(path, "w", encoding="utf-8") as f:
            json.dump(merged, f, indent=2)

    def clear(self):
        path = self._absolute_path(self.token_file)
        if os.path.exists(path):
            os.remove(path)

    def is_connected(self) -> bool:
        token = self.load()
        return bool(token and token.get("access_token"))
