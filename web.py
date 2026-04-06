"""ByeByeSpotify Web Dashboard - FastAPI backend + inline HTML frontend."""

import hashlib
import json
import os
import sqlite3
import time
from pathlib import Path
from datetime import datetime, timezone

import httpx
from fastapi import FastAPI, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response, StreamingResponse
from fastapi.middleware.gzip import GZipMiddleware
from shared.db import get_db as _shared_get_db
from shared.navidrome import nd_config_from_env, nd_cover_url, nd_stream_url, nd_scrobble_url
from shared.tracks import normalize_track as _normalize_track, build_genre_map as _build_genre_map_shared
from shared.spotify_auth import get_fresh_token, SCOPES_WEB

EXPORT_DIR = Path("/app/exports")
ART_CACHE_DIR = Path("/app/exports/art_cache")
ART_CACHE_DIR.mkdir(exist_ok=True)
DB_PATH = EXPORT_DIR / "library.db"

# ── Integration detection ──
_SPOTIFY_CONFIGURED = bool(os.getenv("SPOTIFY_CLIENT_ID") and os.getenv("SPOTIFY_CLIENT_SECRET"))
_NAVIDROME_CONFIGURED = bool(os.getenv("NAVIDROME_URL") and os.getenv("NAVIDROME_USER") and os.getenv("NAVIDROME_PASSWORD"))
_KOITO_CONFIGURED = bool(os.getenv("KOITO_DSN"))

# ── Koito (scrobble history) ──
KOITO_DSN = os.environ.get("KOITO_DSN", "")
KOITO_USER_ID = int(os.environ.get("KOITO_USER_ID", "1"))

if _KOITO_CONFIGURED:
    import psycopg2
    import psycopg2.extras

# ── Response cache (TTL-based, invalidated when DB changes) ──
_cache: dict[str, tuple[float, bytes]] = {}
_CACHE_TTL = 300  # 5 minutes


def _cache_get(key: str) -> bytes | None:
    entry = _cache.get(key)
    if entry and (time.time() - entry[0]) < _CACHE_TTL:
        return entry[1]
    return None


def _cache_set(key: str, data: bytes):
    _cache[key] = (time.time(), data)


def _get_db() -> sqlite3.Connection:
    return _shared_get_db(DB_PATH, timeout=5)


def _db_available() -> bool:
    return DB_PATH.exists()


_KOITO_QUERY = """
SELECT
  l.listened_at,
  ta.alias AS track_name,
  t.duration,
  ra.alias AS album_name,
  string_agg(DISTINCT aa.alias, ', ') AS artists
FROM listens l
JOIN tracks t ON t.id = l.track_id
JOIN track_aliases ta ON ta.track_id = t.id AND ta.is_primary = true
LEFT JOIN releases r ON r.id = t.release_id
LEFT JOIN release_aliases ra ON ra.release_id = r.id AND ra.is_primary = true
LEFT JOIN artist_tracks at2 ON at2.track_id = t.id
LEFT JOIN artists ar ON ar.id = at2.artist_id
LEFT JOIN artist_aliases aa ON aa.artist_id = ar.id AND aa.is_primary = true
WHERE l.user_id = %s
GROUP BY l.listened_at, t.id, ta.alias, t.duration, ra.alias
ORDER BY l.listened_at DESC
LIMIT %s
"""


def _fetch_koito_listens(limit: int = 500) -> list[dict]:
    cached = _cache_get("koito_listens")
    if cached is not None:
        return json.loads(cached)
    try:
        conn = psycopg2.connect(KOITO_DSN)
        cur = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
        cur.execute(_KOITO_QUERY, (KOITO_USER_ID, limit))
        rows = cur.fetchall()
        cur.close()
        conn.close()
    except Exception:
        return []
    tracks = []
    for r in rows:
        tracks.append({
            "name": r["track_name"] or "",
            "artist": r["artists"] or "",
            "album": r["album_name"] or "",
            "duration_ms": (r["duration"] or 0) * 1000,
            "year": "",
            "genre": "",
            "uri": f"koito:{int(r['listened_at'].timestamp())}",
            "image": "",
            "source": "recent",
            "source_name": "Listening History",
            "added_at": r["listened_at"].isoformat(),
            "platform": "koito",
        })
    _cache_set("koito_listens", json.dumps(tracks).encode())
    return tracks


