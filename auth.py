#!/usr/bin/env python3
"""
One-time OAuth authorization for ByeByeSpotify.
Run this interactively to get the initial token.
"""
import os
from dotenv import load_dotenv
import spotipy
from spotipy.oauth2 import SpotifyOAuth

load_dotenv()

scope = " ".join([
    "user-library-read",
    "user-read-recently-played",
    "user-top-read",
    "user-follow-read",
    "playlist-read-private",
    "playlist-read-collaborative",
    "streaming",
    "user-read-email",
    "user-read-private",
])

cache_path = os.environ.get("SPOTIPY_CACHE_PATH", "/app/.cache/.spotipy_cache")
os.makedirs(os.path.dirname(cache_path), exist_ok=True)

auth = SpotifyOAuth(
    client_id=os.getenv("SPOTIFY_CLIENT_ID"),
    client_secret=os.getenv("SPOTIFY_CLIENT_SECRET"),
    redirect_uri=os.getenv("SPOTIFY_REDIRECT_URI", "http://127.0.0.1:8888"),
    scope=scope,
    cache_path=cache_path,
    open_browser=False
)

print("\n" + "="*60)
print("SPOTIFY OAUTH AUTHORIZATION")
print("="*60)
print("\n1. Open this URL in your browser:")
print("\n" + auth.get_authorize_url())
print("\n2. Log in to Spotify and authorize the app")
print("3. You'll be redirected to a page that won't load (that's OK!)")
print("4. Copy the ENTIRE URL from your browser's address bar")
print("5. Paste it below:\n")

redirect_url = input("Paste the redirect URL here: ").strip()

try:
    code = auth.parse_response_code(redirect_url)
    token = auth.get_access_token(code)
    print("\n✓ Authorization successful! Token saved to:", cache_path)

    # Verify by getting user info
    sp = spotipy.Spotify(auth_manager=auth)
    user = sp.current_user()
    print(f"✓ Logged in as: {user['display_name']}")
except Exception as e:
    print(f"\n✗ Authorization failed: {e}")
    raise
