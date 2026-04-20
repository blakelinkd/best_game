#!/usr/bin/env python3
"""
Smoke tests for the Steam Twitch Viewer Dashboard.
"""

import os
import time
import shutil
import sys
from requests import Response


def test_structure():
    print("Testing project structure...")
    required_files = [
        "requirements.txt",
        ".env.example",
        "config.py",
        "steam_client.py",
        "twitch_client.py",
        "viewer_service.py",
        "main.py",
        "setup.py",
        "README.md",
        "run.bat",
        os.path.join("templates", "index.html"),
        os.path.join("static", "styles.css"),
    ]

    missing = [path for path in required_files if not os.path.exists(path)]
    if missing:
        print(f"[ERROR] Missing files: {missing}")
        return False

    print("[OK] All required files exist")
    return True


def test_imports():
    print("\nTesting module imports...")
    modules = [
        ("config", "config"),
        ("steam_client", "SteamClient"),
        ("twitch_client", "TwitchClient"),
        ("viewer_service", "ViewerService"),
        ("main", "app"),
    ]

    for module_name, symbol in modules:
        try:
            module = __import__(module_name, fromlist=[symbol])
            getattr(module, symbol)
            print(f"[OK] {module_name}.{symbol}")
        except Exception as e:
            print(f"[ERROR] Failed to import {module_name}.{symbol}: {e}")
            return False

    return True


def test_twitch_safe_tags():
    print("\nTesting Twitch tag normalization...")
    try:
        from viewer_service import twitch_safe_tags

        tags = twitch_safe_tags([
            "Atomic Heart",
            "First Playthrough",
            "Multi-player",
            "FPS",
            "Ranked",
            "Co Op",
            "New Game Plus",
            "Boss Rush",
            "Challenge Run",
            "Survival Horror",
            "Extra Tag",
            "atomic heart",
        ])
        expected = [
            "atomicheart",
            "firstplaythrough",
            "multiplayer",
            "fps",
            "ranked",
            "coop",
            "newgameplus",
            "bossrush",
            "challengerun",
            "survivalhorror",
        ]
        if tags != expected:
            print(f"[ERROR] Unexpected normalized tags: {tags}")
            return False

        print("[OK] Twitch tags are lowercased, de-spaced, deduplicated, and limited")
        return True
    except Exception as e:
        print(f"[ERROR] Twitch tag normalization test failed: {e}")
        return False


def test_flask_route():
    print("\nTesting Flask route with mocked service...")
    try:
        import main

        original_service = main.ViewerService
        original_validate = main.config.validate
        original_twitch_client = main.TwitchClient

        class MockViewerService:
            def can_render_from_cache(self, limit=None, include_zero=False):
                return False

            def _result(self, limit=None):
                return {
                    "games": [
                        {
                            "appid": 730,
                            "name": "Counter-Strike 2",
                            "playtime_hours": 12.5,
                            "viewer_count": 1000,
                            "twitch_game_id": "32399",
                            "twitch_name": "Counter-Strike",
                            "steam_tags": ["action"],
                            "installed": True,
                            "last_played": 1700000000,
                            "steam_header_url": "https://example.com/header.jpg",
                            "steam_url": "https://store.steampowered.com/app/730/",
                            "twitch_url": "https://www.twitch.tv/directory/category/123",
                        }
                    ],
                    "total_owned": 1,
                    "processed": 1,
                    "matched": 1,
                    "shown": 1,
                    "limited": limit is not None,
                    "limit": limit,
                    "owned_cache_ttl": 86400,
                    "viewer_cache_ttl": 600,
                }

            def get_games_with_viewers(self, limit=None, include_zero=False, force_refresh=False):
                return self._result(limit)

            def process_games_incremental(self, include_zero=False, force_refresh=False, on_game=None):
                result = self._result(None)
                if on_game:
                    for game in result["games"]:
                        on_game(game, result)
                return result

        class MockTwitchClient:
            def __init__(self, *args, **kwargs):
                pass

            def get_channel_info(self, broadcaster_id=None):
                return {}

        main.ViewerService = MockViewerService
        main.config.validate = lambda: True
        main.TwitchClient = MockTwitchClient
        client = main.app.test_client()
        response = client.get("/")

        if response.status_code != 200:
            print(f"[ERROR] Expected HTTP 200, got {response.status_code}")
            return False
        page_body = response.get_data(as_text=True)
        if "Twitch Viewer Dashboard" not in page_body:
            print("[ERROR] Dashboard shell missing expected content")
            return False

        job_id = next(reversed(main._background_jobs))
        for _ in range(20):
            if main._background_jobs[job_id].get("done"):
                break
            time.sleep(0.05)

        response = client.get(f"/api/progress?job_id={job_id}")
        body = response.get_data(as_text=True)

        main.ViewerService = original_service
        main.config.validate = original_validate
        main.TwitchClient = original_twitch_client

        if "Counter-Strike 2" not in body:
            print("[ERROR] Dashboard content missing expected game data")
            return False

        print("[OK] Flask route rendered expected content")
        return True
    except Exception as e:
        print(f"[ERROR] Flask route test failed: {e}")
        return False


