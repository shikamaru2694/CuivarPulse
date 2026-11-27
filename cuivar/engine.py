"""Downloads songs with yt-dlp into a local cache (direct YouTube stream URLs
get refused mid-song without anti-bot tokens; whole-file downloads through
yt-dlp are reliable). yt-dlp itself is set up and kept current by tools.py;
when YouTube changes something, the engine updates it and tries once more."""

import asyncio
import json
import os
import re
import time

from . import child_env
from .tools import ToolsError

VIDEO_ID = re.compile(r"^[A-Za-z0-9_-]{11}$")
CACHE_LIMIT = 1024 * 1024 * 1024  # ~220 songs
TIMEOUT = 120
# Upload titles that aren't a real music video (for pixel video).
NOT_A_VIDEO = r"(?i)lyric|letra|visuali[sz]er|\baudio\b|karaoke|slowed|reverb|sped.?up|nightcore|\b8d\b"


class EngineError(Exception):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


def classify(stderr):
    if re.search(r"confirm you.?re not a bot|Sign in to confirm|HTTP Error 429|Too Many Requests", stderr, re.I):
        # Seen on 2026-11-19: temporary block of the home connection.
        return EngineError("BLOCKED", "YouTube is busy right now. Try again in a little while.")
    if re.search(r"Video unavailable|not available|private video|removed", stderr, re.I):
        return EngineError("UNAVAILABLE", "This song is not available.")
    if re.search(r"HTTP Error 403|Requested format is not available|Unable to extract|nsig|signature", stderr, re.I):
        return EngineError("ENGINE_OUTDATED", "YouTube changed something and the music engine couldn't update itself yet. "
                                              "Try Update music engine in Settings, or try again later.")
    if re.search(r"getaddrinfo|timed out|Connection|Network", stderr, re.I):
        return EngineError("OFFLINE", "Could not reach YouTube Music.")
    return EngineError("FAILED", "This song could not be loaded.")


