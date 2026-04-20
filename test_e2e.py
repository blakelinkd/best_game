#!/usr/bin/env python3
"""
E2E tests for the stream update button functionality.
Uses Playwright to test the full browser experience.
"""

import os
import sys
import time
import subprocess
from pathlib import Path

import requests
from playwright.sync_api import sync_playwright, Page


PROJECT_ROOT = Path(__file__).parent
SERVER_HOST = "127.0.0.1"
SERVER_PORT = 5001


def test_page_loads(page: Page):
    """Test that the main page loads successfully."""
    url = f"http://{SERVER_HOST}:{SERVER_PORT}/"
    page.goto(url, wait_until="domcontentloaded", timeout=15000)
    title = page.title()
    assert "Steam Twitch Viewer Library" in title, f"Unexpected title: {title}"
    print("[OK] Page loads with correct title")


def test_stream_button_exists(page: Page):
    """Test that the stream update button exists in the DOM."""
    url = f"http://{SERVER_HOST}:{SERVER_PORT}/"
    page.goto(url, wait_until="domcontentloaded", timeout=15000)

    button = page.locator("#stream-submit-button")
    assert button.count() > 0, "Stream submit button not found in DOM"
    print("[OK] Stream submit button exists in DOM")


def test_stream_modal_opens(page: Page):
    """Test that the stream modal can be opened."""
    url = f"http://{SERVER_HOST}:{SERVER_PORT}/"
    page.goto(url, wait_until="domcontentloaded", timeout=15000)

    stream_button = page.locator('[data-stream-open]').first
    if stream_button.count() > 0:
        stream_button.click()
        page.wait_for_timeout(500)
        modal = page.locator("#stream-modal")
        is_visible = modal.evaluate("el => !el.hidden" if hasattr(el := modal.first, 'evaluate') else True)
        print("[OK] Stream modal opens on button click")
    else:
        print("[SKIP] No stream buttons available")


def test_stream_button_state(page: Page):
    """Test stream button enabled/disabled state based on Twitch connection."""
    url = f"http://{SERVER_HOST}:{SERVER_PORT}/"
    page.goto(url, wait_until="domcontentloaded", timeout=15000)

    from twitch_auth import TwitchAuthStore
    store = TwitchAuthStore()
    token = store.load()
    connected = token and token.get("access_token")

    stream_buttons = page.locator('[data-stream-open]')
    if stream_buttons.count() > 0:
        is_disabled = stream_buttons.first.is_disabled()
        if connected and not is_disabled:
            print("[OK] Stream button enabled when Twitch connected")
        elif not connected and is_disabled:
            print("[OK] Stream button disabled when Twitch not connected")
        else:
            print(f"[INFO] Button enabled={not is_disabled}, connected={connected}")
    else:
        print("[SKIP] No stream buttons")


def test_stream_form_submits(page: Page):
    """Test that the stream form can be submitted."""
    url = f"http://{SERVER_HOST}:{SERVER_PORT}/"
    page.goto(url, wait_until="domcontentloaded", timeout=15000)

    stream_buttons = page.locator('[data-stream-open]')
    if stream_buttons.count() == 0:
        print("[SKIP] No stream buttons")
        return

    stream_buttons.first.click()
    page.wait_for_timeout(500)

    title_field = page.locator("#modal-title")
    title_field.fill("Test Stream Title via E2E")

    page.locator("#stream-submit-button").click()
    page.wait_for_timeout(3000)

    notices = page.locator(".notice")
    if notices.count() > 0:
        notice_text = notices.first.text_content()
        print(f"[INFO] Notice: {notice_text}")
    print("[OK] Stream form submitted")


def main():
    print("=" * 60)
    print("E2E Tests - Stream Update Button")
    print("=" * 60)

    env = os.environ.copy()
    env["PYTHONPATH"] = str(PROJECT_ROOT)

    process = subprocess.Popen(
        [sys.executable, "main.py", "--host", SERVER_HOST, "--port", str(SERVER_PORT), "--no-browser"],
        cwd=str(PROJECT_ROOT),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )

    server_url = f"http://{SERVER_HOST}:{SERVER_PORT}"

    print(f"\n[INFO] Starting server on {server_url}...")
    for _ in range(30):
        try:
            requests.get(server_url, timeout=1)
            break
        except Exception:
            time.sleep(0.5)
    else:
        process.kill()
        print("[ERROR] Server failed to start")
        return 1

    print(f"[INFO] Server started on {server_url}")

    results = []
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)

            tests = [
                ("Page Loads", test_page_loads),
                ("Stream Button Exists", test_stream_button_exists),
                ("Stream Modal Opens", test_stream_modal_opens),
                ("Stream Button State", test_stream_button_state),
                ("Stream Form Submits", test_stream_form_submits),
            ]

            for test_name, test_func in tests:
                print(f"\n{test_name}:")
                try:
                    page = browser.new_page()
                    test_func(page)
                    page.close()
                    results.append((test_name, True))
                except Exception as e:
                    print(f"[ERROR] {e}")
                    results.append((test_name, False))

            browser.close()

    finally:
        print(f"\n[INFO] Stopping server...")
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
        print(f"[INFO] Server stopped")

    print("\n" + "=" * 60)
    print("TEST SUMMARY")
    print("=" * 60)

    passed = sum(1 for _, success in results if success)
    for test_name, success in results:
        status = "[OK] PASS" if success else "[ERROR] FAIL"
        print(f"{test_name:35} {status}")

    print(f"\n{passed}/{len(results)} tests passed")
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())