"""Database schema, connection factory, and initialisation for library.db."""

import sqlite3
from pathlib import Path

DB_SCHEMA = """
CREATE TABLE IF NOT EXISTS tracks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    artist TEXT NOT NULL DEFAULT '',
    album TEXT NOT NULL DEFAULT '',
    duration_ms INTEGER NOT NULL DEFAULT 0,
    year TEXT NOT NULL DEFAULT '',
    genre TEXT NOT NULL DEFAULT '',
    uri TEXT NOT NULL DEFAULT '',
    image TEXT NOT NULL DEFAULT '',
    source TEXT NOT NULL,
    source_name TEXT NOT NULL,
    added_at TEXT NOT NULL DEFAULT '',
    platform TEXT NOT NULL DEFAULT 'spotify',
    album_id TEXT NOT NULL DEFAULT '',
    name_lower TEXT NOT NULL DEFAULT '',
    artist_lower TEXT NOT NULL DEFAULT '',
    UNIQUE(uri, source, source_name)
);
CREATE INDEX IF NOT EXISTS idx_tracks_source ON tracks(source);
CREATE INDEX IF NOT EXISTS idx_tracks_platform ON tracks(platform);
CREATE INDEX IF NOT EXISTS idx_tracks_name_artist ON tracks(name_lower, artist_lower);
CREATE VIRTUAL TABLE IF NOT EXISTS tracks_fts USING fts5(
    name, artist, album, genre,
    content='tracks', content_rowid='id',
    tokenize='unicode61 remove_diacritics 2'
);
CREATE TABLE IF NOT EXISTS sidebar_cache (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS playlists (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    owner TEXT NOT NULL DEFAULT '',
    total_tracks INTEGER NOT NULL DEFAULT 0
);
"""

DB_TRIGGERS = """
CREATE TRIGGER IF NOT EXISTS tracks_ai AFTER INSERT ON tracks BEGIN
    INSERT INTO tracks_fts(rowid, name, artist, album, genre) VALUES (new.id, new.name, new.artist, new.album, new.genre);
END;
CREATE TRIGGER IF NOT EXISTS tracks_ad AFTER DELETE ON tracks BEGIN
    INSERT INTO tracks_fts(tracks_fts, rowid, name, artist, album, genre) VALUES ('delete', old.id, old.name, old.artist, old.album, old.genre);
END;
CREATE TRIGGER IF NOT EXISTS tracks_au AFTER UPDATE ON tracks BEGIN
    INSERT INTO tracks_fts(tracks_fts, rowid, name, artist, album, genre) VALUES ('delete', old.id, old.name, old.artist, old.album, old.genre);
    INSERT INTO tracks_fts(rowid, name, artist, album, genre) VALUES (new.id, new.name, new.artist, new.album, new.genre);
END;
"""


def get_db(db_path: Path, *, timeout: int = 10) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path), timeout=timeout)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    return conn


def init_db(db_path: Path):
    db = get_db(db_path)
    db.executescript(DB_SCHEMA)
    db.executescript(DB_TRIGGERS)
    db.commit()
    db.close()
