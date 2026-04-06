#!/usr/bin/env python3
"""
ByeByeSpotify - Incremental Spotify metadata backup service.
Runs as a persistent loop, exporting categories on independent schedules.
"""

import os
import sys
import json
import csv
import time
import logging
import sqlite3
import hashlib as _hl
import random as _rnd
import string as _str
import threading
import requests as http_requests
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import spotipy
from spotipy.oauth2 import SpotifyOAuth
from spotipy.exceptions import SpotifyException
from dotenv import load_dotenv
from shared.db import get_db as _shared_get_db, init_db as _shared_init_db
from shared.navidrome import nd_auth_params as _nd_auth_params
from shared.tracks import normalize_track as _shared_normalize_track, build_genre_map as _shared_build_genre_map
from shared.spotify_auth import make_spotify_oauth, SCOPES_EXPORTER

load_dotenv()

# Integration detection — presence of env vars determines what's active
_SPOTIFY_CONFIGURED = bool(os.getenv("SPOTIFY_CLIENT_ID") and os.getenv("SPOTIFY_CLIENT_SECRET"))
_NAVIDROME_CONFIGURED = bool(os.getenv("NAVIDROME_URL") and os.getenv("NAVIDROME_USER") and os.getenv("NAVIDROME_PASSWORD"))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    stream=sys.stdout,
)
log = logging.getLogger("byebyespotify")

# ---------------------------------------------------------------------------
# Schedule intervals (hours)
# ---------------------------------------------------------------------------
SCHEDULES = {
    "recently_played": 2,
    "saved_tracks": 6,
    "playlists": 6,
    "saved_albums": 24,
    "followed_artists": 24,
    "saved_shows": 24,
    "saved_episodes": 24,
    "artist_genres": 24,
    "top_tracks": 168,      # weekly
    "top_artists": 168,     # weekly
    "navidrome": 6,          # same as saved_tracks
}

LOOP_SLEEP_SECONDS = 60  # check every minute
WEB_PORT = int(os.getenv("WEB_PORT", "8080"))


# ---------------------------------------------------------------------------
# Error log (persisted for web UI)
# ---------------------------------------------------------------------------
def log_error(export_dir: Path, category: str, error: str, level: str = "error"):
    """Append an error to errors.json (max 100 entries)."""
    path = export_dir / "errors.json"
    try:
        existing = json.loads(path.read_text()) if path.exists() else []
    except (json.JSONDecodeError, OSError):
        existing = []
    existing.append({
        "timestamp": time.time(),
        "category": category,
        "error": str(error),
        "level": level,
    })
    existing = existing[-100:]  # keep last 100
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(existing, indent=2))
    tmp.replace(path)


# ---------------------------------------------------------------------------
# Gotify notifications
# ---------------------------------------------------------------------------
def notify(title: str, message: str, priority: int = 5):
    """Send a Gotify notification."""
    url = os.getenv("GOTIFY_URL")
    token_path = "/run/secrets/gotify"
    if not url:
        return
    try:
        token = Path(token_path).read_text().strip()
    except FileNotFoundError:
        log.warning("Gotify token not found at %s", token_path)
        return
    try:
        http_requests.post(
            f"{url}/message",
            params={"token": token},
            data={"title": title, "message": message, "priority": str(priority)},
            timeout=10,
        )
    except Exception as e:
        log.warning("Gotify send failed: %s", e)


# ---------------------------------------------------------------------------
# State persistence
# ---------------------------------------------------------------------------
class State:
    """Tracks last-run times and playlist snapshot IDs."""

    def __init__(self, path: Path):
        self._path = path
        self._data: dict = {}
        if self._path.exists():
            try:
                self._data = json.loads(self._path.read_text())
            except (json.JSONDecodeError, OSError):
                self._data = {}

    def save(self):
        self._path.write_text(json.dumps(self._data, indent=2))

    # -- last-run helpers --
    def last_run(self, category: str) -> float:
        return self._data.get("last_run", {}).get(category, 0)

    def mark_run(self, category: str):
        self._data.setdefault("last_run", {})[category] = time.time()
        self.save()

    def should_run(self, category: str, interval_hours: float) -> bool:
        elapsed = time.time() - self.last_run(category)
        return elapsed >= interval_hours * 3600

    # -- playlist snapshot IDs --
    def get_snapshots(self) -> dict:
        return self._data.get("playlist_snapshots", {})

    def set_snapshots(self, snapshots: dict):
        self._data["playlist_snapshots"] = snapshots
        self.save()


