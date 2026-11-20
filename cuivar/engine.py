"""Downloads songs with yt-dlp into a local cache (same approach as Cuivar
Music on Windows: direct YouTube stream URLs get refused mid-song without
anti-bot tokens; whole-file downloads through yt-dlp are reliable)."""

import asyncio
import json
import os
import re
import shutil
import time

from . import child_env

VIDEO_ID = re.compile(r"^[A-Za-z0-9_-]{11}$")
CACHE_LIMIT = 1024 * 1024 * 1024  # ~220 songs
TIMEOUT = 120


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
        return EngineError("ENGINE_OUTDATED", "YouTube changed something. The music engine needs an update.")
    if re.search(r"getaddrinfo|timed out|Connection|Network", stderr, re.I):
        return EngineError("OFFLINE", "Could not reach YouTube Music.")
    return EngineError("FAILED", "This song could not be loaded.")


class Engine:
    def __init__(self, data_dir, bundled_ytdlp, js_runtime=None):
        self.cache_dir = os.path.join(data_dir, "cache")
        self.index_file = os.path.join(data_dir, "cache.json")
        # yt-dlp runs from the plugin's data folder (owned by the user), so it
        # can update itself; a store install puts the plugin in a root folder.
        self.bundled = bundled_ytdlp
        self.ytdlp = os.path.join(data_dir, "bin", "yt-dlp_linux")
        if not os.path.exists(self.ytdlp) and os.path.exists(bundled_ytdlp):
            os.makedirs(os.path.dirname(self.ytdlp), exist_ok=True)
            shutil.copy2(bundled_ytdlp, self.ytdlp)
            os.chmod(self.ytdlp, 0o755)
        self.js_runtime = js_runtime
        os.makedirs(self.cache_dir, exist_ok=True)

        try:
            with open(self.index_file) as f:
                self.index = json.load(f)
        except (OSError, ValueError):
            self.index = {}
        self.inflight = {}
        self.last_error = None

    @staticmethod
    def runnable_copy(bundled, data_dir):
        """A copy of a bundled program in the data folder, marked runnable.
        Plugin ZIPs don't keep Linux 'executable' permissions, and a store
        install's plugin folder belongs to root, so we can't fix it in place.
        Refreshed whenever the bundled file changes (plugin updates)."""
        target = os.path.join(data_dir, "bin", os.path.basename(bundled))
        if not os.path.exists(bundled):
            return bundled
        try:
            fresh = (not os.path.exists(target)
                     or os.path.getsize(target) != os.path.getsize(bundled)
                     or os.path.getmtime(target) < os.path.getmtime(bundled))
            if fresh:
                os.makedirs(os.path.dirname(target), exist_ok=True)
                shutil.copy2(bundled, target)
            if not os.access(target, os.X_OK):
                os.chmod(target, 0o755)
            return target
        except OSError:
            return bundled

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
        proc = await asyncio.create_subprocess_exec(
            self.ytdlp, *args,
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

    async def _download(self, video_id):
        args = []
        if self.js_runtime:
            args += ["--js-runtimes", self.js_runtime]
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

    async def version(self, binary=None):
        if binary:
            proc = await asyncio.create_subprocess_exec(
                binary, "--version", stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL, env=child_env())
            out, _ = await proc.communicate()
            return out.decode().strip() if proc.returncode == 0 else None
        code, out, _err = await self._run(["--version"], 30)
        return out.strip() if code == 0 else None

    async def adopt_newer_bundled(self):
        """After a plugin update, use its yt-dlp if it's newer than our copy.
        (Versions are dates, e.g. 2026.10.26, so they compare as text.)"""
        if not os.path.exists(self.bundled):
            return
        mine, bundled = await self.version(self.ytdlp), await self.version(self.bundled)
        if bundled and (not mine or bundled > mine):
            shutil.copy2(self.bundled, self.ytdlp)
            os.chmod(self.ytdlp, 0o755)

    async def update(self):
        """Self-update yt-dlp (the fix when YouTube changes something)."""
        code, out, err = await self._run(["-U"], 180)
        lines = (out + err).strip().splitlines()
        message = next((l for l in reversed(lines) if re.search(r"Updated yt-dlp|up to date|ERROR", l)), lines[-1] if lines else "")
        if code != 0:
            raise EngineError("FAILED", f"Update failed: {message or 'unknown error'}")
        self.last_error = None
        return message