def _koito_count() -> int:
    cached = _cache_get("koito_count")
    if cached is not None:
        return int(cached)
    try:
        conn = psycopg2.connect(KOITO_DSN)
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM listens WHERE user_id = %s", (KOITO_USER_ID,))
        count = cur.fetchone()[0]
        cur.close()
        conn.close()
    except Exception:
        return 0
    _cache_set("koito_count", str(count).encode())
    return count

SCHEDULES = {
    "recently_played": 2,
    "saved_tracks": 6,
    "playlists": 6,
    "saved_albums": 24,
    "followed_artists": 24,
    "saved_shows": 24,
    "saved_episodes": 24,
    "artist_genres": 24,
    "top_tracks": 168,
    "top_artists": 168,
    "navidrome": 6,
}

CATEGORY_LABELS = {
    "recently_played": "Recently Played",
    "saved_tracks": "Liked Songs",
    "playlists": "Playlists",
    "saved_albums": "Saved Albums",
    "followed_artists": "Followed Artists",
    "saved_shows": "Podcasts",
    "saved_episodes": "Podcast Episodes",
    "artist_genres": "Artist Genres",
    "top_tracks": "Top Tracks",
    "top_artists": "Top Artists",
    "navidrome": "Navidrome Library",
}


def _build_genre_map(export_dir: Path) -> dict:
    return _build_genre_map_shared(export_dir)


def _extract_track(item: dict, source: str, source_name: str, genre_map: dict) -> dict | None:
    return _normalize_track(item, source, source_name, genre_map)

app = FastAPI(title="ByeByeSpotify")
app.add_middleware(GZipMiddleware, minimum_size=1000)


def _read_json(filename: str):
    path = EXPORT_DIR / filename
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return None


def _read_state() -> dict:
    return _read_json("state.json") or {}


def _load_navidrome_tracks() -> list:
    """Load Navidrome tracks from exported JSON."""
    data = _read_json("navidrome_library.json")
    return data if isinstance(data, list) else []


# ── API Endpoints ──────────────────────────────────────────────


def _is_schedule_active(cat: str) -> bool:
    if cat == "navidrome":
        return _NAVIDROME_CONFIGURED
    return _SPOTIFY_CONFIGURED


@app.get("/api/status")
def api_status():
    state = _read_state()
    last_runs = state.get("last_run", {})
    now = time.time()
    categories = []
    for cat, interval_h in SCHEDULES.items():
        if not _is_schedule_active(cat):
            continue
        last = last_runs.get(cat, 0)
        next_run = last + interval_h * 3600
        elapsed = now - last if last else None
        categories.append({
            "category": cat,
            "label": CATEGORY_LABELS.get(cat, cat),
            "interval_hours": interval_h,
            "last_run": last,
            "last_run_iso": datetime.fromtimestamp(last, tz=timezone.utc).isoformat() if last else None,
            "next_run": next_run,
            "seconds_until_next": max(0, next_run - now),
            "overdue": now > next_run,
        })
    return {"categories": categories, "now": now}


@app.get("/api/summary")
def api_summary():
    result = {}
    for cat in ("saved_tracks", "followed_artists", "saved_albums", "saved_shows", "saved_episodes"):
        data = _read_json(f"{cat}.json")
        result[cat] = len(data) if isinstance(data, list) else 0

    playlists = _read_json("playlists.json")
    if isinstance(playlists, list):
        own = [p for p in playlists if p.get("owner_id") == (_read_json("profile.json") or {}).get("id")]
        result["playlists_total"] = len(playlists)
        result["playlists_own"] = len(own)
    else:
        result["playlists_total"] = 0
        result["playlists_own"] = 0

    profile = _read_json("profile.json")
    result["user"] = profile.get("display_name", "Unknown") if profile else "Unknown"

    return result


