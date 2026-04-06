"""SpotifyOAuth factory and scope constants."""

import os

from spotipy.oauth2 import SpotifyOAuth

SCOPES_EXPORTER = [
    "user-library-read",
    "user-read-recently-played",
    "user-top-read",
    "user-follow-read",
    "playlist-read-private",
    "playlist-read-collaborative",
]

SCOPES_WEB = SCOPES_EXPORTER + [
    "streaming",
    "user-read-email",
    "user-read-private",
]


def make_spotify_oauth(scopes: list[str] | None = None) -> SpotifyOAuth:
    if scopes is None:
        scopes = SCOPES_EXPORTER
    return SpotifyOAuth(
        client_id=os.getenv("SPOTIFY_CLIENT_ID"),
        client_secret=os.getenv("SPOTIFY_CLIENT_SECRET"),
        redirect_uri=os.getenv("SPOTIFY_REDIRECT_URI", "http://127.0.0.1:8888"),
        scope=" ".join(scopes),
        cache_path=os.getenv("SPOTIPY_CACHE_PATH", ".cache"),
        open_browser=False,
    )


def get_fresh_token(scopes: list[str] | None = None) -> dict | None:
    """Return a valid access token dict, refreshing if expired. Returns None if no cached token."""
    auth = make_spotify_oauth(scopes)
    token_info = auth.get_cached_token()
    if not token_info:
        return None
    if auth.is_token_expired(token_info):
        token_info = auth.refresh_access_token(token_info["refresh_token"])
    return token_info
