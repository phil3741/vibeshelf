# vibeshelf

Listening like it's 2006. With 2026's Music Services.

A purely hand-prompted and heavily modified extension of [ByeByeSpotify](https://github.com/c-rw/ByeByeSpotify). The original idea of byebyespotify was to back up all metadata of your Spotify Account (Liked Songs, Playlists, Artists, ...) without the media files themselves.



Now what if we made this a regular backup...

and what if we made a nice web-UI to visualize and browse through your music..

...why not integrate you existing Navidrome instance while were at it? (with deduplication of similar songs from both sources)

...and add MusicBrainz integration to include proper genre and year metadata?

...and why not make it stream directly in the UI?

...and show your recently played songs from the self-hosted scrobbler you're already using?



Well this is exactly what we're trying to do here.

This project is deeply in alpha state, you wouldn't want to rely on this service but it is a working PoC and maybe inspires some more ideas for any other millennial-brained individuals who cannot comprehend todays algorithmic Feed-UIs.


All integrations are **optional** — configure only what you need via environment variables. The dashboard works even with zero integrations configured.

![Screenshot](screenshot.png)

## Integrations

| Integration | What it does | Required env vars |
|---|---|---|
| [Spotify](https://github.com/spotify) | Scheduled backup of liked songs, playlists, albums, artists, top tracks, recently played, podcasts. Streaming via Spotify Web Playback SDK requires **Spotify Premium**. | `SPOTIFY_CLIENT_ID`, `SPOTIFY_CLIENT_SECRET` |
| [Navidrome](https://github.com/navidrome/navidrome) | Browse and stream your self-hosted music library, cross-platform deduplication with Spotify | `NAVIDROME_URL`, `NAVIDROME_USER`, `NAVIDROME_PASSWORD` |
| [Koito](https://github.com/gabehf/Koito) | Scrobble/listening history from Koito's PostgreSQL database (falls back to Spotify recently played if unavailable) | `KOITO_DSN` |
| [MusicBrainz](https://github.com/metabrainz/musicbrainz-server) | Automatic genre/tag lookups for artists (free, no auth needed, runs daily) | — (always active) |

## Quick Start

```bash
# 1. Clone the repo
git clone https://github.com/phil3741/vibeshelf.git
cd vibeshelf

# 2. Create your config
cp .env.example .env
# Edit .env — uncomment and fill in the integrations you want

# 3. Run
docker compose up -d
```

The dashboard is available at `http://localhost:8080`.

## Configuration

All configuration is done through environment variables (`.env` file). The presence of a variable activates its integration — only set what you need.

### Environment Variables

```bash
# ── Spotify (optional) ──────────────────────────────
# Enables: scheduled backup of your Spotify library, Spotify Web Playback in the dashboard.
# Get credentials from: https://developer.spotify.com/dashboard

SPOTIFY_CLIENT_ID=           # OAuth Client ID from Spotify Developer Dashboard
SPOTIFY_CLIENT_SECRET=       # OAuth Client Secret from Spotify Developer Dashboard
SPOTIFY_REDIRECT_URI=http://127.0.0.1:8888  # OAuth callback — must be exactly this value, also set in the Spotify Dashboard
SPOTIFY_SP_DC=               # (advanced) Your sp_dc browser cookie. Required to back up external/followed
                             # playlists — the standard Spotify API cannot access playlists you don't own.
                             # To get it: open Spotify Web Player in your browser, open DevTools → Application
                             # → Cookies → https://open.spotify.com → copy the value of "sp_dc".
                             # This is a long-lived session cookie tied to your account.

# ── Navidrome (optional) ────────────────────────────
# Enables: browsing and streaming your self-hosted music library, scrobbling,
# and cross-platform deduplication (tracks in both Spotify and Navidrome are detected and merged).

NAVIDROME_URL=               # Subsonic API base URL, e.g. http://navidrome:4533
NAVIDROME_USER=              # Navidrome username
NAVIDROME_PASSWORD=          # Navidrome password (used for Subsonic token auth, not sent in plain text)
NAVIDROME_EXTERNAL_URL=      # (optional) Public URL for the dashboard to link out to Navidrome, e.g. https://music.example.com

# ── Koito (optional) ────────────────────────────────
# Enables: full listening/scrobble history in the "Recently Played" view.
# If not configured, falls back to Spotify's last 50 recently played tracks.

KOITO_DSN=                   # PostgreSQL connection string, e.g. postgresql://user:pass@host:5432/koitodb
KOITO_USER_ID=1              # User ID in Koito's database (default: 1)

# ── Notifications (optional) ────────────────────────
# Push notifications on export errors via Gotify.
# Also requires a Gotify app token mounted at /run/secrets/gotify.

GOTIFY_URL=                  # Gotify server URL, e.g. https://gotify.example.com

# ── General ─────────────────────────────────────────

WEB_PORT=8080                # Port the web dashboard listens on (default: 8080)
EXPORT_DIR=exports           # Directory for export data (default: exports)
TZ=Europe/Vienna             # Timezone for log timestamps and scheduling
```

### Spotify Setup

To use the Spotify integration you need to create a **Spotify Developer App** to get API credentials (Client ID and Client Secret). This is free and takes a few minutes:

1. Go to the [Spotify Developer Dashboard](https://developer.spotify.com/dashboard) and log in with your Spotify account
2. Click **"Create app"**
3. Fill in the form:
   - **App name**: vibeshelf (or any name)
   - **App description**: Personal data backup
   - **Redirect URI**: `http://127.0.0.1:8888` — must be exactly this, Spotify does **not** allow `localhost`
4. Click **"Save"**, then go to **Settings**
5. Copy the **Client ID** and **Client Secret** into your `.env` file
6. Start the container, then run the one-time auth flow:
   ```bash
   docker compose exec vibeshelf python auth.py
   ```
   This opens a Spotify login prompt. After authorizing, the OAuth token is saved to the `cache/` volume and auto-refreshes from then on.

**Export schedule:** Liked songs & playlists every 6h, recently played every 2h, albums/artists/podcasts daily, top tracks weekly.

**Note:** Streaming Spotify tracks directly in the dashboard uses the Spotify Web Playback SDK, which requires a **Spotify Premium** account. Backup and browsing work with any account.

### Navidrome Setup

Point it at your [Navidrome](https://www.navidrome.org/) instance. The service communicates via the Subsonic API. If Navidrome runs in the same Docker network, use the container name as the host (e.g. `http://navidrome:4533`).

### Koito Setup

Point it at Koito's PostgreSQL database. The service reads from the `listens`, `tracks`, `releases`, and `artists` tables. If Koito is not configured or its database is unreachable, the dashboard falls back to Spotify's recently played export.

## Docker Compose

```yaml
services:
  vibeshelf:
    build: .
    container_name: vibeshelf
    env_file: .env
    ports:
      - "8080:8080"
    volumes:
      - ./exports:/app/exports      # persistent export data
      - ./cache:/app/.cache          # OAuth token cache
    restart: unless-stopped
```

## Data

Exports are stored in the `exports/` volume:

- `library.db` — SQLite database with full-text search (primary data store)
- `*.json` / `*.csv` — Raw Spotify API exports
- `state.json` — Scheduling state (last run timestamps)
- `art_cache/` — Cached album artwork

## License

Free to use, modify, and distribute. If you build on this project, please credit and link back to this repository.

This project uses the [Spotify Web API](https://developer.spotify.com/terms) — their Terms of Service apply when using the Spotify integration.
