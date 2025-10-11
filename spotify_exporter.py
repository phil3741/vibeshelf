#!/usr/bin/env python3
"""
Spotify Data Exporter
Extracts all your Spotify data including liked songs, playlists, albums, and more.
"""

import os
import json
import csv
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Any, Optional
import spotipy
from spotipy.oauth2 import SpotifyPKCE
from dotenv import load_dotenv


class SpotifyDataExporter:
    """Handles extraction of all Spotify user data."""

    def __init__(self):
        """Initialize the Spotify client with OAuth authentication."""
        load_dotenv()

        # Required scopes for accessing all user data
        scope = " ".join([
            "user-library-read",           # Saved tracks, albums, shows, episodes
            "user-read-recently-played",   # Recently played tracks
            "user-top-read",               # Top artists and tracks
            "user-follow-read",            # Followed artists
            "playlist-read-private",       # Private playlists
            "playlist-read-collaborative", # Collaborative playlists
        ])

        # Use PKCE flow - more secure for local apps, no client secret needed
        self.sp = spotipy.Spotify(auth_manager=SpotifyPKCE(
            client_id=os.getenv("SPOTIFY_CLIENT_ID"),
            redirect_uri=os.getenv("SPOTIFY_REDIRECT_URI", "http://127.0.0.1:8888"),
            scope=scope
        ))

        # Create exports directory
        self.export_dir = Path("exports")
        self.export_dir.mkdir(exist_ok=True)

        # Timestamp for this export session
        self.timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    def _paginate(self, method, *args, **kwargs) -> List[Dict]:
        """
        Generic pagination handler for Spotify API calls.

        Args:
            method: The Spotipy method to call
            *args: Positional arguments for the method
            **kwargs: Keyword arguments for the method

        Returns:
            List of all items from paginated results
        """
        results = []
        response = method(*args, **kwargs)

        while response:
            if isinstance(response, dict) and 'items' in response:
                results.extend(response['items'])
                if response['next']:
                    response = self.sp.next(response)
                else:
                    break
            else:
                # Not a paginated response
                return response

        return results

    def get_user_profile(self) -> Dict:
        """Get current user's profile information."""
        print("Fetching user profile...")
        return self.sp.current_user()

    def get_saved_tracks(self) -> List[Dict]:
        """Get all liked songs/saved tracks."""
        print("Fetching saved tracks (liked songs)...")
        return self._paginate(self.sp.current_user_saved_tracks, limit=50)

    def get_playlists(self) -> List[Dict]:
        """Get all user playlists with their tracks."""
        print("Fetching playlists...")
        playlists = self._paginate(self.sp.current_user_playlists, limit=50)

        # For each playlist, get all tracks
        detailed_playlists = []
        for i, playlist in enumerate(playlists, 1):
            print(f"  Fetching tracks for playlist {i}/{len(playlists)}: {playlist['name']}")

            # Get all tracks in this playlist
            tracks = self._paginate(
                self.sp.playlist_tracks,
                playlist['id'],
                limit=100
            )

            playlist_data = {
                'id': playlist['id'],
                'name': playlist['name'],
                'description': playlist.get('description', ''),
                'public': playlist.get('public', False),
                'collaborative': playlist.get('collaborative', False),
                'owner': playlist.get('owner', {}).get('display_name', 'Unknown'),
                'total_tracks': playlist['tracks']['total'],
                'tracks': tracks
            }
            detailed_playlists.append(playlist_data)

        return detailed_playlists

    def get_saved_albums(self) -> List[Dict]:
        """Get all saved albums."""
        print("Fetching saved albums...")
        return self._paginate(self.sp.current_user_saved_albums, limit=50)

    def get_followed_artists(self) -> List[Dict]:
        """Get all followed artists."""
        print("Fetching followed artists...")
        artists = []
        response = self.sp.current_user_followed_artists(limit=50)

        while response:
            if 'artists' in response and 'items' in response['artists']:
                artists.extend(response['artists']['items'])
                if response['artists']['next']:
                    response = self.sp.next(response['artists'])
                else:
                    break
            else:
                break

        return artists

    def get_top_tracks(self) -> Dict[str, List[Dict]]:
        """Get top tracks for different time ranges."""
        print("Fetching top tracks...")
        time_ranges = {
            'short_term': 'Last 4 weeks',
            'medium_term': 'Last 6 months',
            'long_term': 'All time'
        }

        top_tracks = {}
        for range_key, range_label in time_ranges.items():
            print(f"  {range_label}...")
            top_tracks[range_key] = self.sp.current_user_top_tracks(
                limit=50,
                time_range=range_key
            )['items']

        return top_tracks

    def get_top_artists(self) -> Dict[str, List[Dict]]:
        """Get top artists for different time ranges."""
        print("Fetching top artists...")
        time_ranges = {
            'short_term': 'Last 4 weeks',
            'medium_term': 'Last 6 months',
            'long_term': 'All time'
        }

        top_artists = {}
        for range_key, range_label in time_ranges.items():
            print(f"  {range_label}...")
            top_artists[range_key] = self.sp.current_user_top_artists(
                limit=50,
                time_range=range_key
            )['items']

        return top_artists

    def get_recently_played(self) -> List[Dict]:
        """Get recently played tracks (last ~50 tracks)."""
        print("Fetching recently played tracks...")
        return self.sp.current_user_recently_played(limit=50)['items']

    def get_saved_shows(self) -> List[Dict]:
        """Get all saved podcast shows."""
        print("Fetching saved shows (podcasts)...")
        return self._paginate(self.sp.current_user_saved_shows, limit=50)

    def get_saved_episodes(self) -> List[Dict]:
        """Get all saved podcast episodes."""
        print("Fetching saved episodes...")
        return self._paginate(self.sp.current_user_saved_episodes, limit=50)

    def get_saved_audiobooks(self) -> List[Dict]:
        """Get all saved audiobooks."""
        print("Fetching saved audiobooks...")
        try:
            return self._paginate(self.sp.current_user_saved_audiobooks, limit=50)
        except Exception as e:
            # Audiobooks might not be available in all regions/accounts
            print(f"  Warning: Could not fetch audiobooks: {e}")
            return []

    def export_to_json(self, data: Dict[str, Any], filename: str):
        """Export data to a JSON file."""
        filepath = self.export_dir / f"{self.timestamp}_{filename}"
        with open(filepath, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        print(f"Saved: {filepath}")

    def export_tracks_to_csv(self, tracks: List[Dict], filename: str, track_key: str = 'track'):
        """
        Export tracks to a CSV file.

        Args:
            tracks: List of track objects
            filename: Output filename
            track_key: Key to access track data (e.g., 'track' for saved tracks, None for top tracks)
        """
        filepath = self.export_dir / f"{self.timestamp}_{filename}"

        if not tracks:
            print(f"No tracks to export for {filename}")
            return

        # Extract track data
        csv_data = []
        for item in tracks:
            # Handle different response formats
            track = item.get(track_key) if track_key and track_key in item else item

            if not track:
                continue

            csv_data.append({
                'Track Name': track.get('name', 'Unknown'),
                'Artist(s)': ', '.join([artist['name'] for artist in track.get('artists', [])]),
                'Album': track.get('album', {}).get('name', 'Unknown'),
                'Release Date': track.get('album', {}).get('release_date', 'Unknown'),
                'Duration (ms)': track.get('duration_ms', 0),
                'Popularity': track.get('popularity', 0),
                'Spotify URI': track.get('uri', ''),
                'Added At': item.get('added_at', 'Unknown') if 'added_at' in item else 'N/A'
            })

        if csv_data:
            with open(filepath, 'w', newline='', encoding='utf-8') as f:
                writer = csv.DictWriter(f, fieldnames=csv_data[0].keys())
                writer.writeheader()
                writer.writerows(csv_data)
            print(f"Saved: {filepath}")

    def export_all(self):
        """Export all Spotify data."""
        print("\n" + "="*60)
        print("SPOTIFY DATA EXPORTER")
        print("="*60 + "\n")

        # Get user profile
        profile = self.get_user_profile()
        print(f"Exporting data for: {profile.get('display_name', 'Unknown User')}\n")

        all_data = {
            'export_info': {
                'timestamp': self.timestamp,
                'user': profile.get('display_name'),
                'user_id': profile.get('id'),
            }
        }

        # Export user profile
        all_data['profile'] = profile
        self.export_to_json(profile, "profile.json")

        # Export saved tracks (liked songs)
        saved_tracks = self.get_saved_tracks()
        all_data['saved_tracks'] = saved_tracks
        self.export_to_json(saved_tracks, "saved_tracks.json")
        self.export_tracks_to_csv(saved_tracks, "saved_tracks.csv", track_key='track')
        print(f"Total saved tracks: {len(saved_tracks)}\n")

        # Export playlists
        playlists = self.get_playlists()
        all_data['playlists'] = playlists
        self.export_to_json(playlists, "playlists.json")
        print(f"Total playlists: {len(playlists)}\n")

        # Export playlist tracks to individual CSVs
        for playlist in playlists:
            safe_name = "".join(c for c in playlist['name'] if c.isalnum() or c in (' ', '_')).strip()
            safe_name = safe_name.replace(' ', '_')[:50]  # Limit length
            self.export_tracks_to_csv(
                playlist['tracks'],
                f"playlist_{safe_name}.csv",
                track_key='track'
            )

        # Export saved albums
        saved_albums = self.get_saved_albums()
        all_data['saved_albums'] = saved_albums
        self.export_to_json(saved_albums, "saved_albums.json")
        print(f"Total saved albums: {len(saved_albums)}\n")

        # Export followed artists
        followed_artists = self.get_followed_artists()
        all_data['followed_artists'] = followed_artists
        self.export_to_json(followed_artists, "followed_artists.json")
        print(f"Total followed artists: {len(followed_artists)}\n")

        # Export top tracks
        top_tracks = self.get_top_tracks()
        all_data['top_tracks'] = top_tracks
        self.export_to_json(top_tracks, "top_tracks.json")
        for time_range, tracks in top_tracks.items():
            self.export_tracks_to_csv(tracks, f"top_tracks_{time_range}.csv", track_key=None)
        print()

        # Export top artists
        top_artists = self.get_top_artists()
        all_data['top_artists'] = top_artists
        self.export_to_json(top_artists, "top_artists.json")
        print()

        # Export recently played
        recently_played = self.get_recently_played()
        all_data['recently_played'] = recently_played
        self.export_to_json(recently_played, "recently_played.json")
        print(f"Total recently played tracks: {len(recently_played)}\n")

        # Export saved shows (podcasts)
        saved_shows = self.get_saved_shows()
        all_data['saved_shows'] = saved_shows
        self.export_to_json(saved_shows, "saved_shows.json")
        print(f"Total saved shows: {len(saved_shows)}\n")

        # Export saved episodes
        saved_episodes = self.get_saved_episodes()
        all_data['saved_episodes'] = saved_episodes
        self.export_to_json(saved_episodes, "saved_episodes.json")
        print(f"Total saved episodes: {len(saved_episodes)}\n")

        # Export saved audiobooks
        saved_audiobooks = self.get_saved_audiobooks()
        all_data['saved_audiobooks'] = saved_audiobooks
        if saved_audiobooks:
            self.export_to_json(saved_audiobooks, "saved_audiobooks.json")
            print(f"Total saved audiobooks: {len(saved_audiobooks)}\n")

        # Export complete data dump
        self.export_to_json(all_data, "complete_export.json")

        # Create summary report
        self._create_summary_report(all_data)

        print("\n" + "="*60)
        print("EXPORT COMPLETE!")
        print("="*60)
        print(f"\nAll data has been exported to: {self.export_dir}/")
        print(f"\nIMPORTANT: For your complete listening history, request your data from:")
        print("https://www.spotify.com/account/privacy/")
        print("(Spotify will email you a data archive within 30 days)")
        print()

    def _create_summary_report(self, data: Dict):
        """Create a human-readable summary report."""
        report_file = self.export_dir / f"{self.timestamp}_SUMMARY.txt"

        with open(report_file, 'w', encoding='utf-8') as f:
            f.write("="*60 + "\n")
            f.write("SPOTIFY DATA EXPORT SUMMARY\n")
            f.write("="*60 + "\n\n")

            f.write(f"Export Date: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
            f.write(f"User: {data.get('export_info', {}).get('user', 'Unknown')}\n")
            f.write(f"User ID: {data.get('export_info', {}).get('user_id', 'Unknown')}\n\n")

            f.write("-"*60 + "\n")
            f.write("DATA SUMMARY\n")
            f.write("-"*60 + "\n\n")

            # Saved tracks
            saved_tracks = len(data.get('saved_tracks', []))
            f.write(f"Saved Tracks (Liked Songs): {saved_tracks}\n")

            # Playlists
            playlists = data.get('playlists', [])
            total_playlist_tracks = sum(p.get('total_tracks', 0) for p in playlists)
            f.write(f"Playlists: {len(playlists)}\n")
            f.write(f"  Total tracks across all playlists: {total_playlist_tracks}\n")

            # Albums
            saved_albums = len(data.get('saved_albums', []))
            f.write(f"Saved Albums: {saved_albums}\n")

            # Artists
            followed_artists = len(data.get('followed_artists', []))
            f.write(f"Followed Artists: {followed_artists}\n")

            # Shows and episodes
            saved_shows = len(data.get('saved_shows', []))
            saved_episodes = len(data.get('saved_episodes', []))
            f.write(f"Saved Shows (Podcasts): {saved_shows}\n")
            f.write(f"Saved Episodes: {saved_episodes}\n")

            # Audiobooks
            saved_audiobooks = len(data.get('saved_audiobooks', []))
            if saved_audiobooks > 0:
                f.write(f"Saved Audiobooks: {saved_audiobooks}\n")

            # Recently played
            recently_played = len(data.get('recently_played', []))
            f.write(f"Recently Played Tracks (last 50): {recently_played}\n\n")

            # Top tracks and artists
            f.write("-"*60 + "\n")
            f.write("TOP CONTENT\n")
            f.write("-"*60 + "\n\n")

            for time_range in ['short_term', 'medium_term', 'long_term']:
                range_label = {
                    'short_term': 'Last 4 weeks',
                    'medium_term': 'Last 6 months',
                    'long_term': 'All time'
                }[time_range]

                top_tracks = data.get('top_tracks', {}).get(time_range, [])
                top_artists = data.get('top_artists', {}).get(time_range, [])

                f.write(f"{range_label}:\n")
                f.write(f"  Top Tracks: {len(top_tracks)}\n")
                f.write(f"  Top Artists: {len(top_artists)}\n")

                if top_tracks:
                    f.write(f"  #1 Track: {top_tracks[0].get('name', 'Unknown')} - "
                           f"{', '.join([a['name'] for a in top_tracks[0].get('artists', [])])}\n")

                if top_artists:
                    f.write(f"  #1 Artist: {top_artists[0].get('name', 'Unknown')}\n")

                f.write("\n")

            f.write("-"*60 + "\n")
            f.write("FILES EXPORTED\n")
            f.write("-"*60 + "\n\n")

            f.write("JSON Files:\n")
            f.write("  - complete_export.json (all data in one file)\n")
            f.write("  - profile.json\n")
            f.write("  - saved_tracks.json\n")
            f.write("  - playlists.json\n")
            f.write("  - saved_albums.json\n")
            f.write("  - followed_artists.json\n")
            f.write("  - top_tracks.json\n")
            f.write("  - top_artists.json\n")
            f.write("  - recently_played.json\n")
            f.write("  - saved_shows.json\n")
            f.write("  - saved_episodes.json\n")
            if saved_audiobooks > 0:
                f.write("  - saved_audiobooks.json\n")

            f.write("\nCSV Files:\n")
            f.write("  - saved_tracks.csv (all liked songs)\n")
            f.write("  - top_tracks_short_term.csv\n")
            f.write("  - top_tracks_medium_term.csv\n")
            f.write("  - top_tracks_long_term.csv\n")
            f.write(f"  - {len(playlists)} individual playlist CSV files\n\n")

            f.write("="*60 + "\n")
            f.write("IMPORTANT NOTES\n")
            f.write("="*60 + "\n\n")

            f.write("1. Complete Listening History:\n")
            f.write("   The Spotify API only provides the last ~50 recently played tracks.\n")
            f.write("   For your complete listening history, you must request your data from:\n")
            f.write("   https://www.spotify.com/account/privacy/\n")
            f.write("   (Spotify will email you within 30 days)\n\n")

            f.write("2. Data Format:\n")
            f.write("   - JSON files contain complete metadata\n")
            f.write("   - CSV files are simplified for spreadsheet viewing\n")
            f.write("   - All Spotify URIs are preserved for reference\n\n")

            f.write("3. Backup:\n")
            f.write("   Store these files safely - they represent your Spotify library!\n\n")

        print(f"Saved: {report_file}")


def main():
    """Main entry point."""
    try:
        exporter = SpotifyDataExporter()
        exporter.export_all()
    except Exception as e:
        print(f"\nError: {e}")
        print("\nMake sure you have:")
        print("1. Created a .env file with your Spotify API credentials")
        print("2. Installed dependencies: pip install -r requirements.txt")
        print("\nSee README.md for setup instructions.")
        raise


if __name__ == "__main__":
    main()
