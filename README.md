# ByeByeSpotify - Export Your Spotify Data

A comprehensive Python script to extract all your Spotify data before leaving the platform. This tool downloads your liked songs, playlists, albums, followed artists, listening stats, and more.

## What Data Gets Exported

This script extracts:

- **Liked Songs** - All tracks saved in "Your Music" library
- **Playlists** - All your playlists (owned and followed) with complete track listings
- **Saved Albums** - All albums you've saved
- **Followed Artists** - Artists you follow
- **Top Tracks & Artists** - Your most listened to content over different time periods:
  - Last 4 weeks (short-term)
  - Last 6 months (medium-term)
  - All time (long-term)
- **Recently Played** - Last 50 recently played tracks
- **Saved Shows** - Podcast shows you follow
- **Saved Episodes** - Individual podcast episodes you've saved
- **Saved Audiobooks** - Audiobooks in your library (if available in your region)

## Output Formats

The script exports data in two formats:

1. **JSON Files** - Complete metadata with all details from Spotify
2. **CSV Files** - Simplified spreadsheet format for easy viewing/importing

## Important Limitation: Full Listening History

The Spotify API only provides the last ~50 recently played tracks. For your **complete listening history**, you must:

1. Go to: <https://www.spotify.com/account/privacy/>
2. Scroll down to "Download your data"
3. Request your data (Spotify will email you within 30 days)

This will include your complete streaming history, which is NOT available via the API.

## Prerequisites

- Python 3.7 or higher
- A Spotify account
- Spotify API credentials (free)

## Setup Instructions

### Step 1: Get Spotify API Credentials