class Engine:
    def __init__(self, data_dir, tools):
        self.cache_dir = os.path.join(data_dir, "cache")
        self.index_file = os.path.join(data_dir, "cache.json")
        self.tools = tools  # yt-dlp and QuickJS: downloaded, checked, kept current
        os.makedirs(self.cache_dir, exist_ok=True)

        try:
            with open(self.index_file) as f:
                self.index = json.load(f)
        except (OSError, ValueError):
            self.index = {}
        self.inflight = {}
        self.last_error = None

    def _save(self):
        tmp = self.index_file + ".tmp"
        with open(tmp, "w") as f:
            json.dump(self.index, f)
        os.replace(tmp, self.index_file)

    def cached(self, video_id):
        entry = self.index.get(video_id) if VIDEO_ID.match(str(video_id)) else None
        if not entry:
            return None
        path = os.path.join(self.cache_dir, entry["file"])
        if not os.path.exists(path):
            self.index.pop(video_id, None)
            return None
        return path

    async def fetch(self, video_id):
        """Path of the song in the cache, downloading it if needed."""
        if not VIDEO_ID.match(str(video_id)):
            raise EngineError("INVALID", "Invalid song.")
        path = self.cached(video_id)
        if path:
            self.index[video_id]["used"] = time.time()
            self._save()
            return path
        if video_id not in self.inflight:
            self.inflight[video_id] = asyncio.ensure_future(self._download(video_id))
            self.inflight[video_id].add_done_callback(lambda _f: self.inflight.pop(video_id, None))
        return await asyncio.shield(self.inflight[video_id])

    def prefetch(self, video_ids):
        for video_id in video_ids[:2]:
            task = asyncio.ensure_future(self.fetch(video_id))
            # Errors and cancellation (e.g. at shutdown) are fine for pre-loading.
            task.add_done_callback(lambda t: t.cancelled() or t.exception())

    async def _run(self, args, timeout=TIMEOUT):
        try:
            await self.tools.wait_ready()  # first launch: the engine is still being set up
        except ToolsError as error:
            raise EngineError("SETUP", str(error))
        proc = await asyncio.create_subprocess_exec(
            *self.tools.command(), *args,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, env=child_env(),
        )
        try:
            out, err = await asyncio.wait_for(proc.communicate(), timeout)
        except asyncio.TimeoutError:
            proc.kill()
            raise EngineError("OFFLINE", "YouTube Music took too long to answer.")
        except asyncio.CancelledError:
            proc.kill()  # don't leave yt-dlp running when the plugin stops
            raise
        return proc.returncode, out.decode(errors="replace"), err.decode(errors="replace")

    async def _self_fixing(self, attempt):
        """Runs `attempt`; if YouTube changed something, updates yt-dlp right away
        (checked and tried first, see tools.py) and tries once more."""
        try:
            return await attempt()
        except EngineError as error:
            if error.code != "ENGINE_OUTDATED" or not await self.tools.auto_update():
                raise
        return await attempt()

    def _js(self):
        runtime = self.tools.js_runtime()
        return ["--js-runtimes", runtime] if runtime else []

    async def _download(self, video_id):
        return await self._self_fixing(lambda: self._download_once(video_id))

    async def _download_once(self, video_id):
        args = self._js()
        args += [
            "-f", "bestaudio[ext=webm]/bestaudio",
            "--no-playlist", "--no-part", "--no-mtime", "--no-progress",
            "--cache-dir", os.path.join(os.path.dirname(self.cache_dir), "yt-dlp-cache"),
            "-o", os.path.join(self.cache_dir, "%(id)s.%(ext)s"),
            "--print", "after_move:filepath",
            f"https://music.youtube.com/watch?v={video_id}",
        ]
        code, out, err = await self._run(args)
        if code != 0:
            error = classify(err)
            self.last_error = {"code": error.code, "message": str(error), "detail": err[-500:], "at": time.time()}
            raise error
        path = out.strip().splitlines()[-1] if out.strip() else ""
        if not path or not os.path.exists(path):
            raise EngineError("FAILED", "This song could not be loaded.")
        self.last_error = None
        self.index[video_id] = {"file": os.path.basename(path), "size": os.path.getsize(path), "used": time.time()}
        self._trim(video_id)
        self._save()
        return path

    async def fetch_video(self, video_id, out_dir, min_seconds, max_seconds):
        return await self._self_fixing(lambda: self._fetch_video_once(video_id, out_dir, min_seconds, max_seconds))

    async def _fetch_video_once(self, video_id, out_dir, min_seconds, max_seconds):
        """Pixel video source: the smallest video-only stream (144p) and the
        smallest audio stream (only to line the video up with the song).
        Returns (video_path, audio_path), or None when the upload is skipped:
        a lyric / audio-only / visualiser upload, an auto-generated "Topic"
        channel, or a length that can't be this song."""
        if not VIDEO_ID.match(str(video_id)):
            return None
        args = self._js() + [
            "-f", "wv[height>=144]/wv,wa",
            "--match-filters",
            f"title!~='{NOT_A_VIDEO}' & channel!~=' - Topic$' & duration>{int(min_seconds)} & duration<{int(max_seconds)}",
            "--no-playlist", "--no-part", "--no-mtime", "--no-progress", "--no-warnings",
            "--cache-dir", os.path.join(os.path.dirname(self.cache_dir), "yt-dlp-cache"),
            "-o", os.path.join(out_dir, "%(id)s.%(format_id)s.%(ext)s"),
            "--print", "after_move:filepath",
            f"https://www.youtube.com/watch?v={video_id}",
        ]
        code, out, err = await self._run(args)
        if code != 0:
            raise classify(err)
        paths = [line for line in out.strip().splitlines() if line and os.path.exists(line)]
        return (paths[0], paths[1]) if len(paths) == 2 else None

    def _trim(self, keep):
        total = sum(e["size"] for e in self.index.values())
        for video_id, entry in sorted(self.index.items(), key=lambda kv: kv[1]["used"]):
            if total <= CACHE_LIMIT:
                break
            if video_id == keep:
                continue
            try:
                os.remove(os.path.join(self.cache_dir, entry["file"]))
            except OSError:
                pass
            total -= entry["size"]
            self.index.pop(video_id, None)

    def shutdown(self):
        """Cancels downloads in progress (their yt-dlp processes are killed)."""
        for task in list(self.inflight.values()):
            task.cancel()

    def release(self, video_id):
        """Hands a cached file over (e.g. to downloads): forgets it without deleting."""
        path = self.cached(video_id)
        self.index.pop(video_id, None)
        self._save()
        return path

    def cache_bytes(self):
        return sum(e["size"] for e in self.index.values())

    def clear(self, keep=()):
        for video_id, entry in list(self.index.items()):
            if video_id in keep:
                continue
            try:
                os.remove(os.path.join(self.cache_dir, entry["file"]))
            except OSError:
                pass
            self.index.pop(video_id, None)
        self._save()

    async def version(self):
        if self.tools.version:
            return self.tools.version
        if not self.tools.ready:
            return None
        code, out, _err = await self._run(["--version"], 30)
        return out.strip() if code == 0 else None

    async def update(self):
        """The manual "Update music engine" button: same checked update as the
        automatic ones (the working version stays if anything fails)."""
        try:
            message = await self.tools.update("button")
        except ToolsError as error:
            raise EngineError("FAILED", str(error))
        self.last_error = None
        return message
