"""Pixel video: the song's music video as chunky pixel art.

For the current song, only while the panel is showing it:
  1. find the music video (an upload by the song's own artist, see
     catalog.video_candidates; lyric, audio-only and visualiser uploads are
     skipped by their real title, see engine.fetch_video),
  2. fetch its smallest streams (144p video, tiny audio),
  3. line it up with the song by matching the bass of both (music videos
     often have an intro, so the video can start well before the song),
  4. decode it into tiny videos (64 px wide for the panel, 96 for fullscreen,
     12 fps; the panel draws
     it in the chosen pixel style, e.g. a retro palette), already
     shifted so its time = the song's time, plus one colour per frame for the
     "Video" light colours.
A still image (frames that barely change) counts as no video. The panel draws
the result scaled up with blocky pixels and keeps it on the song's position.
"""

import array
import asyncio
import base64
import colorsys
import json
import math
import os
import shutil
import subprocess
import time

from . import catalog, child_env
from .engine import EngineError

FPS = 12
# Made together for every song: Medium for the panel, Fine for fullscreen.
PANEL_SIZE, FULLSCREEN_SIZE = 64, 96
SIZES = (PANEL_SIZE, FULLSCREEN_SIZE)
CACHE_LIMIT = 200 * 1024 * 1024   # pixel videos the user didn't keep with a download
WATCH_GRACE = 8                   # seconds without the panel watching before work stops
RETRY_AFTER = 14 * 86400          # a song without a usable video is looked up again after this
MIN_MATCH = 0.45                  # how well the video's bass has to line up with the song's
STILL = 3.0                       # average change between frames 1 s apart (0-255): below = still image
MAX_SHIFT = 60                    # seconds the video may start before / after the song


def _envelope(path, fps=10):
    """Bass loudness, `fps` values per second, normalised (mean 0, sd 1)."""
    raw = subprocess.run(
        ["ffmpeg", "-nostdin", "-v", "error", "-i", path, "-af", "lowpass=f=180", "-ac", "1", "-ar", "6000",
         "-f", "s16le", "-"], capture_output=True, env=child_env(), timeout=120).stdout
    samples = array.array("h")
    samples.frombytes(raw[: len(raw) - len(raw) % 2])
    window = 6000 // fps
    values = [math.sqrt(sum(x * x for x in samples[i:i + window]) / window)
              for i in range(0, len(samples) - window + 1, window)]
    if len(values) < fps * 5:
        return []
    mean = sum(values) / len(values)
    spread = math.sqrt(sum((v - mean) ** 2 for v in values) / len(values)) or 1.0
    return [(v - mean) / spread for v in values]


def align(song_path, video_audio_path):
    """(offset, score): the video's time = the song's time + offset (seconds).
    Score is the correlation of the two bass envelopes (1 = identical)."""
    song, video = _envelope(song_path), _envelope(video_audio_path)
    if not song or not video:
        return 0.0, 0.0
    best = (-1.0, 0)
    for shift in range(-MAX_SHIFT * 10, MAX_SHIFT * 10 + 1):
        lo, hi = max(0, -shift), min(len(song), len(video) - shift)
        if hi - lo < len(song) * 0.5:
            continue
        total = sum(song[i] * video[i + shift] for i in range(lo, hi))
        score = total / (hi - lo)
        if score > best[0]:
            best = (score, shift)
    return best[1] / 10.0, best[0]


def frame_colours(raw):
    """One light colour per frame from 4x4-pixel frames: the most vivid of the
    16 cells (a plain average of a video frame is usually a muddy grey)."""
    colours = []
    for start in range(0, len(raw) - 47, 48):
        best, best_score = None, -1.0
        for cell in range(16):
            r, g, b = raw[start + cell * 3: start + cell * 3 + 3]
            _, s, v = colorsys.rgb_to_hsv(r / 255, g / 255, b / 255)
            if s * v > best_score:
                best, best_score = (r, g, b), s * v
        colours.append(list(best))
    return colours


