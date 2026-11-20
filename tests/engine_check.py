"""On-device check of the backend (search, download, playback controls).

Run on the Legion:  python3 tests/engine_check.py <plugin_dir> <data_dir>
Plays a few seconds of a song through the speakers.
"""

import asyncio
import os
import sys
import time

plugin_dir, data_dir = sys.argv[1], sys.argv[2]
sys.path[:0] = [plugin_dir, os.path.join(plugin_dir, "py_modules")]

from cuivar import catalog  # noqa: E402
from cuivar.engine import Engine  # noqa: E402
from cuivar.player import Player  # noqa: E402


def log(ok, name, detail=""):
    print(f"{'PASS' if ok else 'FAIL'}  {name}  {detail}".rstrip(), flush=True)


async def main():
    t = time.time()
    songs = await asyncio.to_thread(catalog.search, "Tum Hi Ho Arijit Singh")
    log(len(songs) >= 5, "search", f"{len(songs)} songs in {time.time() - t:.1f}s; first: {songs[0]['title']} — {songs[0]['artists'][0]['name']}")

    t = time.time()
    station = await asyncio.to_thread(catalog.radio, songs[0]["id"])
    log(len(station) > 10, "radio", f"{len(station)} songs in {time.time() - t:.1f}s")

    engine = Engine(data_dir, os.path.join(plugin_dir, "bin", "yt-dlp_linux"),
                    js_runtime=f"quickjs:{os.path.join(plugin_dir, 'bin', 'qjs')}")
    log(bool(await engine.version()), "yt-dlp runs on SteamOS", await engine.version() or "")
    t = time.time()
    try:
        path = await engine.fetch(songs[0]["id"])
        size = os.path.getsize(path)
        kbps = size * 8 / 1000 / (songs[0]["duration"] or 1)
        log(kbps > 100, "song downloaded (complete)", f"{time.time() - t:.1f}s, {size / 1048576:.2f} MB, ~{kbps:.0f} kbps, {os.path.basename(path)}")
    except Exception as error:
        log(False, "song downloaded", f"{getattr(error, 'code', '')} {error} :: {(engine.last_error or {}).get('detail', '')[-300:]}")
        return

    ended = asyncio.Event()

    async def on_end():
        ended.set()

    player = Player(on_end)
    await player.play(path, 0, songs[0]["duration"])
    await asyncio.sleep(4)
    alive = all(p.returncode is None for p in player.procs)
    log(alive and player.playing, "playing through PipeWire", f"position {player.position():.1f}s")

    await player.pause()
    paused_at = player.position()
    await asyncio.sleep(1.5)
    log(not player.playing and abs(player.position() - paused_at) < 0.01, "pause holds position", f"{paused_at:.1f}s")

    await player.resume()
    await asyncio.sleep(2)
    log(player.playing and player.position() > paused_at + 1.5, "resume continues", f"{player.position():.1f}s")

    await player.seek(120)
    await asyncio.sleep(2)
    alive = all(p.returncode is None for p in player.procs)
    log(alive and 121 < player.position() < 124, "seek to 2:00", f"{player.position():.1f}s")

    await player.seek((songs[0]["duration"] or 200) - 2)
    try:
        await asyncio.wait_for(ended.wait(), 8)
        log(True, "end of song detected")
    except asyncio.TimeoutError:
        log(False, "end of song detected")

    await player.stop()
    log(not player.procs, "stop cleans up processes")


asyncio.run(main())
