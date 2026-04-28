#!/usr/bin/env python3
"""Test multi-platform integration."""

import os
import sys

# Add current directory to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from platform_client import PlatformClient
from steam_client import SteamClient
from gog_client import GogClient
from epic_client import EpicClient
from viewer_service import ViewerService

def test_platform_client(client: PlatformClient):
    """Test a platform client."""
    print(f"\nTesting {client.platform_name} client...")
    
    # Test connection
    if client.test_connection():
        print(f"  Connection: OK")
        
        # Get owned games
        games = client.get_owned_games()
        print(f"  Owned games: {len(games)}")
        
        if games:
            # Show first 3 games
            for i, game in enumerate(games[:3]):
                print(f"    {i+1}. {game.get('name', 'Unknown')} (ID: {game.get('appid')})")
        
        # Get installed app IDs
        installed = client.get_installed_appids()
        print(f"  Installed: {len(installed)}")
        
    else:
        print(f"  Connection: FAILED")

def test_viewer_service():
    """Test ViewerService with multiple platforms."""
    print("\nTesting ViewerService...")
    
    # Create platform clients
    clients = [
        SteamClient(),
        GogClient(),
        EpicClient(),
    ]
    
    # Create viewer service with all clients
    service = ViewerService(platform_clients=clients)
    
    print(f"Platform clients: {[c.platform_name for c in service.platform_clients]}")
    
    # Test getting owned games
    games = service._get_owned_games()
    print(f"Total owned games: {len(games)}")
    
    # Group by platform
    by_platform = {}
    for game in games:
        platform = game.get('platform', 'unknown')
        by_platform.setdefault(platform, 0)
        by_platform[platform] += 1
    
    for platform, count in by_platform.items():
        print(f"  {platform}: {count} games")
    
    # Test ordered games
    ordered = service._ordered_owned_games()
    print(f"Ordered games: {len(ordered)}")
    
    if ordered:
        print("\nFirst 5 games:")
        for i, game in enumerate(ordered[:5]):
            platform = game.get('platform', 'steam')
            print(f"  {i+1}. [{platform}] {game.get('name', 'Unknown')} (installed: {game.get('installed', False)})")

def main():
    print("=== Multi-Platform Integration Test ===\n")
    
    # Test individual clients
    print("Testing individual platform clients:")
    
    steam = SteamClient()
    test_platform_client(steam)
    
    gog = GogClient()
    test_platform_client(gog)
    
    epic = EpicClient()
    test_platform_client(epic)
    
    # Test ViewerService integration
    test_viewer_service()
    
    print("\n=== Test Complete ===")

if __name__ == "__main__":
    main()