@app.get("/api/errors")
def api_errors():
    data = _read_json("errors.json")
    return data if isinstance(data, list) else []


@app.get("/api/top/{kind}")
def api_top(kind: str, time_range: str = Query("short_term")):
    if kind not in ("tracks", "artists"):
        return JSONResponse({"error": "kind must be 'tracks' or 'artists'"}, 400)
    if time_range not in ("short_term", "medium_term", "long_term"):
        return JSONResponse({"error": "invalid time_range"}, 400)
    data = _read_json(f"top_{kind}.json")
    if not data:
        return []
    return data.get(time_range, [])[:20]


@app.get("/api/recently-played")
def api_recently_played():
    if _KOITO_CONFIGURED:
        tracks = _fetch_koito_listens()
        if tracks:
            return {"items": tracks, "total": len(tracks)}
    # Fallback: Spotify's exported recently_played.json
    data = _read_json("recently_played.json")
    if isinstance(data, list):
        genre_map = _build_genre_map(EXPORT_DIR)
        tracks = []
        for item in data:
            t = _extract_track(item, "recent", "Recently Played", genre_map)
            if t:
                t["added_at"] = item.get("played_at", "")
                tracks.append(t)
        return {"items": tracks, "total": len(tracks)}
    return {"items": [], "total": 0}


@app.get("/api/playlists")
def api_playlists():
    data = _read_json("playlists.json")
    if not isinstance(data, list):
        return []
    return [{
        "id": p.get("id"),
        "name": p.get("name"),
        "owner": p.get("owner"),
        "owner_id": p.get("owner_id"),
        "total_tracks": p.get("total_tracks", 0),
        "tracks_backed_up": len(p.get("tracks", [])),
        "public": p.get("public"),
        "description": p.get("description", ""),
    } for p in data]


@app.get("/api/playlists/{playlist_id}")
def api_playlist_detail(playlist_id: str):
    data = _read_json("playlists.json")
    if not isinstance(data, list):
        return JSONResponse({"error": "not found"}, 404)
    for p in data:
        if p.get("id") == playlist_id:
            return p
    return JSONResponse({"error": "not found"}, 404)


@app.get("/api/albums")
def api_albums():
    data = _read_json("saved_albums.json")
    if not isinstance(data, list):
        return []
    return [{
        "name": item.get("album", {}).get("name", "?"),
        "artists": ", ".join(a["name"] for a in item.get("album", {}).get("artists", [])),
        "release_date": item.get("album", {}).get("release_date", ""),
        "total_tracks": item.get("album", {}).get("total_tracks", 0),
        "image": (item.get("album", {}).get("images") or [{}])[-1].get("url", ""),
        "uri": item.get("album", {}).get("uri", ""),
        "added_at": item.get("added_at", ""),
    } for item in data]


@app.get("/api/artists")
def api_artists():
    data = _read_json("followed_artists.json")
    if not isinstance(data, list):
        return []
    return [{
        "name": a.get("name", "?"),
        "genres": a.get("genres", [])[:3],
        "image": (a.get("images") or [{}])[-1].get("url", ""),
        "uri": a.get("uri", ""),
        "followers": a.get("followers", {}).get("total", 0),
    } for a in data]


@app.get("/api/shows")
def api_shows():
    data = _read_json("saved_shows.json")
    if not isinstance(data, list):
        return []
    return [{
        "name": item.get("show", {}).get("name", "?"),
        "description": item.get("show", {}).get("description", "")[:150],
        "total_episodes": item.get("show", {}).get("total_episodes", 0),
        "image": (item.get("show", {}).get("images") or [{}])[-1].get("url", ""),
        "uri": item.get("show", {}).get("uri", ""),
        "added_at": item.get("added_at", ""),
    } for item in data]


