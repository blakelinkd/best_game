import json
import os
import re
import sqlite3
from typing import Dict, List, Optional, Set
from platform_client import PlatformClient, get_windows_appdata_paths


class GogClient(PlatformClient):
    """Client for GOG Galaxy platform using local database parsing."""

    @property
    def platform_name(self) -> str:
        return "gog"

    def __init__(self, db_path: Optional[str] = None):
        if not db_path:
            self.db_path = self._default_db_path()
        else:
            self.db_path = db_path
        print(f"GOG client using database: {self.db_path}")

    def _default_db_path(self) -> str:
        paths = get_windows_appdata_paths()
        programdata = paths.get('programdata', '') or os.environ.get('ProgramData', '')
        return os.path.join(programdata, 'GOG.com', 'Galaxy', 'storage', 'galaxy-2.0.db')

    def _connect_db(self) -> Optional[sqlite3.Connection]:
        if not os.path.exists(self.db_path):
            print(f"GOG database not found at: {self.db_path}")
            return None
        try:
            conn = sqlite3.connect(f"file:{self.db_path}?mode=ro", uri=True)
            conn.row_factory = sqlite3.Row
            return conn
        except Exception as e:
            print(f"Error connecting to GOG database: {e}")
            return None

    @staticmethod
    def _parse_gog_image(images_json: Optional[str]) -> Optional[str]:
        """Extract a usable image URL from a storeImages GamePiece JSON blob."""
        if not images_json:
            return None
        try:
            images = json.loads(images_json)
            # 'logo' is always a clean URL; 'horizontalCover' has {formatter} placeholder
            logo = images.get('logo')
            if logo:
                return logo
            cover = images.get('horizontalCover', '')
            if cover:
                # Remove _{formatter} suffix so the base image is served
                return re.sub(r'_\{formatter\}', '', cover)
        except (json.JSONDecodeError, AttributeError):
            pass
        return None

    def get_owned_games(self, force_refresh: bool = False) -> List[Dict]:
        conn = self._connect_db()
        if not conn:
            return []
        try:
            cursor = conn.cursor()
            # GamePieces type 9 = title, type 31 = storeImages
            # GameTimes has per-release playtime in minutes
            cursor.execute("""
                SELECT
                    lr.releaseKey,
                    ptr.gogId AS productId,
                    title_gp.value AS title_json,
                    images_gp.value AS images_json,
                    ip.installationPath,
                    COALESCE(gt.minutesInGame, 0) AS minutesInGame
                FROM LibraryReleases lr
                JOIN LicensedReleases lic ON lr.id = lic.libraryId
                JOIN ReleaseProperties rp ON lr.releaseKey = rp.releaseKey
                LEFT JOIN ProductsToReleaseKeys ptr ON lr.releaseKey = ptr.releaseKey
                LEFT JOIN GamePieces title_gp
                    ON lr.releaseKey = title_gp.releaseKey AND title_gp.gamePieceTypeId = 9
                LEFT JOIN GamePieces images_gp
                    ON lr.releaseKey = images_gp.releaseKey AND images_gp.gamePieceTypeId = 31
                LEFT JOIN InstalledBaseProducts ip ON ptr.gogId = ip.productId
                LEFT JOIN GameTimes gt ON lr.releaseKey = gt.releaseKey
                WHERE lic.isOwned = 1
                  AND rp.isDlc = 0
                  AND rp.isVisibleInLibrary = 1
                  AND lr.releaseKey LIKE 'gog_%'
                ORDER BY lr.releaseKey
            """)

            games = []
            for row in cursor.fetchall():
                release_key = row['releaseKey']
                product_id = row['productId']

                title = None
                if row['title_json']:
                    try:
                        title = json.loads(row['title_json']).get('title')
                    except (json.JSONDecodeError, AttributeError):
                        pass

                if product_id:
                    appid = str(product_id)
                else:
                    appid = release_key[4:] if release_key.startswith('gog_') else release_key

                game = {
                    'appid': appid,
                    'name': title or f"GOG_{appid}",
                    'platform': self.platform_name,
                    'installed': row['installationPath'] is not None,
                    'playtime_forever': int(row['minutesInGame'] or 0),
                    'release_key': release_key,
                }

                image_url = self._parse_gog_image(row['images_json'])
                if image_url:
                    game['header_image'] = image_url

                normalized = self._normalize_game(game, default_name_prefix="GOG_")
                if normalized:
                    # Preserve fields _normalize_game doesn't copy
                    normalized['release_key'] = release_key
                    if image_url:
                        normalized['header_image'] = image_url
                    games.append(normalized)

            return games

        except Exception as e:
            print(f"Error querying GOG database: {e}")
            return []
        finally:
            conn.close()

    def get_installed_appids(self) -> Set[str]:
        conn = self._connect_db()
        if not conn:
            return set()
        try:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT ptr.gogId
                FROM InstalledBaseProducts ip
                JOIN ProductsToReleaseKeys ptr ON ip.productId = ptr.gogId
                WHERE ip.installationPath IS NOT NULL
            """)
            return {str(row[0]) for row in cursor.fetchall()}
        except Exception as e:
            print(f"Error querying installed GOG games: {e}")
            return set()
        finally:
            conn.close()

    def get_store_metadata(self, appid: str) -> Dict:
        conn = self._connect_db()
        if not conn:
            return super().get_store_metadata(appid)
        try:
            cursor = conn.cursor()
            # storeTags is gamePieceTypeId 45; look up by productId via ProductsToReleaseKeys
            cursor.execute("""
                SELECT gp.value
                FROM ProductsToReleaseKeys ptr
                JOIN GamePieces gp ON ptr.releaseKey = gp.releaseKey
                WHERE ptr.gogId = ?
                  AND gp.gamePieceTypeId = 45
                LIMIT 1
            """, (appid,))
            row = cursor.fetchone()
            tags = []
            if row and row[0]:
                try:
                    tags_data = json.loads(row[0])
                    if isinstance(tags_data, dict):
                        tags = [t.get('name', '') for t in tags_data.get('tags', []) if t.get('name')]
                    elif isinstance(tags_data, list):
                        tags = [str(t) for t in tags_data if t]
                except (json.JSONDecodeError, AttributeError):
                    pass
            return {"genres": [], "categories": [], "tags": tags, "short_description": "", "description": ""}
        except Exception as e:
            print(f"Error getting GOG metadata for {appid}: {e}")
            return super().get_store_metadata(appid)
        finally:
            conn.close()

    def get_image_url(self, appid: str) -> Optional[str]:
        conn = self._connect_db()
        if not conn:
            return None
        try:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT gp.value
                FROM ProductsToReleaseKeys ptr
                JOIN GamePieces gp ON ptr.releaseKey = gp.releaseKey
                WHERE ptr.gogId = ?
                  AND gp.gamePieceTypeId = 31
                LIMIT 1
            """, (appid,))
            row = cursor.fetchone()
            return self._parse_gog_image(row[0] if row else None)
        except Exception as e:
            print(f"Error getting GOG image URL for {appid}: {e}")
            return None
        finally:
            conn.close()

    def test_connection(self) -> bool:
        if not os.path.exists(self.db_path):
            print(f"GOG database not found at: {self.db_path}")
            print("Make sure GOG Galaxy is installed and has run at least once.")
            return False
        conn = self._connect_db()
        if conn:
            conn.close()
            return True
        return False


if __name__ == "__main__":
    client = GogClient()
    if client.test_connection():
        print("GOG connection test: PASSED")
        games = client.get_owned_games()
        print(f"Found {len(games)} owned GOG games")
        for game in games[:5]:
            print(f"  - {game.get('name', 'Unknown')} (ID: {game.get('appid')})")
    else:
        print("GOG connection test: FAILED")
        print(f"Expected database at: {client.db_path}")
