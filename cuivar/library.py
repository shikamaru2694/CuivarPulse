"""Playlists, offline downloads and storage settings.

Downloads are songs the user chose to keep: they live in their own folder,
play without internet and are never removed automatically. They are limited
by a user-chosen allowance (number of songs or storage); near the limit the
user is reminded of downloads they haven't played in a long time.
The automatic cache (engine.py) is separate and cleans itself up.
"""

import json
import os
import time
import uuid

# The panel offers these allowance steps; saved values snap to the nearest.
SONG_STEPS = (50, 100, 200, 500)
GB_STEPS = (2.0, 5.0, 10.0, 25.0)
DEFAULT_SETTINGS = {"limitMode": "songs", "limitSongs": 200, "limitGB": 5.0, "region": "auto", "lights": True,
                    "lightColours": "artwork", "lightFixed": "#1a9fff",
                    "haptics": "off"}


def _nearest(steps, value):
    return min(steps, key=lambda step: abs(step - float(value)))
REMIND_RATIO = 0.9
STALE_DAYS = 30
TYPICAL_SONG_BYTES = 5 * 1024 * 1024


def _clean_track(track):
    """Keep only the fields a track needs, with sane types."""
    album = track.get("album") if isinstance(track.get("album"), dict) else None
    return {
        "kind": "track",
        "id": str(track["id"]),
        "title": str(track.get("title") or "")[:300],
        "artists": [{"name": str(a.get("name") or "")[:200], "id": a.get("id")} for a in (track.get("artists") or [])[:10]],
        "album": {"name": str(album.get("name") or "")[:300], "id": album.get("id")} if album and album.get("name") else None,
        "duration": track.get("duration") if isinstance(track.get("duration"), (int, float)) else None,
        "art": track.get("art") if isinstance(track.get("art"), str) else None,
    }


def _haptics_mode(value):
    """"off" / "subtle" / "strong" (older builds saved True or "balanced" / "performance")."""
    if value is True or value == "balanced":
        return "subtle"
    if value == "performance":
        return "strong"
    return value if value in ("off", "subtle", "strong") else "off"


class LibraryError(Exception):
    pass