@app.get("/api/episodes")
def api_episodes(limit: int = Query(50, le=200), offset: int = Query(0, ge=0)):
    data = _read_json("saved_episodes.json")
    if not isinstance(data, list):
        return {"items": [], "total": 0}
    items = [{
        "name": item.get("episode", {}).get("name", "?"),
        "show": item.get("episode", {}).get("show", {}).get("name", "?"),
        "duration_ms": item.get("episode", {}).get("duration_ms", 0),
        "release_date": item.get("episode", {}).get("release_date", ""),
        "image": (item.get("episode", {}).get("images") or [{}])[-1].get("url", ""),
        "uri": item.get("episode", {}).get("uri", ""),
        "added_at": item.get("added_at", ""),
    } for item in data]
    return {"items": items[offset:offset + limit], "total": len(items)}


@app.get("/api/library")
def api_library(source: str = Query("saved"), artist: str = Query(""), album: str = Query(""), genre: str = Query(""), q: str = Query("")):
    if source == "recent":
        if _KOITO_CONFIGURED:
            tracks = _fetch_koito_listens()
            if tracks:
                if q:
                    ql = q.lower()
                    tracks = [t for t in tracks if ql in t["name"].lower() or ql in t["artist"].lower() or ql in t["album"].lower()]
                return {"tracks": tracks}
        # Fallback to DB or JSON (reads recently_played.json)
        if _db_available():
            return _api_library_db("recent", artist, album, genre, q)
        return _api_library_json("recent", artist, album, genre, q)
    if _db_available():
        return _api_library_db(source, artist, album, genre, q)
    return _api_library_json(source, artist, album, genre, q)


def _api_library_db(source: str, artist: str, album: str, genre: str, q: str):
    cache_key = f"lib:{source}:{artist}:{album}:{genre}:{q}"
    cached = _cache_get(cache_key)
    if cached is not None:
        return Response(content=cached, media_type="application/json",
                        headers={"Cache-Control": "public, max-age=120"})

    db = _get_db()
    _COLS = "id, name, artist, album, duration_ms, year, genre, uri, image, source, source_name, added_at, platform, album_id"

    if source == "all":
        # Dedup: 1) group by URI (saved+playlist dupes), 2) skip navidrome dupes (platform='both')
        sql = f"""SELECT {_COLS} FROM tracks WHERE source IN ('saved', 'playlist', 'navidrome')
            AND NOT (source = 'navidrome' AND platform = 'both')
            AND id IN (
                SELECT MIN(id) FROM tracks WHERE source IN ('saved', 'playlist', 'navidrome')
                AND NOT (source = 'navidrome' AND platform = 'both')
                GROUP BY uri
            )"""
        base_params = []
    elif source == "saved":
        sql = f"SELECT {_COLS} FROM tracks WHERE source='saved'"
        base_params = []
    elif source == "navidrome":
        sql = f"SELECT {_COLS} FROM tracks WHERE source='navidrome'"
        base_params = []
    elif source == "recent":
        sql = f"SELECT {_COLS} FROM tracks WHERE source='recent'"
        base_params = []
    elif source.startswith("top_"):
        time_range = source.replace("top_", "") or "short_term"
        sql = f"SELECT {_COLS} FROM tracks WHERE source='top' AND source_name=?"
        base_params = [time_range]
    elif source.startswith("playlist:"):
        playlist_id = source.split(":", 1)[1]
        sql = f"SELECT {_COLS} FROM tracks WHERE source='playlist' AND source_name=(SELECT name FROM playlists WHERE id=?)"
        base_params = [playlist_id]
    else:
        sql = f"SELECT {_COLS} FROM tracks WHERE source=?"
        base_params = [source]

    # Build filter clauses
    filters = []
    params = list(base_params)
    if artist:
        filters.append("artist LIKE ? COLLATE NOCASE")
        params.append(f"%{artist}%")
    if album:
        filters.append("album = ? COLLATE NOCASE")
        params.append(album)
    if genre:
        filters.append("genre = ? COLLATE NOCASE")
        params.append(genre)
    if q:
        # FTS5 search with prefix matching
        fts_terms = " ".join(f'"{term}"*' for term in q.split() if term.strip())
        if fts_terms:
            filters.append("id IN (SELECT rowid FROM tracks_fts WHERE tracks_fts MATCH ?)")
            params.append(fts_terms)

    if filters:
        filter_sql = " AND ".join(filters)
        if source == "all":
            # Wrap the dedup query and apply filters on the outer SELECT
            sql = f"SELECT * FROM ({sql}) WHERE {filter_sql}"
        else:
            sql += " AND " + filter_sql

    try:
        rows = db.execute(sql, params).fetchall()
        tracks = [dict(r) for r in rows]
    except Exception as e:
        import logging
        logging.getLogger("byebyespotify").error("DB query failed, falling back to JSON: %s", e, exc_info=True)
        db.close()
        return _api_library_json(source, artist, album, genre, q)
    db.close()
    payload = json.dumps({"tracks": tracks, "total": len(tracks)}, ensure_ascii=False).encode()
    _cache_set(cache_key, payload)
    return Response(content=payload, media_type="application/json",
                    headers={"Cache-Control": "public, max-age=120"})


