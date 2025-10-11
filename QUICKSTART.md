# Quick Start Guide

Get your Spotify data in 5 minutes.

## 1. Get API Credentials (2 minutes)

1. Go to: https://developer.spotify.com/dashboard
2. Click "Create app"
3. Fill in:
   - Name: `ByeByeSpotify`
   - Redirect URI: `http://127.0.0.1:8888` (MUST use 127.0.0.1, NOT "localhost")
4. Save and copy your **Client ID**
   - No Client Secret needed - this script uses secure PKCE authentication

## 2. Install (1 minute)

```bash
pip install -r requirements.txt
cp .env.example .env
```

Edit `.env` and paste your Client ID:
```env
SPOTIFY_CLIENT_ID=paste_here
SPOTIFY_REDIRECT_URI=http://127.0.0.1:8888
```

**Troubleshooting**: If you get "INVALID_CLIENT" error, see [TROUBLESHOOTING.md](TROUBLESHOOTING.md)

## 3. Run (2 minutes)

```bash
python spotify_exporter.py
```

- Browser will open → Log in → Click "Agree"
- Copy the URL it redirects to (even if page doesn't load)
- Paste URL back in terminal → Press Enter
- Wait for export to complete

## 4. Get Your Data

Check the `exports/` folder. Start with `SUMMARY.txt` to see what was exported.

## 5. Don't Forget!

For your **complete listening history**, go to:
https://www.spotify.com/account/privacy/

Request your data → Spotify emails you within 30 days

---

That's it! See [README.md](README.md) for detailed docs.