def test_stream_update_route():
    print("\nTesting stream update route...")
    try:
        import main

        original_validate = main.config.validate
        original_broadcaster_id = main.config.TWITCH_BROADCASTER_ID
        original_user_token = main.config.TWITCH_USER_ACCESS_TOKEN
        original_client_id = main.config.TWITCH_CLIENT_ID
        original_twitch_client = main.TwitchClient
        original_load_token = main._load_valid_user_token
        captured = {}

        class MockTwitchClient:
            def __init__(self, client_id=None, client_secret=None, ensure_app_token=True):
                captured["client_id"] = client_id
                captured["client_secret"] = client_secret
                captured["ensure_app_token"] = ensure_app_token

            def resolve_broadcaster_id(self, broadcaster=None):
                captured["broadcaster_input"] = broadcaster
                return "123"

            def update_channel_info(self, broadcaster_id, access_token, updates):
                captured["broadcaster_id"] = broadcaster_id
                captured["access_token"] = access_token
                captured["updates"] = updates
                return True

        main.config.validate = lambda: True
        main.config.TWITCH_BROADCASTER_ID = "123"
        main.config.TWITCH_USER_ACCESS_TOKEN = "user-token"
        main.config.TWITCH_CLIENT_ID = "client-id"
        main.TwitchClient = MockTwitchClient
        main._load_valid_user_token = lambda: {"access_token": "user-token", "user_id": "123"}

        client = main.app.test_client()
        response = client.post(
            "/stream/update",
            data={
                "game_id": "32399",
                "title": "Testing Counter-Strike",
                "broadcaster_language": "en",
                "tags": "FPS, Ranked, Multi-player, First Playthrough, Atomic Heart, Co Op, New Game Plus, Boss Rush, Challenge Run, Survival Horror, Extra Tag",
                "is_branded_content": "1",
                "limit": "1",
            },
            headers={"Accept": "application/json", "X-Requested-With": "fetch"},
        )

        if response.status_code != 200:
            print(f"[ERROR] Expected HTTP 200, got {response.status_code}")
            return False
        if not response.is_json or not response.get_json().get("success"):
            print(f"[ERROR] Expected successful JSON response, got {response.get_data(as_text=True)}")
            return False

        expected_updates = {
            "game_id": "32399",
            "title": "Testing Counter-Strike",
            "broadcaster_language": "en",
            "tags": ["fps", "ranked", "multiplayer", "firstplaythrough", "atomicheart", "coop", "newgameplus", "bossrush", "challengerun", "survivalhorror"],
            "is_branded_content": True,
        }
        if captured.get("updates") != expected_updates:
            print(f"[ERROR] Unexpected update payload: {captured.get('updates')}")
            return False
        if captured.get("client_secret") != "":
            print("[ERROR] Stream update route should not request an app access token")
            return False
        if captured.get("ensure_app_token") is not False:
            print("[ERROR] Stream update route should skip app token initialization")
            return False

        fallback_response = client.post(
            "/stream/update",
            data={
                "game_id": "32399",
                "title": "Testing Counter-Strike",
                "broadcaster_language": "en",
                "limit": "1",
            },
            follow_redirects=False,
        )
        if fallback_response.status_code != 302:
            print(f"[ERROR] Expected fallback redirect, got {fallback_response.status_code}")
            return False

        main.config.validate = original_validate
        main.config.TWITCH_BROADCASTER_ID = original_broadcaster_id
        main.config.TWITCH_USER_ACCESS_TOKEN = original_user_token
        main.config.TWITCH_CLIENT_ID = original_client_id
        main.TwitchClient = original_twitch_client
        main._load_valid_user_token = original_load_token

        print("[OK] Stream update route built expected Twitch payload")
        return True
    except Exception as e:
        print(f"[ERROR] Stream update route test failed: {e}")
        return False


def test_twitch_login_route():
    print("\nTesting Twitch login route...")
    try:
        import main

        original_validate = main.config.validate
        original_client = main.TwitchClient
        captured = {}

        class MockTwitchClient:
            def get_authorization_url(self, redirect_uri, state):
                captured["redirect_uri"] = redirect_uri
                captured["state"] = state
                return f"https://id.twitch.tv/oauth2/authorize?state={state}"

        main.config.validate = lambda: True
        main.TwitchClient = MockTwitchClient

        client = main.app.test_client()
        response = client.get("/auth/twitch", follow_redirects=False)

        main.config.validate = original_validate
        main.TwitchClient = original_client

        if response.status_code != 302:
            print(f"[ERROR] Expected redirect, got {response.status_code}")
            return False
        if not captured.get("redirect_uri", "").endswith("/auth/twitch/callback"):
            print("[ERROR] OAuth redirect URI was not built for the callback route")
            return False
        if not captured.get("state"):
            print("[ERROR] OAuth state was not generated")
            return False

        print("[OK] Twitch login route redirects to OAuth")
        return True
    except Exception as e:
        print(f"[ERROR] Twitch login route test failed: {e}")
        return False


