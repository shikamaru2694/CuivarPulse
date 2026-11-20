"""Cuivar Pulse for SteamOS: Decky backend.

Runs in the background for as long as Decky runs, independent of games, so
music keeps playing while you play. Owns the queue, the player and the
library; the Quick Access Menu panel only sends commands and shows state.
"""

import asyncio
import json
import os
import random
import sys

import decky

PLUGIN_DIR = decky.DECKY_PLUGIN_DIR
for path in (PLUGIN_DIR, os.path.join(PLUGIN_DIR, "py_modules")):
    if path not in sys.path:
        sys.path.insert(0, path)

from cuivar import catalog, lights  # noqa: E402
from cuivar.engine import Engine, EngineError  # noqa: E402
from cuivar.library import Library, LibraryError  # noqa: E402
from cuivar.player import Player  # noqa: E402
from cuivar.voice import Voice, VoiceError  # noqa: E402

SESSION_FILE = os.path.join(decky.DECKY_PLUGIN_SETTINGS_DIR, "session.json")


def ok(**values):
    return {"ok": True, **values}


def fail(message):
    return {"ok": False, "error": message}


class Plugin:
    async def _main(self):
        bin_dir = os.path.join(PLUGIN_DIR, "bin")
        self.engine = Engine(
            decky.DECKY_PLUGIN_RUNTIME_DIR,
            os.path.join(bin_dir, "yt-dlp_linux"),
            js_runtime=f"quickjs:{Engine.runnable_copy(os.path.join(bin_dir, 'qjs'), decky.DECKY_PLUGIN_RUNTIME_DIR)}",
        )
        self.library = Library(decky.DECKY_PLUGIN_RUNTIME_DIR, decky.DECKY_PLUGIN_SETTINGS_DIR)
        self.player = Player(self._song_ended)
        self.queue = []
        self.index = -1
        self.loading = False
        self.error = None
        self.token = 0
        self.shuffle = False
        self.unshuffled = None  # upcoming songs in their original order, while shuffled
        self.repeat = "off"  # off | all | one
        self.download_queue = []  # tracks waiting to be downloaded
        self.downloading = None  # track being downloaded now
        self.download_wakeup = asyncio.Event()
        # Leftovers from the removed game-mood feature.
        self.library.data.pop("gameMoods", None)
        try:
            os.remove(os.path.join(decky.DECKY_PLUGIN_SETTINGS_DIR, "steam-tags.json"))
        except OSError:
            pass
        catalog.set_region(self.library.settings().get("region", "auto"))
        self._restore_session()
        self.lights = lights.Lights(
            self.player, os.path.join(decky.DECKY_PLUGIN_SETTINGS_DIR, "lights-saved.json"), decky.logger,
            art_lookup=lambda: (self._current() or {}).get("art"))
        settings = self.library.settings()
        self.lights.set_colours(settings["lightColours"], settings["lightFixed"])
        self.lights.set_enabled(settings["lights"])
        self.lights.haptics_available = await asyncio.to_thread(lights.haptics_available)
        self.lights.set_haptics(settings["haptics"])
        self.voice = Voice(decky.DECKY_PLUGIN_RUNTIME_DIR, self.player, decky.logger)
        loop = asyncio.get_running_loop()
        self.tasks = [
            loop.create_task(self._tick()),
            loop.create_task(self._download_worker()),
            loop.create_task(self.engine.adopt_newer_bundled()),
        ]
        decky.logger.info(f"Cuivar Pulse started (Python {sys.version.split()[0]})")

    async def _unload(self):
        self._save_session()
        for task in self.tasks:
            task.cancel()
        self.lights.shutdown()
        await self.voice.shutdown()
        self.engine.shutdown()
        await self.player.stop()
        decky.logger.info("Cuivar Pulse stopped")

    async def _uninstall(self):
        pass

    # --- session: survive Decky restarts and reboots -------------------------------

    def _save_session(self):
        try:
            with open(SESSION_FILE + ".tmp", "w") as f:
                json.dump({
                    "queue": self.queue[:300], "index": self.index, "position": self.player.position(),
                    "shuffle": self.shuffle, "repeat": self.repeat, "volume": self.player.volume,
                }, f)
            os.replace(SESSION_FILE + ".tmp", SESSION_FILE)
        except Exception:
            decky.logger.exception("could not save session")

    def _restore_session(self):
        try:
            with open(SESSION_FILE) as f:
                session = json.load(f)
            self.queue = session.get("queue") or []
            self.index = session.get("index", -1) if self.queue else -1
            # Paused at the remembered position; the play button resumes.
            self.player.offset = float(session.get("position") or 0)
            self.shuffle = bool(session.get("shuffle"))
            self.repeat = session.get("repeat") if session.get("repeat") in ("off", "all", "one") else "off"
            volume = session.get("volume")
            if isinstance(volume, (int, float)):
                self.player.volume = int(min(max(volume, 0), 100))
        except (OSError, ValueError):
            pass

    # --- state shared with the panel -------------------------------------------------

    def _current(self):
        return self.queue[self.index] if 0 <= self.index < len(self.queue) else None

    def _state(self):
        track = self._current()
        return {
            "track": track,
            "playing": self.player.playing,
            "loading": self.loading,
            "position": round(self.player.position(), 1),
            "duration": self.player.duration or (track or {}).get("duration") or 0,
            "index": self.index,
            "upNext": self.queue[self.index + 1:self.index + 21],
            "error": self.error,
            "downloaded": bool(track and self.library.is_downloaded(track["id"])),
            "downloadingId": self.downloading["id"] if self.downloading else None,
            "pendingIds": [t["id"] for t in self.download_queue],
            "shuffle": self.shuffle,
            "repeat": self.repeat,
            "volume": self.player.volume,
        }

    async def _emit(self):
        await decky.emit("state", self._state())

    async def _emit_library(self):
        await decky.emit("library")

    async def _notice(self, text):
        await decky.emit("notice", text)

    async def _tick(self):
        seconds = 0
        while True:
            await asyncio.sleep(1)
            if self.player.playing:
                await self._emit()
                seconds += 1
                if seconds % 10 == 0:
                    self._save_session()

    # --- playback commands --------------------------------------------------------------

    async def get_state(self):
        return self._state()

    async def search(self, query):
        try:
            return ok(results=await asyncio.to_thread(catalog.search, query))
        except Exception:
            decky.logger.exception("search failed")
            return fail("Search failed. Check the internet connection.")

    async def play_track(self, track):
        """Play a song and continue with a radio built from it."""
        self.queue = [track]
        self.index = 0
        self.unshuffled = None
        self._start_soon()
        asyncio.get_running_loop().create_task(self._add_radio(track["id"]))

    async def play_list(self, tracks, start=0, shuffle=False):
        """Play a list (playlist, downloads) from a position, optionally shuffled."""
        tracks = [t for t in tracks if isinstance(t, dict) and t.get("id")]
        if not tracks:
            return
        start = min(max(int(start), 0), len(tracks) - 1)
        self.unshuffled = None
        if shuffle:
            # "Shuffle" button: a random first song; also turns shuffle on.
            self.shuffle = True
            first = random.choice(tracks) if start == 0 else tracks[start]
        else:
            first = tracks[start]
        if self.shuffle:
            ordered = tracks[start + 1:] + tracks[:start]
            rest = [t for t in tracks if t is not first]
            self.unshuffled = [t for t in ordered if t is not first]
            random.shuffle(rest)
            tracks, start = [first] + rest, 0
        self.queue = tracks
        self.index = start
        self._start_soon()

    async def play_next(self, track):
        """Insert a song right after the current one."""
        if self._current() is None:
            await self.play_track(track)
            return
        self.queue.insert(self.index + 1, track)
        await self._emit()

    async def play_at(self, index):
        if 0 <= index < len(self.queue):
            self.index = index
            self._start_soon()

    async def toggle(self):
        if self.loading:
            return
        if self.player.file is None and self._current():
            self._start_soon(resume_at=self.player.offset)
            return
        await self.player.toggle()
        self._save_session()
        await self._emit()

    async def next(self):
        await self._advance()

    async def previous(self):
        if self.player.position() > 3 or self.index <= 0:
            await self.player.seek(0)
            await self._emit()
            return
        self.index -= 1
        self._start_soon()

    async def seek(self, seconds):
        await self.player.seek(float(seconds))
        await self._emit()

    async def seek_by(self, delta):
        await self.player.seek(self.player.position() + float(delta))
        await self._emit()

    async def set_shuffle(self, on):
        """Shuffle the songs after the current one; off restores their order."""
        on = bool(on)
        if on == self.shuffle:
            return self._state()
        upcoming = self.queue[self.index + 1:]
        if on:
            self.unshuffled = list(upcoming)
            random.shuffle(upcoming)
        elif self.unshuffled is not None:
            # Original order, keeping only songs that are still upcoming
            # (plus anything added while shuffled, at the end).
            remaining = {id(t) for t in upcoming}
            ordered = [t for t in self.unshuffled if id(t) in remaining]
            ordered_ids = {id(t) for t in ordered}
            upcoming = ordered + [t for t in upcoming if id(t) not in ordered_ids]
            self.unshuffled = None
        self.queue = self.queue[:self.index + 1] + upcoming
        self.shuffle = on
        self._save_session()
        await self._emit()
        return self._state()

    async def set_volume(self, percent):
        await self.player.set_volume(percent)
        self._save_session()
        await self._emit()
        return self.player.volume

    async def cycle_repeat(self):
        self.repeat = {"off": "all", "all": "one", "one": "off"}[self.repeat]
        self._save_session()
        await self._emit()
        return self._state()

    # --- playback internals ---------------------------------------------------------------

    def _start_soon(self, resume_at=0.0):
        asyncio.get_running_loop().create_task(self._start(resume_at))

    async def _song_path(self, track):
        """A downloaded song plays from disk (works offline); otherwise via the cache."""
        return self.library.downloaded_path(track["id"]) or await self.engine.fetch(track["id"])

    async def _start(self, resume_at=0.0):
        self.token += 1
        token = self.token
        track = self._current()
        if not track:
            return
        await self.player.stop()
        self.loading = True
        self.error = None
        await self._emit()

        try:
            path = await self._song_path(track)
        except EngineError as error:
            if token != self.token:
                return
            self.loading = False
            self.error = str(error)
            await self._emit()
            if error.code == "UNAVAILABLE" and self.index < len(self.queue) - 1:
                self.index += 1
                await self._start()
            return
        except Exception:
            decky.logger.exception("fetch failed")
            if token == self.token:
                self.loading = False
                self.error = "This song could not be loaded."
                await self._emit()
            return

        if token != self.token:
            return  # another song was chosen meanwhile
        self.loading = False
        await self.player.play(path, resume_at, track.get("duration"))
        self.lights.song_changed(path, track.get("art"))
        self.library.played(track["id"])
        self._save_session()
        await self._emit()
        upcoming = [t["id"] for t in self.queue[self.index + 1:self.index + 3] if not self.library.is_downloaded(t["id"])]
        self.engine.prefetch(upcoming)

    async def _song_ended(self):
        # Repeat one replays only when a song ends by itself; Next still skips.
        if self.repeat == "one" and self._current():
            await self._start()
            return
        await self._advance()

    async def _advance(self):
        if self.index < len(self.queue) - 1:
            self.index += 1
            await self._start()
            return
        if self.repeat == "all" and self.queue:
            self.index = 0
            await self._start()
            return
        # End of the queue: keep the music going with a radio from the last song.
        track = self._current()
        if track and await self._add_radio(track["id"]) and self.index < len(self.queue) - 1:
            self.index += 1
            await self._start()

    async def _add_radio(self, video_id):
        try:
            station = await asyncio.to_thread(catalog.radio, video_id)
        except Exception:
            decky.logger.info("radio unavailable (offline?)")
            return False
        known = {t["id"] for t in self.queue}
        fresh = [t for t in station if t["id"] not in known]
        current = self._current()
        # Only if the song the radio was built from is still the one playing.
        if not fresh or not current or current["id"] != video_id:
            return False
        self.queue.extend(fresh)
        await self._emit()
        return True

    # --- library: playlists -------------------------------------------------------------

    async def get_library(self):
        return {
            "playlists": self.library.playlists(),
            "downloads": len(self.library.data["downloads"]),
            "settings": self.library.settings(),
            "usage": self.library.usage(),
            "cacheBytes": self.engine.cache_bytes(),
        }

    async def get_playlist(self, playlist_id):
        try:
            return ok(playlist=self.library.playlist(playlist_id))
        except LibraryError as error:
            return fail(str(error))

    async def create_playlist(self, name, track=None):
        try:
            playlist_id = self.library.create_playlist(name)
            if track:
                self.library.add_to_playlist(playlist_id, track)
        except LibraryError as error:
            return fail(str(error))
        await self._emit_library()
        return ok(id=playlist_id)

    async def rename_playlist(self, playlist_id, name):
        try:
            self.library.rename_playlist(playlist_id, name)
        except LibraryError as error:
            return fail(str(error))
        await self._emit_library()
        return ok()

    async def delete_playlist(self, playlist_id):
        self.library.delete_playlist(playlist_id)
        await self._emit_library()
        return ok()

    async def add_to_playlist(self, playlist_id, track):
        try:
            added = self.library.add_to_playlist(playlist_id, track)
        except LibraryError as error:
            return fail(str(error))
        await self._emit_library()
        return ok(added=added)

    async def remove_from_playlist(self, playlist_id, video_id):
        try:
            self.library.remove_from_playlist(playlist_id, video_id)
        except LibraryError as error:
            return fail(str(error))
        await self._emit_library()
        return ok()

    # --- library: downloads ---------------------------------------------------------------

    async def get_downloads(self):
        return {"tracks": self.library.downloads(), "stale": self.library.stale(), "usage": self.library.usage()}

    async def download_tracks(self, tracks):
        """Queue songs for offline download (skips ones already downloaded or queued)."""
        queued_ids = {t["id"] for t in self.download_queue}
        if self.downloading:
            queued_ids.add(self.downloading["id"])
        added = 0
        for track in tracks:
            if not isinstance(track, dict) or not track.get("id"):
                continue
            if self.library.is_downloaded(track["id"]) or track["id"] in queued_ids:
                continue
            self.download_queue.append(track)
            queued_ids.add(track["id"])
            added += 1
        self.download_wakeup.set()
        await self._emit()
        return ok(added=added)

    async def download_playlist(self, playlist_id):
        try:
            tracks = self.library.playlist(playlist_id)["tracks"]
        except LibraryError as error:
            return fail(str(error))
        return await self.download_tracks(tracks)

    async def remove_downloads(self, video_ids):
        for video_id in video_ids:
            self.library.remove_download(video_id)
        await self._emit_library()
        await self._emit()
        return ok()

    async def cancel_downloads(self):
        self.download_queue = []
        await self._emit()
        return ok()

    async def _download_worker(self):
        while True:
            if not self.download_queue:
                self.download_wakeup.clear()
                await self.download_wakeup.wait()
                continue
            track = self.download_queue.pop(0)
            try:
                self.library.check_room()
            except LibraryError as error:
                skipped = len(self.download_queue) + 1
                self.download_queue = []
                await self._notice(f"{error} ({skipped} not downloaded)")
                await self._emit()
                continue

            self.downloading = track
            await self._emit()
            try:
                await self.engine.fetch(track["id"])
                source = self.engine.release(track["id"])
                if source:
                    self.library.add_download(track, source)
            except EngineError as error:
                await self._notice(f"Could not download “{track.get('title', 'song')}”: {error}")
                if error.code == "BLOCKED":
                    # YouTube is refusing everything for now; stop instead of hammering.
                    self.download_queue = []
            except Exception:
                decky.logger.exception("download failed")
            finally:
                self.downloading = None
            await self._emit()
            await self._emit_library()
            if not self.download_queue:
                usage = self.library.usage()
                if usage["nearLimit"]:
                    await self._notice("You're close to your download limit. See Settings → Storage.")

    # --- settings ----------------------------------------------------------------------

    async def get_lights(self):
        settings = self.library.settings()
        return {"available": self.lights.available, "enabled": self.lights.enabled,
                "colours": settings["lightColours"], "fixed": settings["lightFixed"],
                "hapticsAvailable": self.lights.haptics_available, "haptics": self.lights.haptics}

    async def set_haptics(self, mode):
        self.library.set_haptics(mode)
        self.lights.set_haptics(self.library.settings()["haptics"])
        if self.lights.haptics != "off" and self.player.file and not self.lights.levels:
            track = self._current()
            self.lights.song = None
            self.lights.song_changed(self.player.file, track.get("art") if track else None)
        return await self.get_lights()

    async def set_light_colours(self, mode, fixed):
        try:
            self.library.set_light_colours(mode, fixed)
        except LibraryError as error:
            return fail(str(error))
        self.lights.set_colours(mode, fixed)
        return ok(**await self.get_lights())

    async def set_lights(self, on):
        self.library.set_lights(on)
        self.lights.set_enabled(on)
        if self.lights.enabled and self.player.file:
            track = self._current()
            self.lights.song = None
            self.lights.song_changed(self.player.file, track.get("art") if track else None)
        return await self.get_lights()

    async def set_settings(self, mode, songs, gb):
        try:
            settings = self.library.set_settings(mode, songs, gb)
        except (LibraryError, ValueError, TypeError):
            return fail("Invalid setting.")
        await self._emit_library()
        return ok(settings=settings)

    async def get_regions(self):
        return {
            "selected": self.library.settings().get("region", "auto"),
            "auto": catalog.auto_region(),
            "regions": [{"code": code, "name": name} for code, name in sorted(catalog.REGIONS.items(), key=lambda kv: kv[1])],
        }

    async def set_region(self, region):
        self.library.set_region(region)
        catalog.set_region(region)
        await self._emit_library()
        return ok(region=catalog.effective_region())

    async def get_engine(self):
        return {"version": await self.engine.version(), "lastError": self.engine.last_error}

    async def update_engine(self):
        try:
            message = await self.engine.update()
        except EngineError as error:
            return fail(str(error))
        return ok(message=message, version=await self.engine.version())

    async def clear_cache(self):
        current = self._current()
        self.engine.clear(keep={current["id"]} if current else set())
        await self._emit_library()
        return ok()

    # --- voice search ------------------------------------------------------------------

    async def voice_status(self):
        return self.voice.status()

    async def voice_prepare(self):
        return self.voice.prepare()

    async def voice_remove(self):
        return self.voice.remove()

    async def voice_start(self):
        try:
            await self.voice.start()
        except (VoiceError, OSError) as error:
            return fail(str(error) if isinstance(error, VoiceError) else "Couldn't use the microphone.")
        return ok()

    async def voice_stop(self):
        try:
            text = await self.voice.stop()
        except (VoiceError, OSError) as error:
            return fail(str(error) if isinstance(error, VoiceError) else "Voice search failed.")
        return ok(text=text) if text else fail("Didn't catch that. Hold Y and try again.")