class Library:
    def __init__(self, data_dir, settings_dir):
        self.downloads_dir = os.path.join(data_dir, "downloads")
        os.makedirs(self.downloads_dir, exist_ok=True)
        os.makedirs(settings_dir, exist_ok=True)
        self.file = os.path.join(settings_dir, "library.json")
        try:
            with open(self.file) as f:
                self.data = json.load(f)
        except (OSError, ValueError):
            self.data = {}
        self.data.setdefault("playlists", [])
        self.data.setdefault("downloads", {})
        self.data.setdefault("plays", {})
        saved = {**DEFAULT_SETTINGS, **self.data.get("settings", {})}
        self.data["settings"] = {
            "limitMode": saved["limitMode"] if saved["limitMode"] in ("songs", "storage") else "songs",
            "limitSongs": int(_nearest(SONG_STEPS, saved["limitSongs"])),
            "limitGB": _nearest(GB_STEPS, saved["limitGB"]),
            "region": str(saved.get("region") or "auto")[:8],
            "lights": bool(saved.get("lights", True)),
            "lightColours": saved["lightColours"] if saved.get("lightColours") in ("artwork", "energy", "rainbow", "fixed") else "artwork",
            "lightFixed": str(saved.get("lightFixed") or "#1a9fff")[:7],
            "haptics": _haptics_mode(saved.get("haptics")),
        }

    def save(self):
        tmp = self.file + ".tmp"
        with open(tmp, "w") as f:
            json.dump(self.data, f)
        os.replace(tmp, self.file)

    # --- settings and usage ------------------------------------------------------

    def settings(self):
        return dict(self.data["settings"])

    def set_settings(self, mode, songs, gb):
        if mode not in ("songs", "storage"):
            raise LibraryError("Unknown limit type.")
        self.data["settings"].update({
            "limitMode": mode,
            "limitSongs": int(_nearest(SONG_STEPS, songs)),
            "limitGB": _nearest(GB_STEPS, gb),
        })
        self.save()
        return self.settings()

    def set_lights(self, on):
        self.data["settings"]["lights"] = bool(on)
        self.save()

    def set_haptics(self, mode):
        self.data["settings"]["haptics"] = _haptics_mode(mode)
        self.save()

    def set_light_colours(self, mode, fixed):
        if mode not in ("artwork", "energy", "rainbow", "fixed"):
            raise LibraryError("Unknown light colours.")
        self.data["settings"]["lightColours"] = mode
        self.data["settings"]["lightFixed"] = str(fixed or "#1a9fff")[:7]
        self.save()

    def set_region(self, region):
        self.data["settings"]["region"] = str(region or "auto")[:8]
        self.save()

    def usage(self):
        downloads = self.data["downloads"]
        count = len(downloads)
        size = sum(entry.get("size", 0) for entry in downloads.values())
        s = self.data["settings"]
        if s["limitMode"] == "songs":
            ratio = count / s["limitSongs"]
        else:
            ratio = size / (s["limitGB"] * 1024 ** 3)
        stale = self.stale()
        return {
            "songs": count,
            "bytes": size,
            "ratio": round(ratio, 3),
            "nearLimit": ratio >= REMIND_RATIO,
            "staleCount": len(stale),
        }

    def _room_for_one(self):
        s = self.data["settings"]
        u = self.usage()
        if s["limitMode"] == "songs":
            return u["songs"] + 1 <= s["limitSongs"]
        return u["bytes"] + TYPICAL_SONG_BYTES <= s["limitGB"] * 1024 ** 3

    def limit_text(self):
        s = self.data["settings"]
        return f"{s['limitSongs']} songs" if s["limitMode"] == "songs" else f"{s['limitGB']} GB"

    # --- downloads -------------------------------------------------------------------

    def downloaded_path(self, video_id):
        entry = self.data["downloads"].get(video_id)
        if not entry:
            return None
        path = os.path.join(self.downloads_dir, entry["file"])
        return path if os.path.exists(path) else None

    def is_downloaded(self, video_id):
        return self.downloaded_path(video_id) is not None

    def check_room(self):
        if not self._room_for_one():
            raise LibraryError(
                f"Your download limit ({self.limit_text()}) is reached. "
                "Remove some downloads or raise the limit in Settings."
            )

    def add_download(self, track, source_path):
        """Moves a song file (from the automatic cache) into downloads."""
        track = _clean_track(track)
        name = os.path.basename(source_path)
        target = os.path.join(self.downloads_dir, name)
        os.replace(source_path, target)
        self.data["downloads"][track["id"]] = {
            "track": track,
            "file": name,
            "size": os.path.getsize(target),
            "added": time.time(),
        }
        self.save()

    def remove_download(self, video_id):
        entry = self.data["downloads"].pop(video_id, None)
        if entry:
            try:
                os.remove(os.path.join(self.downloads_dir, entry["file"]))
            except OSError:
                pass
            self.save()

    def downloads(self):
        items = []
        for video_id, entry in self.data["downloads"].items():
            items.append({
                **entry["track"],
                "size": entry.get("size", 0),
                "added": entry.get("added", 0),
                "lastPlayed": self.data["plays"].get(video_id),
            })
        return sorted(items, key=lambda t: t["added"], reverse=True)

    def stale(self, days=STALE_DAYS):
        """Downloads not played for `days` (or never, since being added that long ago)."""
        cutoff = time.time() - days * 86400
        old = [t for t in self.downloads() if (t["lastPlayed"] or t["added"]) < cutoff]
        return sorted(old, key=lambda t: t["lastPlayed"] or t["added"])

    def played(self, video_id):
        self.data["plays"][video_id] = time.time()
        # Keep the play log small.
        if len(self.data["plays"]) > 5000:
            newest = sorted(self.data["plays"].items(), key=lambda kv: kv[1], reverse=True)[:4000]
            self.data["plays"] = dict(newest)
        self.save()

    # --- playlists ---------------------------------------------------------------------

    def _playlist(self, playlist_id):
        for playlist in self.data["playlists"]:
            if playlist["id"] == playlist_id:
                return playlist
        raise LibraryError("Playlist not found.")

    def playlists(self):
        return [{
            "id": p["id"],
            "name": p["name"],
            "count": len(p["tracks"]),
            "downloaded": sum(1 for t in p["tracks"] if self.is_downloaded(t["id"])),
            "art": next((t.get("art") for t in p["tracks"] if t.get("art")), None),
        } for p in self.data["playlists"]]

    def playlist(self, playlist_id):
        p = self._playlist(playlist_id)
        return {
            "id": p["id"],
            "name": p["name"],
            "tracks": [{**t, "downloaded": self.is_downloaded(t["id"])} for t in p["tracks"]],
        }

    def create_playlist(self, name):
        name = str(name or "").strip()[:60]
        if not name:
            raise LibraryError("Give the playlist a name.")
        playlist = {"id": uuid.uuid4().hex[:12], "name": name, "tracks": [], "created": time.time()}
        self.data["playlists"].append(playlist)
        self.save()
        return playlist["id"]

    def rename_playlist(self, playlist_id, name):
        name = str(name or "").strip()[:60]
        if not name:
            raise LibraryError("Give the playlist a name.")
        self._playlist(playlist_id)["name"] = name
        self.save()

    def delete_playlist(self, playlist_id):
        self.data["playlists"] = [p for p in self.data["playlists"] if p["id"] != playlist_id]
        self.save()

    def add_to_playlist(self, playlist_id, track):
        p = self._playlist(playlist_id)
        track = _clean_track(track)
        if any(t["id"] == track["id"] for t in p["tracks"]):
            return False
        p["tracks"].append(track)
        self.save()
        return True

    def remove_from_playlist(self, playlist_id, video_id):
        p = self._playlist(playlist_id)
        p["tracks"] = [t for t in p["tracks"] if t["id"] != video_id]
        self.save()