def _api_library_json(source: str, artist: str, album: str, genre: str, q: str):
    """Fallback: load from JSON files (original implementation)."""
    genre_map = _build_genre_map(EXPORT_DIR)
    tracks = []
    seen_uris = set()

    if source == "saved" or source == "all":
        data = _read_json("saved_tracks.json")
        if isinstance(data, list):
            for item in data:
                t = _extract_track(item, "saved", "Library", genre_map)
                if t and t["uri"] not in seen_uris:
                    seen_uris.add(t["uri"])
                    tracks.append(t)

    if source == "all":
        playlists = _read_json("playlists.json")
        if isinstance(playlists, list):
            for pl in playlists:
                for item in pl.get("tracks", []):
                    t = _extract_track(item, "playlist", pl.get("name", "?"), genre_map)
                    if t and t["uri"] not in seen_uris:
                        seen_uris.add(t["uri"])
                        tracks.append(t)
        nd_tracks = _load_navidrome_tracks()
        seen_cross = {(t["name"].lower(), t["artist"].lower()): i for i, t in enumerate(tracks)}
        for t in nd_tracks:
            key = (t["name"].lower(), t["artist"].lower())
            if key in seen_cross:
                tracks[seen_cross[key]]["platform"] = "both"
            else:
                seen_cross[key] = len(tracks)
                tracks.append(t)

    if source == "navidrome":
        tracks = _load_navidrome_tracks()

    if source == "recent":
        data = _read_json("recently_played.json")
        if isinstance(data, list):
            for item in data:
                t = _extract_track(item, "recent", "Recently Played", genre_map)
                if t:
                    t["added_at"] = item.get("played_at", "")
                    tracks.append(t)

    if source.startswith("top_"):
        time_range = source.replace("top_", "") or "short_term"
        data = _read_json("top_tracks.json")
        if isinstance(data, dict):
            for item in data.get(time_range, []):
                t = _extract_track({"track": item}, "top", "Top Tracks", genre_map)
                if t:
                    tracks.append(t)

    if source.startswith("playlist:"):
        playlist_id = source.split(":", 1)[1]
        playlists = _read_json("playlists.json")
        if isinstance(playlists, list):
            for pl in playlists:
                if pl.get("id") == playlist_id:
                    for item in pl.get("tracks", []):
                        t = _extract_track(item, "playlist", pl.get("name", "?"), genre_map)
                        if t:
                            tracks.append(t)
                    break

    if artist:
        tracks = [t for t in tracks if artist.lower() in t["artist"].lower()]
    if album:
        tracks = [t for t in tracks if t["album"].lower() == album.lower()]
    if genre:
        tracks = [t for t in tracks if t["genre"].lower() == genre.lower()]
    if q:
        ql = q.lower()
        tracks = [t for t in tracks if ql in t["name"].lower() or ql in t["artist"].lower() or ql in t["album"].lower()]

    return {"tracks": tracks, "total": len(tracks)}


