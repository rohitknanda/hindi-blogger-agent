"""
setup_auth.py
-------------
One-time Google OAuth2 authorisation for Blogger API.
Run this ONCE on your local machine to get a refresh token.
The token is saved to your .env file automatically.

Usage:
    python setup_auth.py

Requirements:
    1. Go to https://console.cloud.google.com
    2. Create/select a project
    3. Enable "Blogger API v3"
    4. Create OAuth2 credentials (Desktop app)
    5. Copy Client ID and Client Secret below when prompted
"""

import os
import re
from pathlib import Path

from google_auth_oauthlib.flow import InstalledAppFlow

SCOPES = ["https://www.googleapis.com/auth/blogger"]

BANNER = """
╔══════════════════════════════════════════════════════╗
║       Hindi Auto-Blogger — Google Auth Setup         ║
╚══════════════════════════════════════════════════════╝

You need:  Google Cloud Console → Blogger API v3 enabled
           → OAuth2 Client ID (Desktop app type)
"""


def update_env(key: str, value: str, env_path: Path):
    content = env_path.read_text(encoding="utf-8") if env_path.exists() else ""
    pattern = f"^{key}=.*$"
    if re.search(pattern, content, re.MULTILINE):
        content = re.sub(pattern, f"{key}={value}", content, flags=re.MULTILINE)
    else:
        content = content.rstrip("\n") + f"\n{key}={value}\n"
    env_path.write_text(content, encoding="utf-8")


def main():
    print(BANNER)

    client_id = input("Paste your OAuth2 Client ID:     ").strip()
    client_secret = input("Paste your OAuth2 Client Secret: ").strip()

    if not client_id or not client_secret:
        print("Client ID and Secret are required.")
        return

    client_config = {
        "installed": {
            "client_id": client_id,
            "client_secret": client_secret,
            "redirect_uris": ["urn:ietf:wg:oauth:2.0:oob", "http://localhost"],
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
        }
    }

    print("\nOpening browser for Google sign-in...")
    flow = InstalledAppFlow.from_client_config(client_config, SCOPES)
    creds = flow.run_local_server(port=8080, open_browser=True)

    if not creds.refresh_token:
        print("No refresh token returned. Use Desktop app credential type.")
        return

    env_path = Path(".env")
    update_env("GOOGLE_CLIENT_ID", client_id, env_path)
    update_env("GOOGLE_CLIENT_SECRET", client_secret, env_path)
    update_env("GOOGLE_REFRESH_TOKEN", creds.refresh_token, env_path)

    print(f"\n✅  Success! Credentials saved to {env_path.resolve()}")
    print("   Now run:  python agent.py")


if __name__ == "__main__":
    main()
