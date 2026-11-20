"""Audio playback without extra software: ffmpeg (already on SteamOS) decodes
the cached song and pw-cat streams it into PipeWire, the Legion's sound
system. Pause and seek restart the pipeline at the remembered position,
which keeps the player simple and robust."""

import asyncio
import json
import os
import signal

from . import child_env

RATE = 48000
# Our streams carry this name so the music volume can find exactly them.
STREAM_NAME = "Cuivar Pulse"


class Player:
    def __init__(self, on_end):
        self.on_end = on_end  # async callback when a song finishes by itself
        self.file = None
        self.offset = 0.0  # position where the current pipeline started
        self.started = None  # loop time when it started (None = not running)
        self.duration = None
        self.procs = []
        self.monitor = None
        self.generation = 0
        self.volume = 100  # music-only volume, 0-100 (system volume is separate)
        self._volume_busy = False
        self._volume_dirty = False
        # play / pause / resume / seek / stop run one at a time: starting a
        # stream awaits PipeWire, and two starts must never interleave.
        self._lock = asyncio.Lock()

    @property
    def playing(self):
        return self.started is not None

    def position(self):
        if self.started is None:
            return self.offset
        return self.offset + (asyncio.get_running_loop().time() - self.started)

    async def play(self, path, start=0.0, duration=None):
        async with self._lock:
            return await self._play_locked(path, start, duration)

    async def _play_locked(self, path, start=0.0, duration=None):
        await self._kill()
        self.file = path
        self.duration = duration
        await self._spawn(max(0.0, float(start)))

    async def pause(self):
        async with self._lock:
            return await self._pause_locked()

    async def _pause_locked(self):
        if self.started is None:
            return
        await self._kill()  # records the position exactly once

    async def resume(self):
        async with self._lock:
            return await self._resume_locked()

    async def _resume_locked(self):
        if self.file and self.started is None:
            await self._spawn(self.offset)

    async def toggle(self):
        if self.started is None:
            await self.resume()
        else:
            await self.pause()

    async def seek(self, seconds):
        async with self._lock:
            return await self._seek_locked(seconds)

    async def _seek_locked(self, seconds):
        if not self.file:
            return
        target = max(0.0, float(seconds))
        if self.duration:
            target = min(target, max(0.0, self.duration - 1))
        if self.started is None:
            self.offset = target
        else:
            await self._kill()
            await self._spawn(target)

    async def stop(self):
        async with self._lock:
            return await self._stop_locked()

    async def _stop_locked(self):
        await self._kill()
        self.file = None
        self.offset = 0.0

    async def set_volume(self, percent):
        """Music-only volume. New songs start at it; the playing stream changes live."""
        self.volume = int(min(max(round(float(percent)), 0), 100))
        if self.started is None:
            return
        # A slider drag sends many values. Apply one at a time and always the
        # latest, so a slow pactl call can't land after a newer value.
        if self._volume_busy:
            self._volume_dirty = True
            return
        self._volume_busy = True
        try:
            while True:
                self._volume_dirty = False
                await self._apply_volume(self.volume)
                if not self._volume_dirty:
                    break
        finally:
            self._volume_busy = False

    async def _streams(self):
        """PipeWire indexes of our playing stream(s)."""
        listing = await asyncio.create_subprocess_exec(
            "pactl", "-f", "json", "list", "sink-inputs",
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL, env=child_env(),
        )
        try:
            # pactl can stall briefly (e.g. right after a restart); never let
            # that hold up playback.
            out, _ = await asyncio.wait_for(listing.communicate(), 1.5)
        except asyncio.TimeoutError:
            listing.kill()
            return []
        try:
            streams = json.loads(out or b"[]")
        except ValueError:
            return []
        return [s["index"] for s in streams
                if (s.get("properties") or {}).get("application.name") == STREAM_NAME]

    async def duck(self, factor):
        """Turn the music down to `factor` of its volume for a moment (voice
        search listening), or back to normal with None. The volume setting
        itself doesn't change."""
        level = self.volume if factor is None else int(self.volume * factor)
        await self._apply_volume(level)

    async def output_delay(self):
        """Seconds between our stream and the sound reaching the ears, as
        PipeWire reports it: a few ms on the speakers, ~0.2 s on Bluetooth.
        (pactl always says 0 here, so this asks PipeWire directly.)"""
        dump = await asyncio.create_subprocess_exec(
            "pw-dump", stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL, env=child_env(),
        )
        try:
            out, _ = await asyncio.wait_for(dump.communicate(), 2.0)
            objects = json.loads(out or b"[]")
        except (asyncio.TimeoutError, ValueError):
            if dump.returncode is None:
                dump.kill()
            return None
        delays = []
        for item in objects:
            info = item.get("info") or {}
            if (info.get("props") or {}).get("application.name") != STREAM_NAME:
                continue
            for latency in (info.get("params") or {}).get("Latency") or []:
                # "Input" = everything downstream of our stream.
                if latency.get("direction") == "Input":
                    delays.append(latency.get("minNs", 0) / 1e9)
        return min(max(max(delays), 0.0), 0.5) if delays else None

    async def _apply_volume(self, percent, indexes=None):
        # The stream's own volume is the ONLY volume control we use (pactl's
        # percent is perceptual, matching Steam's sliders).
        for index in indexes if indexes is not None else await self._streams():
            setter = await asyncio.create_subprocess_exec(
                "pactl", "set-sink-input-volume", str(index), f"{percent}%",
                stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL, env=child_env(),
            )
            try:
                await asyncio.wait_for(setter.wait(), 1.5)
            except asyncio.TimeoutError:
                setter.kill()

    async def _spawn(self, start):
        env = child_env()
        read_fd, write_fd = os.pipe()
        # pw-cat first, with no audio yet, so the new stream can be set to the
        # slider's volume before a single sample plays.
        pwcat = await asyncio.create_subprocess_exec(
            "pw-cat", "--playback", "--raw", "--format", "s16", "--rate", str(RATE), "--channels", "2",
            "--media-role", "Music",
            # state.restore-props = false: stop WirePlumber re-applying an old
            # saved volume to every new stream (song change, pause, seek).
            "-P", f'{{ application.name = "{STREAM_NAME}" media.name = "{STREAM_NAME}" state.restore-props = false }}', "-",
            stdin=read_fd, stderr=asyncio.subprocess.DEVNULL, env=env, start_new_session=True,
        )
        os.close(read_fd)
        loop = asyncio.get_running_loop()
        deadline = loop.time() + 2.0
        while loop.time() < deadline and pwcat.returncode is None:
            indexes = await self._streams()
            if indexes:
                await self._apply_volume(self.volume, indexes)
                break
            await asyncio.sleep(0.04)
        ffmpeg = await asyncio.create_subprocess_exec(
            "ffmpeg", "-nostdin", "-loglevel", "error", "-ss", f"{start:.2f}", "-i", self.file,
            "-f", "s16le", "-ac", "2", "-ar", str(RATE), "-",
            stdout=write_fd, stderr=asyncio.subprocess.DEVNULL, env=env, start_new_session=True,
        )
        os.close(write_fd)

        self.procs = [ffmpeg, pwcat]
        self.offset = start
        self.started = asyncio.get_running_loop().time()
        self.generation += 1
        self.monitor = asyncio.ensure_future(self._watch(pwcat, self.generation))

    async def _watch(self, pwcat, generation):
        await pwcat.wait()
        # Only a pipeline that ended by itself (not killed by pause/seek/stop)
        # counts as the song finishing.
        if generation == self.generation and self.started is not None:
            self.offset = self.position()
            self.started = None
            self.procs = []
            await self.on_end()

    async def _kill(self):
        self.generation += 1
        if self.started is not None:
            self.offset = self.position()
        self.started = None
        for proc in self.procs:
            if proc.returncode is None:
                try:
                    os.killpg(proc.pid, signal.SIGTERM)
                except ProcessLookupError:
                    pass
        for proc in self.procs:
            try:
                await asyncio.wait_for(proc.wait(), 2)
            except asyncio.TimeoutError:
                try:
                    os.killpg(proc.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
        self.procs = []
