#!/usr/bin/env python3
"""
Install project dependencies.
Run from within the project's virtual environment.
"""

import subprocess
import sys
import os


def run(cmd, **kwargs):
    print(f"  -> {' '.join(cmd)}")
    return subprocess.check_call(cmd, **kwargs)


def main():
    project_root = os.path.dirname(os.path.abspath(__file__))
    req_path = os.path.join(project_root, "requirements.txt")

    if not os.path.exists(req_path):
        print(f"[ERROR] requirements.txt not found at {req_path}")
        sys.exit(1)

    print("Upgrading pip...")
    run([sys.executable, "-m", "pip", "install", "--upgrade", "pip"])

    print("\nInstalling packages from requirements.txt...")
    run([sys.executable, "-m", "pip", "install", "-r", req_path])

    print("\nInstalling Playwright browser (chromium)...")
    try:
        run([sys.executable, "-m", "playwright", "install", "chromium"])
    except subprocess.CalledProcessError:
        print("[WARNING] Playwright browser install failed. Some features may not work.")

    print("\nAll dependencies installed successfully!")


if __name__ == "__main__":
    main()