@app.get("/api/sidebar")
def api_sidebar():
    if _db_available():
        return _api_sidebar_db()
    return _api_sidebar_json()


def _api_sidebar_db():
    """Read pre-computed sidebar data from SQLite."""
    cached = _cache_get("sidebar")
    if cached is not None:
        return Response(content=cached, media_type="application/json",
                        headers={"Cache-Control": "public, max-age=120"})

    db = _get_db()
    cache = {}
    try:
        for row in db.execute("SELECT key, value FROM sidebar_cache"):
            cache[row["key"]] = json.loads(row["value"])
    except Exception:
        db.close()
        return _api_sidebar_json()
    db.close()

    if not cache:
        return _api_sidebar_json()

    profile = _read_json("profile.json")
    user = profile.get("display_name", "Unknown") if profile else "Unknown"

    result = {
        "user": user,
        "saved_count": cache.get("saved_count", 0),
        "recent_count": _koito_count(),
        "navidrome_count": cache.get("navidrome_count", 0),
        "all_count": cache.get("all_count", 0),
        "playlists": cache.get("playlists", []),
        "artists": cache.get("artists", []),
        "albums": cache.get("albums", []),
        "genres": cache.get("genres", []),
    }
    payload = json.dumps(result, ensure_ascii=False).encode()
    _cache_set("sidebar", payload)
    return Response(content=payload, media_type="application/json",
                    headers={"Cache-Control": "public, max-age=120"})


def _api_sidebar_json():
    """Fallback: load sidebar data from JSON files."""
    genre_map = _build_genre_map(EXPORT_DIR)
    profile = _read_json("profile.json")
    user = profile.get("display_name", "Unknown") if profile else "Unknown"

    saved = _read_json("saved_tracks.json")
    saved_count = len(saved) if isinstance(saved, list) else 0

    playlists = _read_json("playlists.json")
    playlist_list = []
    if isinstance(playlists, list):
        for p in playlists:
            playlist_list.append({
                "id": p.get("id"),
                "name": p.get("name"),
                "count": p.get("total_tracks", 0),
            })

    artists_set = set()
    albums_set = set()
    if isinstance(saved, list):
        for item in saved:
            track = item.get("track", {})
            for a in track.get("artists", []):
                if a.get("name"):
                    artists_set.add(a["name"])
            alb = track.get("album", {}).get("name")
            if alb:
                albums_set.add(alb)

    genres_set = set()
    for genres in genre_map.values():
        for g in genres:
            genres_set.add(g.title())

    nd_tracks = _load_navidrome_tracks()
    navidrome_count = len(nd_tracks)
    for t in nd_tracks:
        if t.get("artist"):
            artists_set.add(t["artist"])
        if t.get("album"):
            albums_set.add(t["album"])
        if t.get("genre"):
            genres_set.add(t["genre"])

    recent_count = _koito_count()

    # Compute deduped all-music count
    all_count = saved_count + navidrome_count
    if isinstance(saved, list) and nd_tracks:
        sp_keys = set()
        for item in saved:
            track = item.get("track") or item.get("item") or {}
            name = (track.get("name") or "").lower()
            artist_parts = ", ".join(a.get("name", "") for a in track.get("artists", []))
            sp_keys.add((name, artist_parts.lower()))
        for t in nd_tracks:
            nd_key = ((t.get("name") or "").lower(), (t.get("artist") or "").replace(" \u2022 ", ", ").lower())
            if nd_key in sp_keys:
                all_count -= 1

    return {
        "user": user,
        "saved_count": saved_count,
        "recent_count": recent_count,
        "navidrome_count": navidrome_count,
        "all_count": all_count,
        "playlists": playlist_list,
        "artists": sorted(artists_set),
        "albums": sorted(albums_set),
        "genres": sorted(genres_set),
    }