1. Go to the [Spotify Developer Dashboard](https://developer.spotify.com/dashboard)
2. Log in with your Spotify account
3. Click "Create app"
4. Fill in the form:
   - **App name**: ByeByeSpotify (or any name you want)
   - **App description**: Personal data export tool
   - **Redirect URI**: `http://127.0.0.1:8888`
     - **CRITICAL**: Must use `127.0.0.1` exactly - Spotify does NOT allow "localhost"
     - Must include port number `:8888`
     - No trailing slash
   - Check the Terms of Service box
5. Click "Save"
6. On your app page, click "Settings"
7. Copy your **Client ID** (you'll need this next)
   - Note: You don't need the Client Secret - this script uses PKCE authentication which is more secure for local apps

### Step 2: Install Dependencies

Clone or download this repository, then install the required packages:

```bash
pip install -r requirements.txt
```

Or install manually:

```bash
pip install spotipy python-dotenv
```

### Step 3: Configure Environment Variables

1. Copy the example environment file:

   ```bash
   cp .env.example .env
   ```

2. Edit the `.env` file and add your Client ID:

   ```env
   SPOTIFY_CLIENT_ID=your_client_id_here
   SPOTIFY_REDIRECT_URI=http://127.0.0.1:8888
   ```

   Replace `your_client_id_here` with the value from Step 1.

   **IMPORTANT**: The redirect URI must be `http://127.0.0.1:8888` exactly - Spotify does NOT accept "localhost".

### Step 4: Run the Exporter

```bash
python spotify_exporter.py
```

On first run:

1. A browser window will open asking you to authorize the app
2. Log in to Spotify and click "Agree"
3. You'll be redirected to a page that might say "This site can't be reached" - **this is normal**
4. Copy the entire URL from your browser's address bar
5. Paste it into the terminal where the script is running
6. Press Enter

The script will then start downloading all your data. This may take several minutes depending on the size of your library.

## Output

All data will be saved in the `exports/` directory with timestamps:

```plaintext
exports/
├── 20250311_143022_SUMMARY.txt              # Human-readable summary
├── 20250311_143022_complete_export.json     # All data in one file
├── 20250311_143022_profile.json             # Your profile info
├── 20250311_143022_saved_tracks.json        # Liked songs (JSON)
├── 20250311_143022_saved_tracks.csv         # Liked songs (CSV)
├── 20250311_143022_playlists.json           # All playlists with tracks
├── 20250311_143022_playlist_*.csv           # Individual playlist CSVs
├── 20250311_143022_saved_albums.json        # Saved albums
├── 20250311_143022_followed_artists.json    # Followed artists
├── 20250311_143022_top_tracks.json          # Top tracks (all time ranges)
├── 20250311_143022_top_tracks_*.csv         # Top tracks CSVs
├── 20250311_143022_top_artists.json         # Top artists
├── 20250311_143022_recently_played.json     # Recently played
├── 20250311_143022_saved_shows.json         # Podcast shows
├── 20250311_143022_saved_episodes.json      # Podcast episodes
└── 20250311_143022_saved_audiobooks.json    # Audiobooks (if available)
```

### Understanding the Output

- **SUMMARY.txt** - Start here! Contains a human-readable overview of all exported data
- **complete_export.json** - Everything in one file if you want a single backup
- **CSV files** - Easy to open in Excel, Google Sheets, or import into other music services
- **JSON files** - Complete metadata including Spotify URIs, ISRCs, and all technical details

## Migrating to Another Service

The CSV files make it easy to migrate your data:

### Apple Music

- Use the CSV files with tools like [SongShift](https://songshift.com/) or [TuneMyMusic](https://www.tunemymusic.com/)

### YouTube Music

- Import CSVs using [TuneMyMusic](https://www.tunemymusic.com/) or similar services

### Tidal, Deezer, etc

- Most migration tools accept CSV files with track names, artists, and albums

### Local Music Library

- Use the exported data as a reference to rebuild your library
- Spotify URIs are preserved if you ever want to cross-reference

## Troubleshooting

### "No module named 'spotipy'"

Run: `pip install -r requirements.txt`

### "Invalid client" error

- Double-check your Client ID in `.env`
- Make sure there are no extra spaces or quotes
- Make sure you're using credentials from <https://developer.spotify.com/dashboard>

### "Invalid redirect URI" error or "INVALID_CLIENT" error

This is the most common issue! The redirect URI must be EXACTLY `http://127.0.0.1:8888`

**According to Spotify's official documentation**:

- ✅ ONLY `http://127.0.0.1:PORT` is allowed for localhost
- ❌ "localhost" is NOT allowed (e.g., `http://localhost:8888` will NOT work)
- ❌ No trailing slash (e.g., `http://127.0.0.1:8888/`)
- ❌ No /callback path (e.g., `http://127.0.0.1:8888/callback`)

**Quick fix**:

1. In Spotify Dashboard → Your App → Settings → Redirect URIs
2. Make sure it says EXACTLY: `http://127.0.0.1:8888`
3. In your `.env` file, make sure it says EXACTLY: `SPOTIFY_REDIRECT_URI=http://127.0.0.1:8888`
4. They must match character-for-character

**For detailed troubleshooting**, see [TROUBLESHOOTING.md](TROUBLESHOOTING.md) which covers all redirect URI issues.

### Rate limiting / Timeout errors

- The script handles pagination and rate limiting automatically
- If you have a very large library (thousands of playlists), the script may take 10-15 minutes
- If it fails, you can run it again - it will create a new export

### "Could not fetch audiobooks" warning

- This is normal - audiobooks aren't available in all regions/account types
- The script will continue and export everything else

## Privacy & Security

- **PKCE Authentication** - Uses the modern PKCE OAuth flow, which is more secure for local apps
- **No Client Secret Required** - PKCE doesn't need a client secret, reducing security risks
- **Local Credentials** - Your Client ID is stored locally in `.env` (never committed to git)
- **Local Data Only** - All data stays on your computer - nothing is sent anywhere except Spotify's API
- **Secure Redirect** - Uses `http://127.0.0.1:8888` which is Spotify's approved localhost exception
- **Token Caching** - OAuth tokens are cached in `.cache` for convenience (also not committed to git)
- Review `.gitignore` to see what's excluded from version control

## Advanced Usage

### Export Only Specific Data

Edit `spotify_exporter.py` and comment out the sections you don't need. For example, to only export liked songs:

```python
# In the export_all() method, comment out what you don't want:
# playlists = self.get_playlists()  # Skip playlists
# saved_albums = self.get_saved_albums()  # Skip albums
```

### Automated Backups

Add this script to a cron job or scheduled task to automatically backup your data periodically:

```bash
# Run every week on Sunday at 2 AM
0 2 * * 0 cd /path/to/ByeByeSpotify && /usr/bin/python3 spotify_exporter.py
```

### Custom Export Directory

Change this line in `spotify_exporter.py`:

```python
self.export_dir = Path("exports")  # Change to your preferred path
```

## Data Retention

Once you export your data:

1. **Back it up!** - Store copies in multiple locations (external drive, cloud storage, etc.)
2. **Test the exports** - Open the CSV files to make sure everything exported correctly
3. **Request your full history** - Don't forget to request your complete listening history from Spotify's privacy page
4. **Keep your credentials** - You might want to re-export data later as you add more songs

## Contributing

Found a bug? Want to add a feature? Pull requests are welcome!

## License

This project is provided as-is for personal use. Spotify's API terms of service apply.

## Acknowledgments

- Built with [Spotipy](https://spotipy.readthedocs.io/) - Python library for the Spotify Web API
- Inspired by the need to preserve personal data before platform migrations

## FAQ

**Q: Will this delete my Spotify data?**
A: No! This is read-only. It only downloads/exports your data.

**Q: Can I run this multiple times?**
A: Yes! Each run creates a new timestamped export, so you can track changes over time.

**Q: Why isn't my full listening history included?**
A: Spotify's API only provides the last ~50 tracks. Request your full history from: <https://www.spotify.com/account/privacy/>

**Q: Can I use this for someone else's account?**
A: Only if they authorize it. When you run the script, it will ask for Spotify login - whoever logs in is whose data gets exported.

**Q: How much disk space do I need?**
A: Most exports are 10-100 MB, depending on library size. Very large libraries (50+ playlists, 10,000+ songs) might be 500 MB.

**Q: Does this work with Spotify Free accounts?**
A: Yes! The API works with both Free and Premium accounts.

**Q: Will Spotify ban my account for using this?**
A: No. You're using official Spotify APIs for personal data access, which is allowed and encouraged.

## Support

For issues or questions:

1. Check the Troubleshooting section above
2. Review the [Spotipy documentation](https://spotipy.readthedocs.io/)
3. Check [Spotify API documentation](https://developer.spotify.com/documentation/web-api)
4. Open an issue on GitHub

---

**Remember**: After you export your data, request your complete listening history from <https://www.spotify.com/account/privacy/> - it's the only way to get your full play history!
