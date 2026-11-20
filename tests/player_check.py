"""On-device check of the player alone (no YouTube): playback via ffmpeg +
PipeWire, pause, resume, seek, end-of-song and cleanup.

Run on the Legion:  python3 tests/player_check.py <plugin_dir> <song_file>
Plays a few seconds of the song through the speakers.
"""

import asyncio
import json
import os
import subprocess
import sys

plugin_dir, song = sys.argv[1], sys.argv[2]
sys.path[:0] = [plugin_dir]

from cuivar import child_env  # noqa: E402
from cuivar.player import Player  # noqa: E402


def log(ok, name, detail=""):
    print(f"{'PASS' if ok else 'FAIL'}  {name}  {detail}".rstrip(), flush=True)


def duration_of(path):
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "json", path],
                         capture_output=True, text=True, env=child_env()).stdout
    return float(json.loads(out)["format"]["duration"])


async def main():
    duration = duration_of(song)
    log(duration > 60, "song readable", f"{duration:.0f}s, {os.path.basename(song)}")

    ended = asyncio.Event()

    async def on_end():
        ended.set()

    player = Player(on_end)
    await player.play(song, 0, duration)
    await asyncio.sleep(4)
    log(all(p.returncode is None for p in player.procs) and player.playing, "playing through PipeWire", f"{player.position():.1f}s")

    before = player.position()
    await player.pause()
    paused_at = player.position()
    await asyncio.sleep(1.5)
    log(not player.playing and not player.procs and abs(paused_at - before) < 0.3 and abs(player.position() - paused_at) < 0.01,
        "pause holds the right position", f"{before:.1f}s -> {paused_at:.1f}s")

    await player.resume()
    await asyncio.sleep(2)
    log(player.playing and player.position() > paused_at + 1.5, "resume continues", f"{player.position():.1f}s")

    await player.seek(120)
    await asyncio.sleep(2)
    log(all(p.returncode is None for p in player.procs) and 121 < player.position() < 124, "seek to 2:00", f"{player.position():.1f}s")

    await player.seek(duration - 2)
    try:
        await asyncio.wait_for(ended.wait(), 8)
        log(True, "end of song detected")
    except asyncio.TimeoutError:
        log(False, "end of song detected")

    await player.stop()
    leftovers = subprocess.run(["pgrep", "-u", str(os.getuid()), "-f", "pw-cat --playback --raw"], capture_output=True, text=True).stdout.strip()
    log(not player.procs and not leftovers, "stop leaves no processes behind", leftovers)


asyncio.run(main())