# ── Dashboard HTML ─────────────────────────────────────────────


@app.get("/api/art")
async def api_art(url: str = Query("")):
    """Proxy and cache album art from Spotify CDN or Navidrome."""
    if not url:
        return Response(status_code=400)
    is_nd = url.startswith("nd:")
    is_spotify = url.startswith("https://i.scdn.co/")
    if not is_nd and not is_spotify:
        return Response(status_code=400)
    h = hashlib.md5(url.encode()).hexdigest()
    cached = ART_CACHE_DIR / h
    if cached.exists():
        return Response(content=cached.read_bytes(), media_type="image/jpeg",
                        headers={"Cache-Control": "public, max-age=604800"})
    try:
        if is_nd:
            nd_url, nd_user, nd_pw = nd_config_from_env()
            if not nd_url:
                return Response(status_code=404)
            cover_id = url[3:]
            fetch_url = nd_cover_url(nd_url, nd_user, nd_pw, cover_id)
        else:
            fetch_url = url
        async with httpx.AsyncClient() as client:
            r = await client.get(fetch_url, timeout=10, follow_redirects=True)
        if r.status_code == 200:
            cached.write_bytes(r.content)
            return Response(content=r.content, media_type=r.headers.get("content-type", "image/jpeg"),
                            headers={"Cache-Control": "public, max-age=604800"})
    except Exception:
        pass
    return Response(status_code=502)


_stream_cache: dict[str, tuple[float, bytes]] = {}
_STREAM_CACHE_TTL = 600  # 10 min


def _nd_stream_url_local(id: str) -> str:
    nd_url, nd_user, nd_pw = nd_config_from_env()
    return nd_stream_url(nd_url, nd_user, nd_pw, id)


def _serve_range(data: bytes, range_hdr: str) -> Response:
    total = len(data)
    range_spec = range_hdr.replace("bytes=", "")
    parts = range_spec.split("-")
    start = int(parts[0]) if parts[0] else 0
    end = int(parts[1]) if parts[1] else total - 1
    end = min(end, total - 1)
    return Response(
        content=data[start:end + 1], status_code=206,
        headers={
            "Content-Type": "audio/mpeg",
            "Content-Length": str(end - start + 1),
            "Content-Range": f"bytes {start}-{end}/{total}",
            "Accept-Ranges": "bytes",
        },
    )


@app.get("/api/stream")
async def api_stream(request: Request, id: str = Query("")):
    """Proxy audio stream from Navidrome via Subsonic API."""
    if not id or not os.environ.get("NAVIDROME_URL"):
        return Response(status_code=400)

    range_hdr = request.headers.get("range")

    # Serve from cache if available (instant)
    cached = _stream_cache.get(id)
    if cached and (time.time() - cached[0]) < _STREAM_CACHE_TTL:
        data = cached[1]
        if range_hdr:
            return _serve_range(data, range_hdr)
        return Response(content=data, status_code=200, headers={
            "Content-Type": "audio/mpeg", "Content-Length": str(len(data)), "Accept-Ranges": "bytes",
        })

    # Not cached + range request: fetch full file, cache, then serve range
    if range_hdr:
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.get(_nd_stream_url_local(id), follow_redirects=True)
        except Exception:
            return Response(status_code=502)
        data = resp.content
        if data:
            _stream_cache[id] = (time.time(), data)
        return _serve_range(data, range_hdr)

    # Not cached + no range: stream through to browser immediately, cache in background
    try:
        client = httpx.AsyncClient()
        resp = await client.send(
            client.build_request("GET", _nd_stream_url_local(id)),
            stream=True,
        )
    except Exception:
        return Response(status_code=502)

    content_length = resp.headers.get("content-length")
    resp_headers = {
        "Content-Type": resp.headers.get("content-type", "audio/mpeg"),
        "Accept-Ranges": "bytes",
    }
    if content_length:
        resp_headers["Content-Length"] = content_length

    chunks: list[bytes] = []

    async def stream_and_cache():
        try:
            async for chunk in resp.aiter_bytes(chunk_size=65536):
                chunks.append(chunk)
                yield chunk
        finally:
            await resp.aclose()
            await client.aclose()
            # Cache the complete file for subsequent range requests
            data = b"".join(chunks)
            if data:
                _stream_cache[id] = (time.time(), data)
                now = time.time()
                for k in list(_stream_cache):
                    if now - _stream_cache[k][0] > _STREAM_CACHE_TTL:
                        del _stream_cache[k]

    return StreamingResponse(stream_and_cache(), status_code=200, headers=resp_headers)


