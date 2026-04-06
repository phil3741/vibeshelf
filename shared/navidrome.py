"""Navidrome / Subsonic API authentication and URL helpers."""

import hashlib
import os
import random
import string


def nd_config_from_env() -> tuple[str, str, str]:
    return (
        os.environ.get("NAVIDROME_URL", ""),
        os.environ.get("NAVIDROME_USER", ""),
        os.environ.get("NAVIDROME_PASSWORD", ""),
    )


def nd_auth_params(user: str, password: str, *, fmt: str = "json") -> dict:
    salt = ''.join(random.choices(string.ascii_lowercase + string.digits, k=12))
    token = hashlib.md5((password + salt).encode()).hexdigest()
    return {
        'u': user, 't': token, 's': salt,
        'v': '1.16.1', 'c': 'byebyespotify', 'f': fmt,
    }


def nd_cover_url(nd_url: str, user: str, password: str, cover_id: str, size: int = 300) -> str:
    p = nd_auth_params(user, password)
    return (
        f"{nd_url}/rest/getCoverArt.view?id={cover_id}&size={size}"
        f"&u={p['u']}&t={p['t']}&s={p['s']}&v={p['v']}&c={p['c']}"
    )


def nd_stream_url(nd_url: str, user: str, password: str, song_id: str) -> str:
    p = nd_auth_params(user, password)
    return (
        f"{nd_url}/rest/stream.view?id={song_id}&u={p['u']}&t={p['t']}&s={p['s']}"
        f"&v={p['v']}&c={p['c']}&format=mp3"
    )


def nd_scrobble_url(nd_url: str, user: str, password: str) -> str:
    p = nd_auth_params(user, password)
    return (
        f"{nd_url}/rest/scrobble.view?u={p['u']}&t={p['t']}&s={p['s']}"
        f"&v={p['v']}&c={p['c']}"
    )
