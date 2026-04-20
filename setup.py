#!/usr/bin/env python3
"""
Setup script for Steam Twitch Viewer Dashboard
"""

import os
import sys
import subprocess
import getpass


def print_header():
    print("="*60)
    print("Steam Twitch Viewer Dashboard Setup")
    print("="*60)
    print()


def check_python_version():
    """Check Python version"""
    print("Checking Python version...")
    if sys.version_info < (3, 7):
        print(f"ERROR: Python 3.7 or higher is required (you have {sys.version_info.major}.{sys.version_info.minor})")
        return False
    print(f"[OK] Python {sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}")
    return True


def install_dependencies():
    """Install required Python packages"""
    print("\nInstalling dependencies...")
    
    try:
        # Upgrade pip first
        subprocess.check_call([sys.executable, "-m", "pip", "install", "--upgrade", "pip"])
        
        # Install requirements
        subprocess.check_call([sys.executable, "-m", "pip", "install", "-r", "requirements.txt"])
        print("[OK] Dependencies installed successfully")
        return True
    except subprocess.CalledProcessError as e:
        print(f"[ERROR] Failed to install dependencies: {e}")
        return False


def setup_environment():
    """Setup environment configuration"""
    print("\nSetting up environment...")
    
    # Check if .env file exists
    if os.path.exists(".env"):
        print("[OK] .env file already exists")
        return True
    
    # Create .env file from example
    if os.path.exists(".env.example"):
        with open(".env.example", "r") as f:
            example_content = f.read()
        
        # Get user input for configuration
        print("\nPlease provide the following configuration:")
        print("-"*40)
        
        # Steam User ID
        steam_user_id = input("Enter your Steam User ID (find it in Steam/userdata/ folder): ").strip()
        
        # Check Windows environment variables for Twitch credentials
        print("\nChecking Windows environment variables for Twitch credentials...")
        twitch_client_id = os.environ.get('TWITCH_CLIENT_ID')
        twitch_client_secret = os.environ.get('TWITCH_CLIENT_SECRET')
        
        if twitch_client_id and twitch_client_secret:
            print("[OK] Twitch credentials found in Windows environment variables")
        else:
            print("\n[INFO] Twitch credentials not found in Windows environment variables")
            print("Please set these Windows environment variables:")
            print("1. Press Win + X → System → Advanced system settings")
            print("2. Click 'Environment Variables'")
            print("3. Under 'User variables', click 'New'")
            print("4. Add TWITCH_CLIENT_ID and TWITCH_CLIENT_SECRET")
            print("5. Restart any open command prompts after setting")
            print("\nGet Twitch credentials from: https://dev.twitch.tv/console")
        
        # Create .env file (only non-secret values)
        env_content = f"""# Steam Configuration
# Steam WebAPI Key (required to fetch owned games; set as Windows env var: STEAM_API_KEY)
# STEAM_API_KEY=

# Steam User Configuration
STEAM_USER_ID={steam_user_id}

# Twitch Configuration (set as Windows environment variables)
# TWITCH_CLIENT_ID=
# TWITCH_CLIENT_SECRET=
"""
        
        with open(".env", "w") as f:
            f.write(env_content)
        
        print("\n[OK] .env file created successfully")
        print("\nIMPORTANT: Twitch credentials should be set as Windows environment variables:")
        print("  - TWITCH_CLIENT_ID")
        print("  - TWITCH_CLIENT_SECRET")
        print("\nIMPORTANT: Set STEAM_API_KEY as a Windows environment variable.")
        print("It is required to fetch your owned games from Steam Web API.")
        print("Get one at: https://steamcommunity.com/dev/apikey")
        return True
    else:
        print("[ERROR] .env.example file not found")
        return False


def test_config():
    """Test application configuration"""
    print("\nTesting configuration...")
    
    from config import config
    
    try:
        config.validate()
        print("[OK] Configuration validated")
        return True
    except Exception as e:
        print(f"[ERROR] Configuration error: {e}")
        return False


def main():
    """Main setup function"""
    print_header()
    
    # Check Python version
    if not check_python_version():
        return
    
    # Install dependencies
    if not install_dependencies():
        return
    
    # Setup environment
    if not setup_environment():
        return
    
    # Test config
    test_config()
    
    print("\n" + "="*60)
    print("SETUP COMPLETE!")
    print("="*60)
    print("\nNext steps:")
    print("1. Make sure STEAM_API_KEY, TWITCH_CLIENT_ID, and TWITCH_CLIENT_SECRET are set")
    print("2. Run the dashboard: python main.py")
    print("3. For testing with fewer games: python main.py --limit 10")
    print("\nThe tool will:")
    print("  - Fetch your owned Steam games")
    print("  - Get current Twitch viewer counts for each game")
    print("  - Open a local web dashboard in your browser")
    print("\nNote: The first run may take a while as it builds a game name cache.")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n\nSetup cancelled by user")
    except Exception as e:
        print(f"\nError during setup: {e}")
        import traceback
        traceback.print_exc()
