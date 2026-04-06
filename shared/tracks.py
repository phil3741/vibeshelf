"""Track normalisation and genre-map building shared by exporter and web."""

import json
from pathlib import Path


def build_genre_map(export_dir: Path) -> dict:
    """Build {artist_name_lower: [genres]} from artist_genres.json (primary) + fallback sources."""
    gmap = {}
    ag_path = export_dir / "artist_genres.json"
    if ag_path.exists():
        try:
            data = json.loads(ag_path.read_text())
            if isinstance(data, dict):
                for info in data.values():
                    if isinstance(info, dict) and info.get("genres") and info.get("name"):
                        gmap[info["name"].lower()] = info["genres"]
        except (json.JSONDecodeError, OSError):
            pass
    if not gmap:
        for fname in ("followed_artists.json", "top_artists.json"):
            path = export_dir / fname
            if not path.exists():
                continue
            try:
                data = json.loads(path.read_text())
            except (json.JSONDecodeError, OSError):
                continue
            if fname == "top_artists.json" and isinstance(data, dict):
                for items in data.values():
                    if isinstance(items, list):
                        for a in items:
                            if a.get("genres"):
                                gmap[a["name"].lower()] = a["genres"]
            elif isinstance(data, list):
                for a in data:
                    if a.get("genres"):
                        gmap[a["name"].lower()] = a["genres"]
    return gmap


def normalize_track(item: dict, source: str, source_name: str, genre_map: dict) -> dict | None:
    """Normalize a Spotify API track item to a standard track dict.

    Returns None for episodes or invalid items.
    """
    track = item.get("track") or item.get("item")
    if not track or not isinstance(track, dict):
        return None
    if track.get("type") == "episode" or track.get("episode"):
        return None
    artists = track.get("artists", [])
    artist_str = ", ".join(a.get("name", "") for a in artists)
    album = track.get("album", {})
    genre = ""
    if artists:
        key = artists[0].get("name", "").lower()
        genres = genre_map.get(key, [])
        if genres:
            genre = genres[0].title()
    images = album.get("images") or []
    img = ""
    if images:
        medium = [i for i in images if 200 <= (i.get("width") or 0) <= 400]
        img = medium[0]["url"] if medium else images[0]["url"]
    year = (album.get("release_date") or "")[:4]
    return {
        "name": track.get("name", "?"),
        "artist": artist_str,
        "album": album.get("name", "?"),
        "duration_ms": track.get("duration_ms", 0),
        "year": year,
        "genre": genre,
        "uri": track.get("uri", ""),
        "image": img,
        "source": source,
        "source_name": source_name,
        "added_at": item.get("added_at", ""),
        "platform": "spotify",
    }