# ---------------------------------------------------------------------------
# Core exporter
# ---------------------------------------------------------------------------
class SpotifyExporter:

    def __init__(self):
        self._spotify_configured = _SPOTIFY_CONFIGURED
        if self._spotify_configured:
            self._auth_manager = make_spotify_oauth(SCOPES_EXPORTER)
            self.sp = spotipy.Spotify(
                auth_manager=self._auth_manager,
                retries=0,
                status_retries=0,
            )
        else:
            self._auth_manager = None
            self.sp = None

        self.export_dir = Path(os.getenv("EXPORT_DIR", "exports"))
        self.export_dir.mkdir(exist_ok=True)
        self.state = State(self.export_dir / "state.json")
        self._sp_dc = os.getenv("SPOTIFY_SP_DC", "")
        self._web_token = None
        self._web_token_exp = 0
        self._rate_limited_until = 0
        # Navidrome (Subsonic API)
        self._nd_url = os.getenv("NAVIDROME_URL", "")
        self._nd_user = os.getenv("NAVIDROME_USER", "")
        self._nd_password = os.getenv("NAVIDROME_PASSWORD", "")
        self._init_db()

    # -- SQLite database --

    def _init_db(self):
        self._db_path = self.export_dir / "library.db"
        _shared_init_db(self._db_path)
        # First-run migration from JSON
        db = self._get_db()
        count = db.execute("SELECT COUNT(*) FROM tracks").fetchone()[0]
        if count == 0:
            self._migrate_from_json(db)
        db.close()

    def _get_db(self) -> sqlite3.Connection:
        return _shared_get_db(self._db_path)

    def _normalize_track(self, item: dict, source: str, source_name: str, genre_map: dict) -> dict | None:
        """Normalize a Spotify track item to the standard track dict for DB insertion."""
        t = _shared_normalize_track(item, source, source_name, genre_map)
        if t is None:
            return None
        t["album_id"] = ""
        t["name_lower"] = t["name"].lower()
        t["artist_lower"] = t["artist"].lower()
        return t

    _INSERT_TRACK = """INSERT OR REPLACE INTO tracks
        (name, artist, album, duration_ms, year, genre, uri, image,
         source, source_name, added_at, platform, album_id, name_lower, artist_lower)
        VALUES (:name, :artist, :album, :duration_ms, :year, :genre, :uri, :image,
                :source, :source_name, :added_at, :platform, :album_id, :name_lower, :artist_lower)"""

    def _write_tracks_to_db(self, db: sqlite3.Connection, tracks: list[dict], source: str, source_name: str | None = None):
        """Delete old rows for source/source_name and insert new ones."""
        if source_name:
            db.execute("DELETE FROM tracks WHERE source=? AND source_name=?", (source, source_name))
        else:
            db.execute("DELETE FROM tracks WHERE source=?", (source,))
        for t in tracks:
            if t:
                db.execute(self._INSERT_TRACK, t)
        db.commit()

    def _update_cross_platform_dedup(self, db: sqlite3.Connection):
        """Mark Spotify tracks that also exist in Navidrome as platform='both'.

        Normalizes artist separators: Spotify uses ', ' while Navidrome uses ' • '.
        Also marks the Navidrome side so the 'all' query can skip Navidrome dupes.
        """
        # Reset platforms
        db.execute("UPDATE tracks SET platform='spotify' WHERE source != 'navidrome'")
        db.execute("UPDATE tracks SET platform='navidrome' WHERE source = 'navidrome'")
        # Mark both sides where name matches and artist matches after normalizing separators
        db.execute("""
            UPDATE tracks SET platform='both'
            WHERE source != 'navidrome' AND EXISTS (
                SELECT 1 FROM tracks AS nd
                WHERE nd.source = 'navidrome'
                  AND nd.name_lower = tracks.name_lower
                  AND REPLACE(nd.artist_lower, ' • ', ', ') = REPLACE(tracks.artist_lower, ' • ', ', ')
            )
        """)
        db.execute("""
            UPDATE tracks SET platform='both'
            WHERE source = 'navidrome' AND EXISTS (
                SELECT 1 FROM tracks AS sp
                WHERE sp.source != 'navidrome'
                  AND sp.name_lower = tracks.name_lower
                  AND REPLACE(sp.artist_lower, ' • ', ', ') = REPLACE(tracks.artist_lower, ' • ', ', ')
            )
        """)
        db.commit()

    def _rebuild_sidebar_cache(self, db: sqlite3.Connection):
        """Pre-compute sidebar aggregates into sidebar_cache table."""
        cache = {}
        cache['saved_count'] = db.execute("SELECT COUNT(*) FROM tracks WHERE source='saved'").fetchone()[0]
        cache['navidrome_count'] = db.execute("SELECT COUNT(*) FROM tracks WHERE source='navidrome'").fetchone()[0]
        cache['recent_count'] = db.execute("SELECT COUNT(*) FROM tracks WHERE source='recent'").fetchone()[0]
        # Deduped "All Music" count: all unique tracks minus navidrome-side duplicates
        cache['all_count'] = db.execute(
            "SELECT COUNT(*) FROM tracks WHERE source IN ('saved','playlist','navidrome') "
            "AND NOT (source = 'navidrome' AND platform = 'both') "
            "AND id IN (SELECT MIN(id) FROM tracks WHERE source IN ('saved','playlist','navidrome') "
            "AND NOT (source = 'navidrome' AND platform = 'both') GROUP BY uri)"
        ).fetchone()[0]
        # Split combined artist strings (", " from Spotify, " • " from Navidrome) into individual names
        artists_set = set()
        for (raw,) in db.execute(
            "SELECT DISTINCT artist FROM tracks WHERE source IN ('saved','playlist','navidrome') AND artist != ''"
        ):
            for sep in (', ', ' • '):
                if sep in raw:
                    for part in raw.split(sep):
                        p = part.strip()
                        if p:
                            artists_set.add(p)
                    break
            else:
                artists_set.add(raw.strip())
        cache['artists'] = sorted(artists_set, key=str.casefold)
        cache['albums'] = [r[0] for r in db.execute(
            "SELECT DISTINCT album FROM tracks WHERE source IN ('saved','playlist','navidrome') AND album != '' ORDER BY album COLLATE NOCASE"
        )]
        cache['genres'] = [r[0] for r in db.execute(
            "SELECT DISTINCT genre FROM tracks WHERE source IN ('saved','playlist','navidrome') AND genre != '' ORDER BY genre COLLATE NOCASE"
        )]
        cache['playlists'] = [{'id': r[0], 'name': r[1], 'count': r[2]} for r in db.execute(
            "SELECT id, name, total_tracks FROM playlists ORDER BY name COLLATE NOCASE"
        )]
        db.execute("DELETE FROM sidebar_cache")
        for k, v in cache.items():
            db.execute("INSERT INTO sidebar_cache (key, value) VALUES (?, ?)", (k, json.dumps(v, ensure_ascii=False)))
        db.commit()
        log.info("DB: sidebar cache rebuilt (saved=%d, nd=%d, artists=%d, albums=%d, genres=%d)",
                 cache['saved_count'], cache['navidrome_count'], len(cache['artists']), len(cache['albums']), len(cache['genres']))

    def _build_genre_map_for_db(self) -> dict:
        """Build {artist_name_lower: [genres]} from exported JSON files."""
        return _shared_build_genre_map(self.export_dir)

    def _migrate_from_json(self, db: sqlite3.Connection):
        """Populate DB from existing JSON files on first run."""
        log.info("DB: migrating from JSON files...")
        genre_map = self._build_genre_map_for_db()
        total = 0

        # Saved tracks
        path = self.export_dir / "saved_tracks.json"
        if path.exists():
            try:
                data = json.loads(path.read_text())
                if isinstance(data, list):
                    normalized = [self._normalize_track(item, "saved", "Library", genre_map) for item in data]
                    normalized = [t for t in normalized if t]
                    self._write_tracks_to_db(db, normalized, "saved", "Library")
                    total += len(normalized)
                    log.info("DB: migrated %d saved tracks", len(normalized))
            except (json.JSONDecodeError, OSError):
                pass

        # Playlists
        path = self.export_dir / "playlists.json"
        if path.exists():
            try:
                playlists = json.loads(path.read_text())
                if isinstance(playlists, list):
                    db.execute("DELETE FROM playlists")
                    db.execute("DELETE FROM tracks WHERE source='playlist'")
                    for pl in playlists:
                        db.execute("INSERT OR REPLACE INTO playlists (id, name, owner, total_tracks) VALUES (?, ?, ?, ?)",
                                   (pl.get("id", ""), pl.get("name", ""), pl.get("owner", ""), pl.get("total_tracks", 0)))
                        for item in pl.get("tracks", []):
                            t = self._normalize_track(item, "playlist", pl.get("name", "?"), genre_map)
                            if t:
                                db.execute(self._INSERT_TRACK, t)
                                total += 1
                    db.commit()
                    log.info("DB: migrated %d playlists", len(playlists))
            except (json.JSONDecodeError, OSError):
                pass

        # Recently played
        path = self.export_dir / "recently_played.json"
        if path.exists():
            try:
                data = json.loads(path.read_text())
                if isinstance(data, list):
                    normalized = []
                    for item in data:
                        t = self._normalize_track(item, "recent", "Recently Played", genre_map)
                        if t:
                            t["added_at"] = item.get("played_at", "")
                            normalized.append(t)
                    self._write_tracks_to_db(db, normalized, "recent")
                    total += len(normalized)
            except (json.JSONDecodeError, OSError):
                pass

        # Top tracks
        path = self.export_dir / "top_tracks.json"
        if path.exists():
            try:
                data = json.loads(path.read_text())
                if isinstance(data, dict):
                    db.execute("DELETE FROM tracks WHERE source='top'")
                    for time_range, items in data.items():
                        for item in items:
                            t = self._normalize_track({"track": item}, "top", time_range, genre_map)
                            if t:
                                db.execute(self._INSERT_TRACK, t)
                                total += 1
                    db.commit()
            except (json.JSONDecodeError, OSError):
                pass

        # Navidrome
        path = self.export_dir / "navidrome_library.json"
        if path.exists():
            try:
                data = json.loads(path.read_text())
                if isinstance(data, list):
                    db.execute("DELETE FROM tracks WHERE source='navidrome'")
                    for t in data:
                        t.setdefault("name_lower", t.get("name", "").lower())
                        t.setdefault("artist_lower", t.get("artist", "").lower())
                        db.execute(self._INSERT_TRACK, t)
                    db.commit()
                    total += len(data)
                    log.info("DB: migrated %d navidrome tracks", len(data))
            except (json.JSONDecodeError, OSError):
                pass

        self._update_cross_platform_dedup(db)
        self._rebuild_sidebar_cache(db)
        log.info("DB: migration complete, %d total tracks", total)

    # -- pagination with rate-limit handling --
    def _paginate(self, method, *args, **kwargs) -> list[dict]:
        results = []
        response = self._call(method, *args, **kwargs)
        while response:
            if isinstance(response, dict) and "items" in response:
                results.extend(response["items"])
                if response.get("next"):
                    response = self._call(self.sp.next, response)
                else:
                    break
            else:
                return response
        return results

    def _call(self, method, *args, **kwargs):
        """Call a Spotify API method with rate-limit retry and token refresh."""
        if time.time() < self._rate_limited_until:
            raise SpotifyException(429, -1, "Spotify rate-limited, skipping")
        for attempt in range(3):
            try:
                return method(*args, **kwargs)
            except SpotifyException as e:
                if e.http_status == 401:
                    log.warning("Token expired (attempt %d/3), refreshing...", attempt + 1)
                    token_info = self._auth_manager.refresh_access_token(
                        self._auth_manager.get_cached_token()["refresh_token"]
                    )
                    self.sp.set_auth(token_info["access_token"])
                    continue
                if e.http_status == 429:
                    if "Max Retries" in str(e.msg):
                        log.warning("Spotify long rate-limit detected, backing off for 1h")
                        self._rate_limited_until = time.time() + 3600
                        raise
                    retry_after = int(e.headers.get("Retry-After", 5)) if e.headers else 5
                    retry_after = min(retry_after, 30)
                    log.warning("Rate limited, waiting %ds (attempt %d/3)", retry_after, attempt + 1)
                    time.sleep(retry_after)
                    continue
                raise
        raise SpotifyException(429, -1, "Rate limit exceeded after retries")

    # -- Web token (sp_dc cookie) for external playlists --
    def _get_web_token(self) -> str | None:
        """Get a web player embed token using sp_dc cookie."""
        if not self._sp_dc:
            return None
        if self._web_token and time.time() < self._web_token_exp - 60:
            return self._web_token
        import re
        try:
            s = http_requests.Session()
            s.headers["User-Agent"] = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
            s.cookies.set("sp_dc", self._sp_dc, domain="open.spotify.com", path="/")
            # Fetch any embed page to extract the token
            r = s.get("https://open.spotify.com/embed/playlist/37i9dQZF1DXcBWIGoYBM5M", timeout=10)
            if r.status_code == 200:
                m = re.search(r"accessToken.*?([A-Za-z0-9_-]{100,})", r.text)
                if m:
                    self._web_token = m.group(1)
                    self._web_token_exp = time.time() + 3000  # ~50 min conservative
                    log.info("Web embed token obtained")
                    return self._web_token
            log.warning("Web token extraction failed: %d", r.status_code)
        except Exception as e:
            log.warning("Web token error: %s", e)
        return None

    @staticmethod
    def _b62_to_hex(b62: str) -> str:
        """Convert a Spotify base62 ID to a hex gid for spclient."""
        alphabet = "0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ"
        n = 0
        for c in b62:
            n = n * 62 + alphabet.index(c)
        return format(n, "032x")

    @staticmethod
    def _spclient_track_to_api(meta: dict, track_id: str) -> dict:
        """Convert spclient metadata format to standard Spotify API track format."""
        album = meta.get("album", {})
        date = album.get("date", {})
        release_date = ""
        if date.get("year"):
            release_date = f"{date['year']}"
            if date.get("month"):
                release_date += f"-{date['month']:02d}"
                if date.get("day"):
                    release_date += f"-{date['day']:02d}"
        images = []
        for img in album.get("cover_group", {}).get("image", []):
            fid = img.get("file_id", "")
            if fid:
                images.append({
                    "url": f"https://i.scdn.co/image/{fid}",
                    "width": img.get("width"), "height": img.get("height"),
                })
        return {
            "id": track_id,
            "name": meta.get("name", "Unknown"),
            "uri": f"spotify:track:{track_id}",
            "artists": [{"name": a.get("name", "Unknown")} for a in meta.get("artist", [])],
            "album": {
                "name": album.get("name", "Unknown"),
                "release_date": release_date,
                "images": images,
            },
            "duration_ms": meta.get("duration", 0),
            "popularity": meta.get("popularity", 0),
            "explicit": meta.get("explicit", False),
        }

    def _fetch_playlist_tracks_web(self, pid: str, name: str) -> list:
        """Fetch external playlist tracks: URIs via spclient, details via spclient metadata."""
        token = self._get_web_token()
        if not token:
            return []
        headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
        # Step 1: Get track URIs from the internal playlist API
        try:
            r = http_requests.get(
                f"https://spclient.wg.spotify.com/playlist/v2/playlist/{pid}",
                headers=headers, timeout=15,
            )
            if r.status_code != 200:
                log.warning("    spclient fetch failed for '%s': %d", name, r.status_code)
                return []
            items = r.json().get("contents", {}).get("items", [])
        except Exception as e:
            log.warning("    spclient error for '%s': %s", name, e)
            return []

        uris = [item["uri"] for item in items if item.get("uri", "").startswith("spotify:track:")]
        if not uris:
            return []
        log.info("    Got %d track URIs via spclient, fetching details...", len(uris))

        # Step 2: Fetch track details via spclient metadata (uses web token, separate rate limit)
        tracks = []
        seen = set()
        for i, uri in enumerate(uris):
            track_id = uri.split(":")[-1]
            if track_id in seen:
                continue
            seen.add(track_id)
            try:
                hex_gid = self._b62_to_hex(track_id)
                r = http_requests.get(
                    f"https://spclient.wg.spotify.com/metadata/4/track/{hex_gid}",
                    headers=headers, timeout=10,
                )
                if r.status_code == 429:
                    retry_after = int(r.headers.get("Retry-After", 5))
                    log.info("    Rate limited on metadata, waiting %ds...", retry_after)
                    time.sleep(min(retry_after, 30))
                    r = http_requests.get(
                        f"https://spclient.wg.spotify.com/metadata/4/track/{hex_gid}",
                        headers=headers, timeout=10,
                    )
                if r.status_code != 200:
                    log.warning("    Metadata fetch failed for track %s: %d", track_id, r.status_code)
                    continue
                meta = r.json()
                track = self._spclient_track_to_api(meta, track_id)
                tracks.append({"track": track, "added_at": None})
                if (len(tracks)) % 50 == 0:
                    log.info("    Fetched %d/%d track details for '%s'", len(tracks), len(seen), name)
            except Exception as e:
                log.warning("    Metadata error for track %s: %s", track_id, e)
                continue
            time.sleep(0.05)
        return tracks

    # -- JSON/CSV export helpers --
    def _save_json(self, data: Any, filename: str):
        path = self.export_dir / filename
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False))
        tmp.replace(path)

    def _save_csv(self, tracks: list[dict], filename: str, track_key: str | None = "track"):
        path = self.export_dir / filename
        if not tracks:
            return
        rows = []
        for item in tracks:
            track = item.get(track_key) if track_key and track_key in item else item
            if not track:
                continue
            rows.append({
                "Track Name": track.get("name", "Unknown"),
                "Artist(s)": ", ".join(a["name"] for a in track.get("artists", [])),
                "Album": track.get("album", {}).get("name", "Unknown"),
                "Release Date": track.get("album", {}).get("release_date", ""),
                "Duration (ms)": track.get("duration_ms", 0),
                "Popularity": track.get("popularity", 0),
                "Spotify URI": track.get("uri", ""),
                "Added At": item.get("added_at", ""),
            })
        if rows:
            with open(path, "w", newline="", encoding="utf-8") as f:
                w = csv.DictWriter(f, fieldnames=rows[0].keys())
                w.writeheader()
                w.writerows(rows)

    # -- category exporters --
    def export_saved_tracks(self):
        log.info("Exporting saved tracks...")
        tracks = self._paginate(self.sp.current_user_saved_tracks, limit=50)
        self._save_json(tracks, "saved_tracks.json")
        self._save_csv(tracks, "saved_tracks.csv", track_key="track")
        log.info("Saved tracks: %d", len(tracks))
        # Write to DB
        genre_map = self._build_genre_map_for_db()
        normalized = [self._normalize_track(item, "saved", "Library", genre_map) for item in tracks]
        normalized = [t for t in normalized if t]
        db = self._get_db()
        self._write_tracks_to_db(db, normalized, "saved", "Library")
        self._update_cross_platform_dedup(db)
        self._rebuild_sidebar_cache(db)
        db.close()
        return len(tracks)

    def export_playlists(self):
        log.info("Exporting playlists...")
        playlists = self._paginate(self.sp.current_user_playlists, limit=50)

        # We need the user ID to distinguish own vs followed playlists.
        # Followed (external) playlists return 403 on track fetches due to
        # Spotify API restrictions for non-approved apps, so we skip them
        # and only store their metadata.
        user_id = self._call(self.sp.current_user)["id"]

        old_snapshots = self.state.get_snapshots()
        new_snapshots = {}
        detailed = []
        fetched = 0
        skipped = 0
        external = 0

        for i, pl in enumerate(playlists, 1):
            pid = pl["id"]
            name = pl["name"]
            snapshot = pl.get("snapshot_id", "")
            owner_id = pl.get("owner", {}).get("id", "")
            new_snapshots[pid] = snapshot

            tracks_info = pl.get("tracks") or pl.get("items") or {}
            total = tracks_info.get("total", 0) if isinstance(tracks_info, dict) else 0

            # External playlists: use web token directly (standard API returns track=None)
            if owner_id != user_id:
                external += 1
                if self._sp_dc:
                    log.info("  [%d/%d] %s (external, fetching via web token)", i, len(playlists), name)
                    tracks = self._fetch_playlist_tracks_web(pid, name)
                    if tracks:
                        log.info("  [%d/%d] %s (external, %d tracks)", i, len(playlists), name, len(tracks))
                        fetched += 1
                    else:
                        log.info("  [%d/%d] %s (external, web fetch failed)", i, len(playlists), name)
                        tracks = []
                else:
                    log.info("  [%d/%d] %s (external, no sp_dc token)", i, len(playlists), name)
                    tracks = []
            # Only fetch tracks if snapshot changed
            elif snapshot and snapshot == old_snapshots.get(pid):
                existing = self.export_dir / "playlists.json"
                cached_tracks = []
                if existing.exists():
                    try:
                        old_data = json.loads(existing.read_text())
                        for old_pl in old_data:
                            if old_pl.get("id") == pid:
                                cached_tracks = old_pl.get("tracks", [])
                                break
                    except (json.JSONDecodeError, OSError):
                        pass
                if cached_tracks:
                    tracks = cached_tracks
                    skipped += 1
                    log.info("  [%d/%d] %s (unchanged)", i, len(playlists), name)
                else:
                    tracks = self._fetch_playlist_tracks(pid, name, i, len(playlists))
                    fetched += 1
            else:
                tracks = self._fetch_playlist_tracks(pid, name, i, len(playlists))
                fetched += 1

            detailed.append({
                "id": pid,
                "name": name,
                "description": pl.get("description", ""),
                "public": pl.get("public", False),
                "collaborative": pl.get("collaborative", False),
                "owner": pl.get("owner", {}).get("display_name", "Unknown"),
                "owner_id": owner_id,
                "snapshot_id": snapshot,
                "total_tracks": total,
                "tracks": tracks,
            })

        self._save_json(detailed, "playlists.json")
        self.state.set_snapshots(new_snapshots)

        # Individual playlist CSVs
        for pl in detailed:
            safe = "".join(c for c in pl["name"] if c.isalnum() or c in (" ", "_")).strip()
            safe = safe.replace(" ", "_")[:50]
            if pl["tracks"]:
                self._save_csv(pl["tracks"], f"playlist_{safe}.csv", track_key="track")

        log.info("Playlists: %d total, %d fetched, %d cached, %d external",
                 len(playlists), fetched, skipped, external)
        # Write to DB
        genre_map = self._build_genre_map_for_db()
        db = self._get_db()
        db.execute("DELETE FROM tracks WHERE source='playlist'")
        db.execute("DELETE FROM playlists")
        for pl in detailed:
            db.execute("INSERT OR REPLACE INTO playlists (id, name, owner, total_tracks) VALUES (?, ?, ?, ?)",
                       (pl["id"], pl["name"], pl.get("owner", ""), pl.get("total_tracks", 0)))
            for item in pl.get("tracks", []):
                t = self._normalize_track(item, "playlist", pl["name"], genre_map)
                if t:
                    db.execute(self._INSERT_TRACK, t)
        db.commit()
        self._update_cross_platform_dedup(db)
        self._rebuild_sidebar_cache(db)
        db.close()
        return len(playlists)

    def _fetch_playlist_tracks(self, pid: str, name: str, idx: int, total: int) -> list:
        log.info("  [%d/%d] %s (fetching tracks)", idx, total, name)
        try:
            return self._paginate(self.sp.playlist_tracks, pid, limit=100)
        except Exception as e:
            log.warning("    Could not fetch tracks for '%s': %s", name, e)
            return []

    def export_saved_albums(self):
        log.info("Exporting saved albums...")
        albums = self._paginate(self.sp.current_user_saved_albums, limit=50)
        self._save_json(albums, "saved_albums.json")
        log.info("Saved albums: %d", len(albums))
        return len(albums)

    def export_followed_artists(self):
        log.info("Exporting followed artists...")
        artists = []
        response = self._call(self.sp.current_user_followed_artists, limit=50)
        while response:
            if "artists" in response and "items" in response["artists"]:
                artists.extend(response["artists"]["items"])
                if response["artists"].get("next"):
                    response = self._call(self.sp.next, response["artists"])
                else:
                    break
            else:
                break
        self._save_json(artists, "followed_artists.json")
        log.info("Followed artists: %d", len(artists))
        return len(artists)

    def export_top_tracks(self):
        log.info("Exporting top tracks...")
        result = {}
        for range_key in ("short_term", "medium_term", "long_term"):
            data = self._call(self.sp.current_user_top_tracks, limit=50, time_range=range_key)
            result[range_key] = data["items"]
            self._save_csv(data["items"], f"top_tracks_{range_key}.csv", track_key=None)
        self._save_json(result, "top_tracks.json")
        log.info("Top tracks exported")
        # Write to DB
        genre_map = self._build_genre_map_for_db()
        db = self._get_db()
        db.execute("DELETE FROM tracks WHERE source='top'")
        for time_range, items in result.items():
            for item in items:
                t = self._normalize_track({"track": item}, "top", time_range, genre_map)
                if t:
                    db.execute(self._INSERT_TRACK, t)
        db.commit()
        db.close()

    def export_top_artists(self):
        log.info("Exporting top artists...")
        result = {}
        for range_key in ("short_term", "medium_term", "long_term"):
            data = self._call(self.sp.current_user_top_artists, limit=50, time_range=range_key)
            result[range_key] = data["items"]
        self._save_json(result, "top_artists.json")
        log.info("Top artists exported")

    def export_recently_played(self):
        """Fetch the latest 50 recently played tracks."""
        log.info("Fetching recently played...")
        items = self._call(self.sp.current_user_recently_played, limit=50)["items"]
        self._save_json(items, "recently_played.json")
        log.info("Recently played: %d tracks", len(items))
        # Write to DB
        genre_map = self._build_genre_map_for_db()
        normalized = []
        for item in items:
            t = self._normalize_track(item, "recent", "Recently Played", genre_map)
            if t:
                t["added_at"] = item.get("played_at", "")
                normalized.append(t)
        db = self._get_db()
        self._write_tracks_to_db(db, normalized, "recent")
        self._rebuild_sidebar_cache(db)
        db.close()

    def export_saved_shows(self):
        log.info("Exporting saved shows...")
        shows = self._paginate(self.sp.current_user_saved_shows, limit=50)
        self._save_json(shows, "saved_shows.json")
        log.info("Saved shows: %d", len(shows))
        return len(shows)

    def export_saved_episodes(self):
        log.info("Exporting saved episodes...")
        episodes = self._paginate(self.sp.current_user_saved_episodes, limit=50)
        self._save_json(episodes, "saved_episodes.json")
        log.info("Saved episodes: %d", len(episodes))
        return len(episodes)

    def export_artist_genres(self):
        """Fetch genre/tag data for artists via MusicBrainz (free, no auth needed)."""
        log.info("Exporting artist genres via MusicBrainz...")
        saved = self.export_dir / "saved_tracks.json"
        if not saved.exists():
            log.info("No saved_tracks.json yet, skipping genre export")
            return

        try:
            tracks = json.loads(saved.read_text())
        except (json.JSONDecodeError, OSError):
            return

        # Collect unique artist names
        artists = {}
        for item in tracks:
            track = item.get("track", {})
            for a in track.get("artists", []):
                name = a.get("name", "").strip()
                aid = a.get("id", "")
                if name and aid not in artists:
                    artists[aid] = name

        # Load existing cache to avoid re-fetching
        cache_path = self.export_dir / "artist_genres.json"
        existing = {}
        if cache_path.exists():
            try:
                existing = json.loads(cache_path.read_text())
            except (json.JSONDecodeError, OSError):
                pass

        # Only fetch artists we don't have yet
        to_fetch = {aid: name for aid, name in artists.items()
                    if aid not in existing or not existing[aid].get("genres")}

        log.info("Artists: %d total, %d cached, %d to fetch from MusicBrainz",
                 len(artists), len(artists) - len(to_fetch), len(to_fetch))

        result = dict(existing)
        fetched = 0
        for aid, name in to_fetch.items():
            try:
                # MusicBrainz search: 1 req/sec rate limit
                resp = http_requests.get(
                    "https://musicbrainz.org/ws/2/artist/",
                    params={"query": f'artist:"{name}"', "limit": "1", "fmt": "json"},
                    headers={"User-Agent": "ByeByeSpotify/1.0 (backup-tool)"},
                    timeout=10,
                )
                if resp.status_code == 200:
                    data = resp.json()
                    mb_artists = data.get("artists", [])
                    if mb_artists:
                        tags = mb_artists[0].get("tags", [])
                        # Sort by count (relevance), take top 3
                        tags.sort(key=lambda t: t.get("count", 0), reverse=True)
                        genres = [t["name"].title() for t in tags[:3] if t.get("name")]
                        result[aid] = {"name": name, "genres": genres}
                        fetched += 1
                    else:
                        result[aid] = {"name": name, "genres": []}
                elif resp.status_code == 503:
                    log.warning("MusicBrainz rate limited, saving progress and stopping")
                    break
                time.sleep(1.1)  # Respect MusicBrainz rate limit
            except Exception as e:
                log.warning("MusicBrainz lookup failed for '%s': %s", name, e)
                result[aid] = {"name": name, "genres": []}
                time.sleep(1.1)

            # Save progress every 50 artists
            if fetched % 50 == 0 and fetched > 0:
                self._save_json(result, "artist_genres.json")
                log.info("  Progress: %d/%d fetched", fetched, len(to_fetch))

        self._save_json(result, "artist_genres.json")
        with_genres = sum(1 for v in result.values() if v.get("genres"))
        log.info("Artist genres: %d artists total, %d with genres (%d newly fetched)",
                 len(result), with_genres, fetched)
        # Re-apply genres to saved tracks in DB and rebuild sidebar
        if fetched > 0:
            genre_map = self._build_genre_map_for_db()
            db = self._get_db()
            # Update genre field for saved tracks that have an empty genre
            for name_lower, genres in genre_map.items():
                if genres:
                    db.execute("UPDATE tracks SET genre=? WHERE artist_lower LIKE ? AND genre=''",
                               (genres[0].title(), name_lower + "%"))
            db.commit()
            self._rebuild_sidebar_cache(db)
            db.close()

    # -- Navidrome (Subsonic API) --
    def _nd_params(self):
        """Build Subsonic API auth query params."""
        return _nd_auth_params(self._nd_user, self._nd_password)

    def export_navidrome(self):
        """Fetch all songs from Navidrome via Subsonic API."""
        if not self._nd_url:
            return
        log.info("Exporting Navidrome library...")
        import httpx
        tracks = []
        offset = 0
        batch_size = 500
        batches = 0
        while True:
            params = self._nd_params()
            params.update({
                'query': ' ',
                'songCount': str(batch_size),
                'songOffset': str(offset),
                'artistCount': '0',
                'albumCount': '0',
            })
            try:
                r = httpx.get(f"{self._nd_url}/rest/search3.view", params=params, timeout=30)
                r.raise_for_status()
                data = r.json()
            except Exception as e:
                log.error("Navidrome API error at offset %d: %s", offset, e)
                break
            songs = data.get("subsonic-response", {}).get("searchResult3", {}).get("song", [])
            if not songs:
                break
            for s in songs:
                genre = ""
                genres_list = s.get("genres", [])
                if genres_list:
                    genre = genres_list[0].get("name", "")
                if not genre:
                    genre = s.get("genre", "")
                tracks.append({
                    "name": s.get("title", "?"),
                    "artist": s.get("displayArtist") or s.get("artist", "?"),
                    "album": s.get("album", "?"),
                    "duration_ms": s.get("duration", 0) * 1000,
                    "year": str(s.get("year", "")),
                    "genre": genre.title() if genre else "",
                    "uri": f"navidrome:{s['id']}",
                    "image": f"nd:{s['coverArt']}" if s.get("coverArt") else "",
                    "source": "navidrome",
                    "source_name": "Navidrome",
                    "added_at": s.get("created", ""),
                    "platform": "navidrome",
                    "album_id": s.get("albumId", ""),
                })
            batches += 1
            offset += batch_size
            if len(songs) < batch_size:
                break
        self._save_json(tracks, "navidrome_library.json")
        log.info("Navidrome: fetched %d songs in %d batches", len(tracks), batches)
        # Write to DB
        db = self._get_db()
        db.execute("DELETE FROM tracks WHERE source='navidrome'")
        for t in tracks:
            t.setdefault("name_lower", t.get("name", "").lower())
            t.setdefault("artist_lower", t.get("artist", "").lower())
            db.execute(self._INSERT_TRACK, t)
        db.commit()
        self._update_cross_platform_dedup(db)
        self._rebuild_sidebar_cache(db)
        db.close()

    # -- schedule filtering --
    def _active_schedules(self) -> dict:
        """Return only schedules for configured integrations."""
        active = {}
        for cat, interval in SCHEDULES.items():
            if cat == "navidrome":
                if self._nd_url:
                    active[cat] = interval
            else:
                if self._spotify_configured:
                    active[cat] = interval
        return active

    # -- main loop --
    def run_category(self, category: str):
        """Run a single category export. Returns True on success."""
        if category != "navidrome" and not self._spotify_configured:
            return False
        exporters = {
            "saved_tracks": self.export_saved_tracks,
            "playlists": self.export_playlists,
            "saved_albums": self.export_saved_albums,
            "followed_artists": self.export_followed_artists,
            "top_tracks": self.export_top_tracks,
            "top_artists": self.export_top_artists,
            "recently_played": self.export_recently_played,
            "saved_shows": self.export_saved_shows,
            "saved_episodes": self.export_saved_episodes,
            "artist_genres": self.export_artist_genres,
            "navidrome": self.export_navidrome,
        }
        fn = exporters.get(category)
        if not fn:
            return False
        if category != "navidrome" and time.time() < self._rate_limited_until:
            remaining = int(self._rate_limited_until - time.time())
            log.info("Skipping %s — Spotify rate-limited for %dm", category, remaining // 60)
            return False
        try:
            fn()
            self.state.mark_run(category)
            return True
        except SpotifyException as e:
            log_error(self.export_dir, category, str(e), "auth" if e.http_status in (401, 403) else "error")
            if e.http_status in (401, 403):
                log.error("Auth error exporting %s: %s", category, e)
                notify(
                    "ByeByeSpotify Auth Error",
                    f"Category '{category}' failed with auth error: {e}\nToken may need refresh.",
                    priority=8,
                )
            else:
                log.error("Spotify error exporting %s: %s", category, e)
                notify(
                    "ByeByeSpotify Export Error",
                    f"Category '{category}' failed: {e}",
                    priority=5,
                )
            return False
        except Exception as e:
            log_error(self.export_dir, category, str(e))
            log.error("Unexpected error exporting %s: %s", category, e, exc_info=True)
            notify(
                "ByeByeSpotify Error",
                f"Category '{category}' failed unexpectedly: {e}",
                priority=5,
            )
            return False

    def run_loop(self):
        log.info("ByeByeSpotify starting up")
        log.info("Export directory: %s", self.export_dir)

        active = self._active_schedules()
        log.info("Active schedules: %s", {k: f"{v}h" for k, v in active.items()} if active else "none")

        if self._spotify_configured:
            # Verify auth works
            try:
                user = self._call(self.sp.current_user)
                log.info("Authenticated as: %s (%s)", user["display_name"], user["id"])
            except Exception as e:
                log.error("Authentication failed: %s", e)
                notify("ByeByeSpotify Auth Failed", f"Could not authenticate: {e}", priority=8)
                raise

            # Export profile once at startup
            try:
                profile = self._call(self.sp.current_user)
                self._save_json(profile, "profile.json")
            except Exception:
                pass
        else:
            log.info("Spotify not configured — running in web-only mode")

        # Start web dashboard in background thread
        self._start_web()

        if not active:
            log.info("No export schedules active, serving web UI only")

        while True:
            for category, interval in active.items():
                if self.state.should_run(category, interval):
                    self.run_category(category)
                    time.sleep(2)  # small pause between categories

            time.sleep(LOOP_SLEEP_SECONDS)


    def _start_web(self):
        """Start the web dashboard in a daemon thread."""
        def _run():
            import uvicorn
            from web import app
            uvicorn.run(app, host="0.0.0.0", port=WEB_PORT, log_level="warning", access_log=True)
        t = threading.Thread(target=_run, daemon=True)
        t.start()
        log.info("Web dashboard started on :%d", WEB_PORT)


def main():
    try:
        exporter = SpotifyExporter()
        exporter.run_loop()
    except KeyboardInterrupt:
        log.info("Shutting down")
    except Exception as e:
        log.error("Fatal error: %s", e)
        notify("ByeByeSpotify Fatal", f"Service crashed: {e}", priority=8)
        raise


if __name__ == "__main__":
    main()