def test_viewer_cache():
    print("\nTesting viewer and asset cache...")
    try:
        from viewer_service import ViewerService

        class FakeSteamClient:
            def __init__(self):
                self.owned_calls = 0
                self.asset_calls = 0
                self.metadata_calls = 0

            def get_owned_games(self):
                self.owned_calls += 1
                return [{"appid": 10, "name": "Counter-Strike", "playtime_forever": 120}]

            def get_installed_appids(self):
                return {10}

            def get_store_metadata(self, appid):
                self.metadata_calls += 1
                return {
                    "genres": ["Action"],
                    "categories": ["Multi-player"],
                    "tags": ["Action", "Multi-player"],
                }

            def _steam_store_get(self, url, **kwargs):
                self.asset_calls += 1
                response = Response()
                response.status_code = 200
                response._content = b"fake-image"
                response.headers["Content-Type"] = "image/jpeg"
                return response

        class FakeTwitchClient:
            def __init__(self):
                self.search_calls = 0
                self.viewer_calls = 0
                self.batch_viewer_calls = 0

            def search_games(self, name):
                self.search_calls += 1
                return [
                    {
                        "id": "32399",
                        "name": "Counter-Strike",
                        "box_art_url": "https://example.com/box-{width}x{height}.jpg",
                    }
                ]

            def get_game_viewer_count(self, game_id):
                self.viewer_calls += 1
                return 42

            def get_game_viewer_counts(self, game_ids):
                self.batch_viewer_calls += 1
                return {str(game_id): 42 for game_id in game_ids}

        temp_dir = os.path.join(os.getcwd(), "test_cache_tmp")
        if os.path.exists(temp_dir):
            shutil.rmtree(temp_dir)
        os.makedirs(temp_dir)
        try:
            steam = FakeSteamClient()
            twitch = FakeTwitchClient()
            service = ViewerService(
                steam_client=steam,
                twitch_client=twitch,
                cache_file=os.path.join(temp_dir, "viewer_cache.json"),
                legacy_cache_file=os.path.join(temp_dir, "missing_legacy_cache.json"),
                asset_cache_dir=os.path.join("static", "cache"),
            )
            service.project_root = temp_dir

            first = service.get_games_with_viewers(limit=1)
            second = service.get_games_with_viewers(limit=1)

            if first["shown"] != 1 or second["shown"] != 1:
                print("[ERROR] Expected cached runs to render one game")
                return False
            if not service.can_render_from_cache(limit=1):
                print("[ERROR] Expected warm cache to be renderable without a background job")
                return False
            if first["games"][0].get("steam_tags") != ["action", "multiplayer"]:
                print(f"[ERROR] Expected Steam tags on rendered game, got {first['games'][0].get('steam_tags')}")
                return False
            if steam.owned_calls != 1:
                print(f"[ERROR] Owned games were fetched {steam.owned_calls} times")
                return False
            if steam.metadata_calls != 1:
                print(f"[ERROR] Steam metadata was fetched {steam.metadata_calls} times")
                return False
            if twitch.search_calls != 1 or twitch.batch_viewer_calls != 1 or twitch.viewer_calls != 0:
                print("[ERROR] Twitch data was not reused from cache")
                return False
            if steam.asset_calls != 2:
                print(f"[ERROR] Expected two initial asset downloads, got {steam.asset_calls}")
                return False

        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)

        print("[OK] Viewer data and image cache reused cached values")
        return True
    except Exception as e:
        print(f"[ERROR] Viewer cache test failed: {e}")
        return False


def main():
    print("=" * 60)
    print("Steam Twitch Viewer Dashboard - Smoke Tests")
    print("=" * 60)

    tests = [
        ("Project Structure", test_structure),
        ("Module Imports", test_imports),
        ("Twitch Tags", test_twitch_safe_tags),
        ("Flask Route", test_flask_route),
        ("Twitch Login", test_twitch_login_route),
        ("Stream Update", test_stream_update_route),
        ("Viewer Cache", test_viewer_cache),
    ]

    results = []
    for test_name, test_func in tests:
        print(f"\n{test_name}:")
        try:
            results.append((test_name, test_func()))
        except Exception as e:
            print(f"[ERROR] Test crashed: {e}")
            results.append((test_name, False))

    print("\n" + "=" * 60)
    print("TEST SUMMARY")
    print("=" * 60)

    passed = sum(1 for _, success in results if success)
    for test_name, success in results:
        print(f"{test_name:20} {'[OK] PASS' if success else '[ERROR] FAIL'}")

    print(f"\n{passed}/{len(results)} tests passed")
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