def is_still(raw):
    """True when frames 1 s apart barely differ (a still image or a black screen)."""
    frames = [raw[i:i + 48] for i in range(0, len(raw) - 47, 48)]
    pairs = [(frames[i], frames[i + FPS]) for i in range(0, len(frames) - FPS, FPS)]
    if not pairs:
        return True
    change = sum(sum(abs(a - b) for a, b in zip(x, y)) / 48 for x, y in pairs) / len(pairs)
    return change < STILL


class PixelVideo:
    def __init__(self, engine, data_dir, logger=None):
        self.engine = engine
        self.logger = logger
        self.dir = os.path.join(data_dir, "pixel")
        self.index_file = os.path.join(self.dir, "index.json")
        os.makedirs(self.dir, exist_ok=True)
        try:
            with open(self.index_file) as f:
                self.index = json.load(f)
        except (OSError, ValueError):
            self.index = {}
        self.jobs = {}        # song id -> task
        self.phase = {}       # song id -> "finding" / "fetching" / "preparing"
        self.watched = {}     # song id -> when it was last wanted (keeps its job alive)
        self.viewer_at = 0.0  # when the panel (TV mode, on screen) last asked
        self.kept_jobs = set()  # jobs for "save with downloads": not stopped when nobody watches
        self.lock = asyncio.Lock()  # one job at a time
        self.watchdog = None
        self._colour_cache = (None, None)
        # Work folders left over from a run that was stopped mid-job.
        for name in os.listdir(self.dir):
            if name.startswith("work-"):
                shutil.rmtree(os.path.join(self.dir, name), ignore_errors=True)

    def _log(self, text):
        if self.logger:
            self.logger.info(f"pixel video: {text}")

    def _save(self):
        tmp = self.index_file + ".tmp"
        with open(tmp, "w") as f:
            json.dump(self.index, f)
        os.replace(tmp, self.index_file)

    def _file(self, song_id, size):
        return os.path.join(self.dir, f"{song_id}.{size}.webm")

    def _colours_file(self, song_id):
        return os.path.join(self.dir, f"{song_id}.colours.json")

    # --- what the panel asks ---------------------------------------------------------

    def status(self, song_id):
        entry = self.index.get(song_id) or {}
        if entry.get("state") == "ready" and all(os.path.exists(self._file(song_id, size)) for size in SIZES):
            return {"state": "ready"}
        if song_id in self.jobs:
            return {"state": self.phase.get(song_id, "finding")}
        if entry.get("state") == "none" and time.time() - entry.get("at", 0) < RETRY_AFTER:
            return {"state": "none"}
        return {"state": "idle"}

    def viewer_seen(self):
        self.viewer_at = time.monotonic()

    def viewer_present(self):
        """Is someone looking at TV mode right now (panel open or fullscreen TV)?"""
        return time.monotonic() - self.viewer_at < WATCH_GRACE

    def cancel_others(self, wanted):
        """Stop work on songs that aren't the current or next one any more."""
        for other, task in list(self.jobs.items()):
            if other not in wanted and other not in self.kept_jobs:
                task.cancel()

    def watch(self, track, song_path, keep=False, by_panel=False):
        """This song's pixel video is wanted (on screen, coming up next, or a
        download wants it saved): start preparing it if needed. Returns its status."""
        song_id = track["id"]
        self.watched[song_id] = time.monotonic()
        if by_panel:
            self.viewer_seen()
        current = self.status(song_id)
        if keep and current["state"] == "ready":
            self.set_kept(song_id, True)
        if current["state"] == "idle" and song_path and os.path.exists(song_path):
            task = asyncio.ensure_future(self._job(track, song_path, keep))
            self.jobs[song_id] = task
            if keep:
                self.kept_jobs.add(song_id)
            task.add_done_callback(lambda _t, s=song_id: self._job_done(s))
            current = {"state": "finding"}
        elif keep and song_id in self.jobs:
            self.kept_jobs.add(song_id)
        if self.jobs and (self.watchdog is None or self.watchdog.done()):
            self.watchdog = asyncio.ensure_future(self._watchdog())
        return current

    def _job_done(self, song_id):
        self.jobs.pop(song_id, None)
        self.phase.pop(song_id, None)
        self.kept_jobs.discard(song_id)

    async def _watchdog(self):
        """Stop work nobody is looking at any more (panel closed, game started)."""
        while self.jobs:
            await asyncio.sleep(2)
            now = time.monotonic()
            for song_id, task in list(self.jobs.items()):
                if song_id not in self.kept_jobs and now - self.watched.get(song_id, 0) > WATCH_GRACE:
                    self._log(f"stopped {song_id} (no longer on screen)")
                    task.cancel()

    def read(self, song_id, size):
        """The pixel video file, base64-encoded for the panel."""
        path = self._file(song_id, size)
        if not os.path.exists(path):
            return None
        entry = self.index.get(song_id)
        if entry:
            entry["used"] = time.time()
            self._save()
        with open(path, "rb") as f:
            return base64.b64encode(f.read()).decode("ascii")

    def colours(self, song_id):
        """Light colours for the song (one per 1/12 s, on the song's time), or None."""
        if self._colour_cache[0] == song_id:
            return self._colour_cache[1]
        try:
            with open(self._colours_file(song_id)) as f:
                colours = json.load(f)
        except (OSError, ValueError):
            colours = None
        if colours is not None or (self.index.get(song_id) or {}).get("state") != "ready":
            self._colour_cache = (song_id, colours)
        return colours

    # --- downloads, cache ---------------------------------------------------------------

    def set_kept(self, song_id, kept):
        entry = self.index.get(song_id)
        if entry and entry.get("state") == "ready":
            entry["keep"] = bool(kept)
            self._save()
            if not kept:
                self._trim()

    def bytes(self, kept=None):
        total = 0
        for song_id, entry in self.index.items():
            if kept is not None and bool(entry.get("keep")) != kept:
                continue
            for size in entry.get("sizes", []):
                path = self._file(song_id, size)
                if os.path.exists(path):
                    total += os.path.getsize(path)
        return total

    def _forget(self, song_id):
        entry = self.index.pop(song_id, None) or {}
        for size in entry.get("sizes", []):
            try:
                os.remove(self._file(song_id, size))
            except OSError:
                pass
        try:
            os.remove(self._colours_file(song_id))
        except OSError:
            pass
        if self._colour_cache[0] == song_id:
            self._colour_cache = (None, None)

    def _trim(self):
        """Keep the automatic pixel cache under CACHE_LIMIT (oldest-watched first)."""
        loose = sorted(((e.get("used", 0), song_id) for song_id, e in self.index.items()
                        if e.get("state") == "ready" and not e.get("keep")))
        while loose and self.bytes(kept=False) > CACHE_LIMIT:
            self._forget(loose.pop(0)[1])
        self._save()

    def clear(self, keep=()):
        """Clear cache: drops pixel videos not saved with a download, and forgets
        "no video" answers so those songs are looked up again."""
        for song_id, entry in list(self.index.items()):
            if song_id in keep or entry.get("keep") or song_id in self.jobs:
                continue
            self._forget(song_id)
        self._save()

    def shutdown(self):
        for task in list(self.jobs.values()):
            task.cancel()
        if self.watchdog:
            self.watchdog.cancel()

    # --- the work ---------------------------------------------------------------------

    async def _job(self, track, song_path, keep):
        song_id = track["id"]
        async with self.lock:
            work = os.path.join(self.dir, f"work-{song_id}")
            shutil.rmtree(work, ignore_errors=True)
            os.makedirs(work)
            try:
                await self._prepare(track, song_path, keep, work)
            except EngineError as error:
                # YouTube busy / offline: try again next time, don't remember "no video".
                self._log(f"{song_id}: {error.code}")
            except asyncio.CancelledError:
                raise
            except Exception as error:  # noqa: BLE001 - a broken video never breaks playback
                self._log(f"{song_id} failed: {error}")
            finally:
                shutil.rmtree(work, ignore_errors=True)

    async def _prepare(self, track, song_path, keep, work):
        song_id = track["id"]
        duration = await asyncio.to_thread(self._duration, song_path) or track.get("duration") or 240
        entry = self.index.get(song_id) or {}
        if entry.get("state") == "ready" and entry.get("video"):
            choices = [(entry["video"], entry.get("offset", 0.0))]   # known video, missing a size
        else:
            self.phase[song_id] = "finding"
            choices = [(vid, None) for vid in await asyncio.to_thread(catalog.video_candidates, track)]
        for video_id, offset in choices:
            self.phase[song_id] = "fetching"
            got = await self.engine.fetch_video(video_id, work, duration * 0.7, duration * 1.6 + 60)
            if not got:
                self._log(f"{song_id}: {video_id} skipped (not a music video)")
                continue
            video, audio = got
            self.phase[song_id] = "preparing"
            if offset is None:
                offset, score = await asyncio.to_thread(align, song_path, audio)
                if score < MIN_MATCH:
                    self._log(f"{song_id}: {video_id} doesn't match the song (score {score:.2f})")
                    continue
            outs = {size: os.path.join(work, f"pixel.{size}.webm") for size in SIZES}
            raw = await self._decode(video, offset, duration, outs)
            if is_still(raw):
                self._log(f"{song_id}: {video_id} is a still image")
                continue
            for size, path in outs.items():
                os.replace(path, self._file(song_id, size))
            with open(self._colours_file(song_id), "w") as f:
                json.dump(frame_colours(raw), f)
            self._colour_cache = (None, None)
            self.index[song_id] = {
                "state": "ready", "video": video_id, "offset": round(offset, 2),
                "sizes": list(SIZES),
                "used": time.time(), "keep": bool(keep or entry.get("keep")),
            }
            self._save()
            self._trim()
            self._log(f"{song_id}: ready from {video_id} (offset {offset:+.1f}s)")
            return
        self.index[song_id] = {"state": "none", "at": time.time()}
        self._save()
        self._log(f"{song_id}: no usable music video")

    @staticmethod
    def _duration(path):
        try:
            out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", path],
                                 capture_output=True, text=True, env=child_env(), timeout=20).stdout
            return float(out.strip())
        except (ValueError, OSError, subprocess.SubprocessError):
            return None

    async def _decode(self, video, offset, duration, outs):
        """Write the pixel video at each size in `outs` ({width: path}, time
        shifted to the song's) and return its frames at 4x4 pixels (for the light
        colours and the still-image check). One decode feeds every output."""
        if offset >= 0:
            source, pad = ["-ss", f"{offset:.2f}", "-i", video], ""
        else:  # the song starts before the video: black until it begins
            source, pad = ["-i", video], f"tpad=start_duration={-offset:.2f}:color=black,"
        sizes = sorted(outs)
        branches = "".join(f"[b{i}]" for i in range(len(sizes)))
        graph = f"[0:v]{pad}fps={FPS},split={len(sizes) + 1}{branches}[c];"
        for i, width in enumerate(sizes):
            height = width * 9 // 16
            graph += (f"[b{i}]scale={width}:{height}:force_original_aspect_ratio=increase:flags=area,"
                      f"crop={width}:{height},format=yuv444p[v{i}];")
        graph += "[c]scale=4:4:flags=area,format=rgb24[s]"
        length = ["-t", f"{duration:.2f}"]
        outputs = []
        for i, width in enumerate(sizes):
            outputs += ["-map", f"[v{i}]", "-an", *length, "-c:v", "libvpx-vp9", "-crf", "24", "-b:v", "0",
                        "-row-mt", "1", "-deadline", "good", "-cpu-used", "5", "-g", "120", outs[width]]
        process = await asyncio.create_subprocess_exec(
            "nice", "-n", "19", "ffmpeg", "-nostdin", "-v", "error", "-y", *source, "-filter_complex", graph,
            *outputs, "-map", "[s]", *length, "-f", "rawvideo", "-",
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, env=child_env(),
        )
        try:
            raw, err = await asyncio.wait_for(process.communicate(), 300)
        except (asyncio.TimeoutError, asyncio.CancelledError):
            process.kill()
            await process.wait()
            raise
        if process.returncode != 0 or not all(os.path.exists(path) for path in outs.values()):
            raise RuntimeError(f"decode failed: {err.decode(errors='replace')[-300:]}")
        return raw