@app.get("/api/scrobble")
async def api_scrobble(id: str = Query("")):
    """Notify Navidrome of playback so multi-scrobbler picks it up."""
    if not id:
        return Response(status_code=400)
    nd_url, nd_user, nd_pw = nd_config_from_env()
    if not nd_url:
        return Response(status_code=404)
    base = nd_scrobble_url(nd_url, nd_user, nd_pw)
    try:
        async with httpx.AsyncClient() as client:
            await client.get(f"{base}&id={id}&submission=false")
    except Exception:
        pass
    return Response(status_code=204)


@app.get("/api/spotify-token")
def api_spotify_token():
    """Return a fresh Spotify access token for the Web Playback SDK."""
    if not _SPOTIFY_CONFIGURED:
        return JSONResponse({"error": "spotify_not_configured"}, 404)
    # Try web scopes (with streaming) first, fall back to whatever token is cached.
    # Spotipy rejects cached tokens whose scopes don't match the requested ones,
    # so we fall back to exporter scopes to avoid returning no_token when the
    # cached token simply hasn't been re-authorized with streaming yet.
    token_info = get_fresh_token(SCOPES_WEB) or get_fresh_token()
    if not token_info:
        return JSONResponse({"error": "no_token"}, 401)
    return {"access_token": token_info["access_token"]}


@app.get("/manifest.json")
def manifest():
    return JSONResponse({
        "name": "ByeByeSpotify",
        "short_name": "BBS",
        "start_url": "/",
        "display": "standalone",
        "background_color": "#808080",
        "theme_color": "#A8A8A8",
        "icons": [
            {"src": "/icon.svg", "sizes": "any", "type": "image/svg+xml"}
        ]
    })


@app.get("/icon.svg")
def icon():
    svg = '''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 128 128">
<rect width="128" height="128" rx="24" fill="#4A8BDA"/>
<text x="64" y="88" text-anchor="middle" font-size="72" font-family="sans-serif" fill="#fff">♫</text>
</svg>'''
    return HTMLResponse(content=svg, media_type="image/svg+xml")


@app.get("/sw.js")
def service_worker():
    sw = '''const CACHE='bbs-v1';
self.addEventListener('install',e=>self.skipWaiting());
self.addEventListener('activate',e=>e.waitUntil(clients.claim()));
self.addEventListener('fetch',e=>{
  if(e.request.url.includes('/api/'))return;
  e.respondWith(caches.match(e.request).then(r=>r||fetch(e.request).then(resp=>{
    if(resp.ok&&resp.type==='basic'){const c=resp.clone();caches.open(CACHE).then(cache=>cache.put(e.request,c));}
    return resp;
  })));
});'''
    return HTMLResponse(content=sw, media_type="application/javascript")


@app.get("/", response_class=HTMLResponse)
def dashboard():
    nd_ext = os.environ.get("NAVIDROME_EXTERNAL_URL", "")
    return _get_dashboard_html().replace("__ND_EXT_URL__", nd_ext)



_dashboard_html: str | None = None

def _get_dashboard_html() -> str:
    global _dashboard_html
    if _dashboard_html is None:
        _dashboard_html = Path(__file__).parent.joinpath("templates", "dashboard.html").read_text()
    return _dashboard_html

