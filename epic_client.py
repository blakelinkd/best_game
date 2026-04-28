import base64
import json
import os
from typing import Dict, List, Optional, Set
from platform_client import PlatformClient, get_windows_appdata_paths


class EpicClient(PlatformClient):
    """Client for Epic Games Store using local catcache and manifest parsing."""

    @property
    def platform_name(self) -> str:
        return "epic"

    def __init__(self, catalog_path: Optional[str] = None, installs_path: Optional[str] = None):
        """
        Args:
            catalog_path: Path to catcache.bin (or its containing directory).
            installs_path: Path to the Manifests directory containing *.item files.
        """
        paths = get_windows_appdata_paths()
        programdata = paths.get('programdata', '') or os.environ.get('ProgramData', '')

        raw_catalog = catalog_path or os.path.join(
            programdata, 'Epic', 'EpicGamesLauncher', 'Data', 'Catalog', 'catcache.bin'
        )
        # Accept either the file itself or its parent directory
        if os.path.isdir(raw_catalog):
            raw_catalog = os.path.join(raw_catalog, 'catcache.bin')
        self.catcache_path = raw_catalog

        self.manifests_path = installs_path or os.path.join(
            programdata, 'Epic', 'EpicGamesLauncher', 'Data', 'Manifests'
        )

        self._catalog_cache: Optional[List[Dict]] = None
        self._installed_cache: Optional[Dict[str, str]] = None  # appName -> displayName

    # ------------------------------------------------------------------ #
    # Internal helpers
    # ------------------------------------------------------------------ #

    def _load_catalog(self) -> List[Dict]:
        """Load and cache the catcache.bin catalog (base64-encoded JSON array)."""
        if self._catalog_cache is not None:
            return self._catalog_cache

        if not os.path.exists(self.catcache_path):
            print(f"Epic catcache not found at: {self.catcache_path}")
            self._catalog_cache = []
            return self._catalog_cache

        try:
            with open(self.catcache_path, 'rb') as f:
                raw = f.read()
            data = json.loads(base64.b64decode(raw))
            self._catalog_cache = data if isinstance(data, list) else []
        except Exception as e:
            print(f"Error loading Epic catcache: {e}")
            self._catalog_cache = []

        return self._catalog_cache

    def _load_installed(self) -> Dict[str, str]:
        """Return {appName: displayName} for all currently installed games."""
        if self._installed_cache is not None:
            return self._installed_cache

        result: Dict[str, str] = {}
        if not os.path.exists(self.manifests_path):
            self._installed_cache = result
            return result

        try:
            for fname in os.listdir(self.manifests_path):
                if not fname.endswith('.item'):
                    continue
                fpath = os.path.join(self.manifests_path, fname)
                try:
                    with open(fpath, 'r', encoding='utf-8') as f:
                        m = json.load(f)
                    if m.get('bIsApplication') and not m.get('bIsIncompleteInstall'):
                        app_name = m.get('AppName', '')
                        display_name = m.get('DisplayName', app_name)
                        if app_name:
                            result[app_name] = display_name
                except (json.JSONDecodeError, OSError):
                    continue
        except Exception as e:
            print(f"Error reading Epic manifests: {e}")

        self._installed_cache = result
        return result

    @staticmethod
    def _is_game(entry: Dict) -> bool:
        """Return True if the catalog entry is a game (not a plugin, asset, or tool)."""
        cats = {c.get('path', '') for c in entry.get('categories', [])}
        return 'games' in cats

    @staticmethod
    def _get_release_app_name(entry: Dict) -> str:
        """Return the appName used to match against installed manifests."""
        release_info = entry.get('releaseInfo', [])
        if release_info and isinstance(release_info, list):
            return release_info[0].get('appId', '')
        return ''

    @staticmethod
    def _best_image(entry: Dict) -> Optional[str]:
        """Return the best available image URL for a catalog entry."""
        images = entry.get('keyImages', [])
        preferred = ('DieselGameBoxTall', 'DieselGameBox', 'Thumbnail', 'OfferImageTall', 'OfferImageWide')
        by_type = {img.get('Type', ''): img.get('url', '') for img in images if img.get('url')}
        for t in preferred:
            if by_type.get(t):
                return by_type[t]
        # Fall back to any image
        for img in images:
            url = img.get('url', '')
            if url:
                return url
        return None

    # ------------------------------------------------------------------ #
    # PlatformClient interface
    # ------------------------------------------------------------------ #

    def get_owned_games(self, force_refresh: bool = False) -> List[Dict]:
        if force_refresh:
            self._catalog_cache = None
            self._installed_cache = None

        catalog = self._load_catalog()
        installed = self._load_installed()

        games = []
        for entry in catalog:
            if not self._is_game(entry):
                continue

            title = entry.get('title', '').strip()
            if not title:
                continue

            entitlement_name = entry.get('entitlementName', '')
            if not entitlement_name:
                continue

            release_app_name = self._get_release_app_name(entry)
            is_installed = release_app_name in installed

            game = {
                'appid': entitlement_name,
                'name': title,
                'platform': self.platform_name,
                'installed': is_installed,
                'namespace': entry.get('namespace', ''),
                'release_app_name': release_app_name,
            }

            image = self._best_image(entry)
            if image:
                game['header_image'] = image

            cats = entry.get('categories', [])
            cat_paths = [c.get('path', '') for c in cats if c.get('path')]
            if cat_paths:
                game['categories'] = cat_paths

            normalized = self._normalize_game(game, default_name_prefix="Epic_")
            if normalized:
                if image:
                    normalized['header_image'] = image
                games.append(normalized)

        return games

    def get_installed_appids(self) -> Set[str]:
        """Return entitlementNames of installed games by cross-referencing catalog."""
        installed = self._load_installed()
        if not installed:
            return set()

        catalog = self._load_catalog()
        installed_ids: Set[str] = set()
        for entry in catalog:
            if not self._is_game(entry):
                continue
            release_app_name = self._get_release_app_name(entry)
            if release_app_name in installed:
                entitlement_name = entry.get('entitlementName', '')
                if entitlement_name:
                    installed_ids.add(entitlement_name)
        return installed_ids

    def get_store_metadata(self, appid: str) -> Dict:
        catalog = self._load_catalog()
        entry = next((e for e in catalog if e.get('entitlementName') == appid), None)
        if not entry:
            return super().get_store_metadata(appid)

        cats = entry.get('categories', [])
        cat_names = [c.get('path', '') for c in cats if c.get('path')]
        description = entry.get('description', '') or entry.get('longDescription', '')
        if description and len(description) > 500:
            description = description[:497] + "..."
        return {
            "genres": [],
            "categories": cat_names,
            "tags": [],
            "short_description": description.strip(),
        }

    def get_image_url(self, appid: str) -> Optional[str]:
        catalog = self._load_catalog()
        entry = next((e for e in catalog if e.get('entitlementName') == appid), None)
        if not entry:
            return None
        return self._best_image(entry)

    def test_connection(self) -> bool:
        if not os.path.exists(self.catcache_path):
            print(f"Epic catcache not found at: {self.catcache_path}")
            print("Make sure Epic Games Launcher is installed and has run at least once.")
            return False
        catalog = self._load_catalog()
        return len(catalog) > 0


if __name__ == "__main__":
    client = EpicClient()
    if client.test_connection():
        print("Epic connection test: PASSED")
        games = client.get_owned_games()
        print(f"Found {len(games)} owned Epic games")
        for game in games[:5]:
            print(f"  - {game.get('name', 'Unknown')} (ID: {game.get('appid')})")
    else:
        print("Epic connection test: FAILED")
        print(f"Expected catcache at: {client.catcache_path}")
