"""On-device check of the backend (main.py) with a stand-in for Decky.

Run on the device:
    python3 tests/plugin_check.py <plugin_dir>            # no YouTube traffic at all
    python3 tests/plugin_check.py <plugin_dir> --online   # + one search, one download,
                                                          #   one region search, engine update

The default run makes its own test songs with ffmpeg, so it can be repeated as
often as you like without YouTube rate-limiting your connection. Uses
throwaway folders in /tmp and a stand-in for the controller lights: your real
library, settings and lighting are not touched.
"""

import asyncio
import base64
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import types

plugin_dir = sys.argv[1]
online = "--online" in sys.argv
work = tempfile.mkdtemp(prefix="cuivar-check-")
events = []


async def emit(event, *args):
    events.append((event, args))


fake = types.ModuleType("decky")
fake.DECKY_PLUGIN_DIR = plugin_dir
fake.DECKY_PLUGIN_RUNTIME_DIR = os.path.join(work, "data")
fake.DECKY_PLUGIN_SETTINGS_DIR = os.path.join(work, "settings")
fake.emit = emit


class Log:
    def info(self, *a): pass
    def exception(self, *a): print("EXCEPTION", *a)


fake.logger = Log()
os.makedirs(fake.DECKY_PLUGIN_RUNTIME_DIR)
os.makedirs(fake.DECKY_PLUGIN_SETTINGS_DIR)
sys.modules["decky"] = fake
sys.path[:0] = [plugin_dir, os.path.join(plugin_dir, "py_modules")]

from cuivar import child_env, lights  # noqa: E402

# Stand-in controller lights (same files as the real sysfs LED).
LED = os.path.join(work, "led")
os.makedirs(LED)
USER_LIGHTING = {"effect": "rainbow", "multi_intensity": "1 90 100", "brightness": "2"}
for name, value in USER_LIGHTING.items():
    with open(os.path.join(LED, name), "w") as f:
        f.write(value)
lights.LED = LED


def led():
    return {name: open(os.path.join(LED, name)).read().strip() for name in USER_LIGHTING}


import main  # noqa: E402
from cuivar import tools as tools_mod  # noqa: E402

failed = False

# --- a stand-in for GitHub's releases (offline runs) -------------------------------------
# A fake yt-dlp: a small Python program that prints its version and passes (or
# fails) the "try it on a YouTube video" check.
FAKE_YTDLP = '''import sys
if "--version" in sys.argv:
    print({version!r})
    sys.exit(0)
sys.exit({broken})
'''
FAKE_QJS = b"#!/bin/sh\necho fake quickjs\n"
fake_github = {"latest": "2026.01.01", "releases": {}, "offline": False}


def fake_release(tag, broken=False, wrong_checksum=False):
    program = FAKE_YTDLP.format(version=tag, broken=1 if broken else 0).encode()
    checksum = tools_mod.sha256(b"something else" if wrong_checksum else program)
    fake_github["releases"][tag] = {"yt-dlp": program, "yt-dlp_linux": program,
                                    "SHA2-256SUMS": f"{checksum}  yt-dlp\n{checksum}  yt-dlp_linux\n".encode()}


def fake_fetch(url, progress=None, timeout=60):
    if fake_github["offline"]:
        raise ConnectionError("no internet (test)")
    if url == tools_mod.QJS["url"]:
        return FAKE_QJS
    tag, asset = url.rsplit("/", 2)[-2:]
    return fake_github["releases"][tag][asset]


def fake_latest():
    if fake_github["offline"]:
        raise ConnectionError("no internet (test)")
    return fake_github["latest"]


def use_fake_github():
    fake_release("2026.01.01")
    tools_mod.fetch = fake_fetch
    tools_mod.latest_ytdlp_tag = fake_latest
    tools_mod.QJS = dict(tools_mod.QJS, sha256=tools_mod.sha256(FAKE_QJS))


def log(ok, name, detail=""):
    global failed
    failed |= not ok
    print(f"{'PASS' if ok else 'FAIL'}  {name}  {detail}".rstrip(), flush=True)


async def wait_for(check, timeout):
    end = time.time() + timeout
    while time.time() < end:
        if check():
            return True
        await asyncio.sleep(0.2)
    return False


def make_song(path, seconds=30, drop_at=None):
    """A 120 BPM test song: bass thump + audible tick on every beat.
    With drop_at, it plays quietly until then and drops in at full volume."""
    level = f"(0.12+0.88*gte(t,{drop_at}))*" if drop_at else ""
    beat = level + "(0.6*sin(2*PI*55*t)*exp(-mod(t,0.5)*25)*lt(mod(t,0.5),0.2)+0.4*sin(2*PI*1500*t)*exp(-mod(t,0.5)*120)*lt(mod(t,0.5),0.04))"
    subprocess.run(["ffmpeg", "-nostdin", "-loglevel", "error", "-y", "-f", "lavfi",
                    "-i", f"aevalsrc='{beat}|{beat}':s=48000:d={seconds}", "-c:a", "libopus", path],
                   check=True, env=child_env())


def make_pixel_sources(folder):
    """A song with an irregular beat (so it can only line up one way), a moving
    "music video" whose sound starts 5 s later, and a still-image upload."""
    beat = ("(0.55+0.45*sin(0.37*{t})*sin(1.3*{t}+1))*(0.7*sin(2*PI*55*{t})*exp(-mod({t},0.5)*25)"
            "*lt(mod({t},0.5),0.2))")
    song = os.path.join(folder, "pixel-song.webm")
    late = f"gte(t,5)*{beat.format(t='(t-5)')}"
    clips = {
        song: ["-f", "lavfi", "-i", f"aevalsrc='{beat.format(t='t')}':s=48000:d=30", "-c:a", "libopus"],
        os.path.join(folder, "moving.audio.webm"): ["-f", "lavfi", "-i", f"aevalsrc='{late}':s=48000:d=36", "-c:a", "libopus"],
        os.path.join(folder, "moving.video.mp4"): ["-f", "lavfi", "-i", "testsrc2=s=426x240:r=25:d=36", "-pix_fmt", "yuv420p"],
        os.path.join(folder, "still.video.mp4"): ["-f", "lavfi", "-i", "color=c=0x3366cc:s=426x240:r=25:d=36", "-pix_fmt", "yuv420p"],
    }
    for path, args in clips.items():
        subprocess.run(["ffmpeg", "-nostdin", "-loglevel", "error", "-y", *args, path], check=True, env=child_env())
    return song


async def pixel_video_checks(plugin, work):
    from cuivar import catalog as catalog_mod, video as video_mod
    folder = os.path.join(work, "pixel-src")
    os.makedirs(folder)
    song = make_pixel_sources(folder)
    sources = {
        "MOVINGVIDEO": (os.path.join(folder, "moving.video.mp4"), os.path.join(folder, "moving.audio.webm")),
        "STILLVIDEO0": (os.path.join(folder, "still.video.mp4"), os.path.join(folder, "moving.audio.webm")),
    }
    fetched = []

    async def fake_fetch(video_id, out_dir, min_seconds, max_seconds):
        fetched.append(video_id)
        if video_id == "SLOWVIDEO00":
            await asyncio.sleep(60)  # stands in for a long download
        if video_id not in sources:
            return None              # "lyric video": skipped by its title
        copies = []
        for path in sources[video_id]:
            copies.append(os.path.join(out_dir, os.path.basename(path)))
            shutil.copy(path, copies[-1])
        return tuple(copies)

    candidates = {}
    real_candidates, catalog_mod.video_candidates = catalog_mod.video_candidates, lambda t, limit=3: candidates[t["id"]]
    real_fetch, plugin.engine.fetch_video = plugin.engine.fetch_video, fake_fetch
    pixel = plugin.video
    try:
        # Lyric video first, then the real one: skips the first, lines up the second.
        candidates["PIXELSONG01"] = ["LYRICVIDEO0", "MOVINGVIDEO"]
        song_track = {"id": "PIXELSONG01", "title": "Pixel test", "artists": [{"name": "Test"}], "duration": 30}
        first = pixel.watch(song_track, song)
        ready = await wait_for(lambda: pixel.watch(song_track, song)["state"] == "ready", 90)
        entry = pixel.index.get("PIXELSONG01", {})
        log(first["state"] == "finding" and ready and fetched[:2] == ["LYRICVIDEO0", "MOVINGVIDEO"],
            "pixel video: skips the lyric video, prepares the music video", f"tried {fetched}")
        log(abs(entry.get("offset", 0) - 5.0) < 0.3, "pixel video: lined up with the song by its beat",
            f"video starts {entry.get('offset')}s before the song (expected 5.0)")
        sizes_kb = {size: len(base64.b64decode(pixel.read("PIXELSONG01", size) or "")) / 1024 for size in video_mod.SIZES}
        log(all(0 < kb < 1000 for kb in sizes_kb.values()), "pixel video: small files for the panel and fullscreen",
            ", ".join(f"{kb:.0f} KB at {size} px" for size, kb in sizes_kb.items()) + " for 30 s")
        colours = pixel.colours("PIXELSONG01") or []
        log(abs(len(colours) - 30 * video_mod.FPS) <= video_mod.FPS and all(len(c) == 3 for c in colours),
            "pixel video: one light colour per frame", f"{len(colours)} colours")

        # "Video" light colours use them; a song without a pixel video uses the artwork.
        rings = plugin.lights
        rings.set_colours("artwork", "#1a9fff")
        rings.video_lookup = lambda: pixel.colours("PIXELSONG01")
        video_colour = rings._colour(100, 8.0)
        rings.video_lookup = lambda: None
        log(len(video_colour) == 3 and rings._colour(100, 8.0) == rings._artwork(100),
            "light colours: artwork follows the TV mode video (artwork without one)", str(video_colour))

        # Still image: no pixel video, remembered so it isn't fetched again.
        candidates["PIXELSONG02"] = ["STILLVIDEO0"]
        still_track = dict(song_track, id="PIXELSONG02")
        pixel.watch(still_track, song)
        none = await wait_for(lambda: pixel.watch(still_track, song)["state"] == "none", 90)
        fetched.clear()
        pixel.watch(still_track, song)
        log(none and not fetched, "pixel video: still image falls back to the artwork, no retry")

        # Nobody watching any more (panel closed / game started): the work stops.
        candidates["PIXELSONG03"] = ["SLOWVIDEO00"]
        slow_track = dict(song_track, id="PIXELSONG03")
        pixel.watch(slow_track, song)
        await asyncio.sleep(1)
        busy = "PIXELSONG03" in pixel.jobs
        stopped = await wait_for(lambda: "PIXELSONG03" not in pixel.jobs, video_mod.WATCH_GRACE + 6)
        log(busy and stopped and pixel.index.get("PIXELSONG03") is None,
            "pixel video: stops when it's no longer on screen", f"stopped within {video_mod.WATCH_GRACE + 6}s")

        # TV mode on screen: a song waits for its video and they start together;
        # with nobody watching it doesn't wait at all.
        candidates["PIXELSONG04"] = ["MOVINGVIDEO"]
        wait_track = dict(song_track, id="PIXELSONG04")
        pixel.viewer_seen()
        t0 = time.time()
        await plugin._wait_for_video(wait_track, song, plugin.token, limit=90)
        waited = time.time() - t0
        ready_after_wait = pixel.status("PIXELSONG04")["state"] == "ready"
        pixel.viewer_at = 0
        t0 = time.time()
        await plugin._wait_for_video(dict(song_track, id="PIXELSONG05"), song, plugin.token, limit=90)
        log(ready_after_wait and waited > 1 and time.time() - t0 < 0.5,
            "TV mode: the song waits for its video (not when nobody's watching)", f"waited {waited:.0f}s")
        await asyncio.sleep(0.5)
        for task in list(pixel.jobs.values()):
            task.cancel()

        # Clear cache drops it unless it was kept with a download.
        pixel.set_kept("PIXELSONG01", True)
        pixel.clear()
        kept = pixel.read("PIXELSONG01", 64) is not None
        pixel.set_kept("PIXELSONG01", False)
        pixel.clear()
        log(kept and pixel.read("PIXELSONG01", 64) is None and "PIXELSONG02" not in pixel.index,
            "pixel video: kept with downloads, otherwise cleared with the cache")
    finally:
        catalog_mod.video_candidates = real_candidates
        plugin.engine.fetch_video = real_fetch


async def engine_checks(plugin):
    """The music engine: set up on first launch, checked, kept current, never broken by an update."""
    from cuivar.engine import EngineError, classify
    from cuivar import PASSED_ON
    t = plugin.tools
    await wait_for(lambda: t.setup_task is not None and t.setup_task.done(), 120)
    engine = await plugin.get_engine()
    command = t.command() or []
    log(t.ready and engine["state"] == "ready" and command[-1].startswith(fake.DECKY_PLUGIN_RUNTIME_DIR),
        "engine: set up on first launch into the data folder", f"yt-dlp {engine['version']} ({engine['kind']})")
    log(engine["kind"] == "python" and len(command) == 2,
        "engine: pure-Python yt-dlp, run with the system's Python", " ".join(command))
    log(os.access(t.qjs_file or "", os.X_OK), "engine: QuickJS downloaded, checked and runnable")
    if online:
        return
    import certifi
    log(certifi.FOUND is not None, "system certificate store found (certifi stand-in)", str(certifi.FOUND))
    os.environ["CUIVAR_TEST_SECRET"] = "do-not-pass"
    env = child_env()
    log(set(env) <= set(PASSED_ON) and "CUIVAR_TEST_SECRET" not in env,
        "helper programs only get the environment they need", ", ".join(sorted(env)))

    first = t.version
    fake_release("2026.02.02", wrong_checksum=True)
    fake_github["latest"] = "2026.02.02"
    refused = False
    try:
        await t.update("test")
    except tools_mod.ToolsError:
        refused = True
    log(refused and t.version == first and t.ready, "engine: an update that fails its checksum is refused")

    fake_release("2026.03.03", broken=True)
    fake_github["latest"] = "2026.03.03"
    refused = False
    try:
        await t.update("test")
    except tools_mod.ToolsError:
        refused = True
    leftovers = [n for n in os.listdir(t.dir) if n.endswith(".new")]
    log(refused and t.version == first and t.ready and not leftovers,
        "engine: a new version that doesn't work is not used (the working one stays)")

    fake_release("2026.04.04")
    fake_github["latest"] = "2026.04.04"
    message = await t.update("test")
    log(t.version == "2026.04.04" and os.path.exists(os.path.join(t.dir, "yt-dlp.previous")),
        "engine: updates to a newer version, keeps the previous one", message)

    # YouTube changed something: update straight away, then try the song once more.
    calls = []

    async def flaky_once(video_id):
        calls.append(t.version)
        if len(calls) == 1:
            raise classify("ERROR: [youtube] nsig extraction failed")  # what yt-dlp says when YouTube changed
        return "/tmp/fixed.webm"
    real_once, plugin.engine._download_once = plugin.engine._download_once, flaky_once
    fake_release("2026.05.05")
    fake_github["latest"] = "2026.05.05"
    t.last_auto_update = 0
    result = await plugin.engine._download("zzzzzzzzzzz")
    log(result == "/tmp/fixed.webm" and calls == ["2026.04.04", "2026.05.05"],
        "engine: YouTube changed something, updated right away and the song retried once", " → ".join(calls))
    # No newer version to fix it: the "needs an update" message, after one try.
    calls.clear()
    t.last_auto_update = 0
    shown = None
    try:
        await plugin.engine._download("zzzzzzzzzzz")
    except EngineError as error:
        shown = str(error)
    log(shown and "Update music engine" in shown and len(calls) == 1,
        "engine: if the auto-update can't fix it, the message is shown", shown or "")
    plugin.engine._download_once = real_once

    # The quiet daily check: only when it's due and nothing is using yt-dlp.
    fake_release("2026.06.06")
    fake_github["latest"] = "2026.06.06"
    t.state["checked"] = time.time() - 2 * 86400
    await t.check_daily(lambda: True)
    skipped_while_busy = t.version == "2026.05.05"
    states = []
    t.on_change = lambda: states.append(t.status)
    await t.check_daily(lambda: False)
    t.on_change = plugin._emit_soon
    log(skipped_while_busy and t.version == "2026.06.06" and "updating" not in states,
        "engine: daily check updates quietly, never while something is downloading")

    # First launch without internet: a clear message, and play/download say so.
    fake_github["offline"] = True
    fresh = tools_mod.Tools(os.path.join(work, "no-internet"))
    await fresh.setup()
    offline_engine = main.Engine(os.path.join(work, "no-internet"), fresh)
    said = None
    try:
        await offline_engine._run(["--version"])
    except EngineError as error:
        said = str(error)
    fake_github["offline"] = False
    log(fresh.status == "error" and said == tools_mod.NO_INTERNET, "engine: no internet on first launch, clear message",
        said or "")


def fake_track(n):
    return {"kind": "track", "id": f"CUIVARTEST{n}", "title": f"Test song {n}",
            "artists": [{"name": "Cuivar", "id": None}], "album": None, "duration": 30, "art": None}


def stream_volume():
    out = subprocess.run(["pactl", "-f", "json", "list", "sink-inputs"], capture_output=True, text=True,
                         env=child_env()).stdout
    ours = [s for s in json.loads(out or "[]") if (s.get("properties") or {}).get("application.name") == "Cuivar Pulse"]
    return ours[0]["volume"]["front-left"]["value_percent"] if ours else "stream not found"


async def run():
    if not online:
        use_fake_github()  # offline: the music engine comes from the stand-in releases
    plugin = main.Plugin()
    await plugin._main()

    # --- test songs, "downloaded" without YouTube ------------------------------------------
    track, other = fake_track(1), fake_track(2)
    for t in (track, other):
        source = os.path.join(work, f"{t['id']}.webm")
        make_song(source)
        plugin.library.add_download(t, source)
    log(plugin.library.is_downloaded(track["id"]) and plugin.library.is_downloaded(other["id"]),
        "test songs added as downloads")

    # --- playlists -------------------------------------------------------------------------
    created = await plugin.create_playlist("Test mix", track)
    pid = created["id"]
    again = await plugin.add_to_playlist(pid, track)
    added = await plugin.add_to_playlist(pid, other)
    playlist = (await plugin.get_playlist(pid))["playlist"]
    log(created["ok"] and not again["added"] and added["added"] and len(playlist["tracks"]) == 2,
        "playlist create / add / no duplicates", f"{[t['title'] for t in playlist['tracks']]}")
    log((await plugin.create_playlist("   ", None))["ok"] is False, "empty playlist name refused")

    # --- offline playback, volume, lights --------------------------------------------------
    await plugin.set_volume(60)
    await plugin.play_list(playlist["tracks"], 0, False)
    playing = await wait_for(lambda: plugin.player.playing, 15)
    from_downloads = bool(plugin.player.file and plugin.player.file.startswith(plugin.library.downloads_dir))
    log(playing and from_downloads, "plays a downloaded song (works offline)",
        "" if playing else f"file={plugin.player.file} error={plugin.error}")
    await asyncio.sleep(0.8)
    log(stream_volume() == "60%", "new song starts at the music volume", stream_volume())
    await plugin.set_volume(35)
    await asyncio.sleep(0.4)
    log(plugin.player.volume == 35 and stream_volume() == "35%", "music volume changes the live stream", stream_volume())
    await asyncio.gather(*(plugin.set_volume(v) for v in range(10, 101, 10)), plugin.set_volume(45))
    await asyncio.sleep(0.4)
    log(stream_volume() == "45%", "rapid volume changes end on the last value", stream_volume())

    info = await plugin.get_lights()
    log(info["available"] and info["enabled"] and info["colours"] == "artwork", "lights on by default (artwork colours)")
    took_over = await wait_for(lambda: led()["effect"] == "monocolor", 6)
    samples = set()
    for _ in range(30):
        samples.add(led()["multi_intensity"])
        await asyncio.sleep(1 / 30)
    log(took_over and len(samples) > 5, "lights pulse while playing", f"{len(samples)} different colours in 1 s")
    saved_file = os.path.join(fake.DECKY_PLUGIN_SETTINGS_DIR, "lights-saved.json")
    log(os.path.exists(saved_file) and json.load(open(saved_file)) == USER_LIGHTING,
        "your own lighting is saved before taking over")
    reported = await plugin.player.output_delay()
    synced = await wait_for(lambda: reported is not None and abs(plugin.lights.delay - reported) < 0.03, 6)
    log(synced, "lights wait for the sound output (Bluetooth delay)",
        f"{reported * 1000:.0f} ms" if reported is not None else "no reading from PipeWire")

    seek_to = 10.0
    await plugin.seek(seek_to)
    await asyncio.sleep(0.6)
    log(abs(plugin.player.position() - (seek_to + 0.6)) < 0.5 and stream_volume() == "45%",
        "seek keeps playing at the same volume", f"{plugin.player.position():.1f}s, {stream_volume()}")

    await plugin.toggle()
    log(not plugin.player.playing, "pause")
    restored = await wait_for(lambda: led() == USER_LIGHTING, 4)
    log(restored and not os.path.exists(saved_file), "your lighting comes back after pausing", str(led()))

    await plugin.toggle()
    await wait_for(lambda: led()["effect"] == "monocolor", 4)
    off = await plugin.set_lights(False)
    log(not off["enabled"] and led() == USER_LIGHTING, "switching lights off restores them at once")
    await plugin.set_lights(True)

    rings = plugin.lights
    for mode, fixed in (("fixed", "#ff0000"), ("rainbow", ""), ("artwork", "")):
        result = await plugin.set_light_colours(mode, fixed or "#1a9fff")
        colour = rings._colour(100, 3.3)
        log(result["ok"] and len(colour) == 3 and all(0 <= c <= 255 for c in colour)
            and (mode != "fixed" or colour == (255, 0, 0)), f"light colours: {mode}", str(colour))
    log((await plugin.set_light_colours("disco", ""))["ok"] is False, "unknown light colours refused")

    # "Feel the beat" taps (counted by a stand-in; nothing actually vibrates).
    log(info["haptics"] == "off", "feel the beat is off by default",
        f"rumble available: {info['hapticsAvailable']}")
    taps_felt = []

    async def fake_tap(strong=False):
        taps_felt.append(plugin.player.position())
        strong_felt.append(strong)
    strong_felt = []
    real_tap, lights.tap = lights.tap, fake_tap
    plugin.lights.haptics_available = True
    on = await plugin.set_haptics("subtle")
    await wait_for(lambda: plugin.lights.levels, 5)  # re-analysis after the lights toggle above
    tap_points = [t / lights.FPS for t in plugin.lights.taps]
    gaps = [b - a for a, b in zip(tap_points, tap_points[1:])]
    log(on["haptics"] == "subtle" and tap_points and min(gaps or [99]) >= 1.5,
        "taps only on big beats, at least 1.5 s apart", f"{len(tap_points)} taps in 30 s")
    def expected(start, seconds):
        return sum(1 for t in tap_points if start < t <= start + seconds)

    async def count_taps(seek_to, seconds):
        """Seek, let playback restart, then count taps over `seconds`."""
        await plugin.seek(seek_to)
        await wait_for(lambda: plugin.player.playing, 3)
        await asyncio.sleep(0.3)
        start = plugin.player.position()
        taps_felt.clear()
        await asyncio.sleep(seconds)
        return start, list(taps_felt)

    start, felt = await count_taps(0, 4)
    log(felt and len(felt) == expected(start, 4), "taps happen while playing, on time",
        f"{len(felt)} of {expected(start, 4)} expected at {[round(t, 1) for t in felt]}")
    await plugin.set_lights(False)
    start, felt = await count_taps(4.5, 3)
    log(felt and len(felt) == expected(start, 3) and led() == USER_LIGHTING,
        "taps work with the lights off", f"{len(felt)} of {expected(start, 3)}")
    # Strong: every beat, big ones harder.
    perf = await plugin.set_haptics("strong")
    strong_felt.clear()
    _, felt = await count_taps(0, 4)
    log(perf["haptics"] == "strong" and len(felt) >= 6 and any(strong_felt) and not all(strong_felt),
        "strong: every beat, big ones harder",
        f"{len(felt)} taps in 4 s, {sum(strong_felt)} strong")
    await plugin.set_haptics("off")
    taps_felt.clear()
    await asyncio.sleep(2)
    log(not taps_felt, "no taps once switched off")
    log((await plugin.set_haptics("earthquake"))["haptics"] == "off", "unknown feel-the-beat mode falls back to off")
    # Build-up: a song that drops in at 15 s swells for 2 s, then double-flashes.
    drop_song = os.path.join(work, "drop.webm")
    make_song(drop_song, seconds=24, drop_at=15)
    lv = lights.analyse(drop_song)["levels"]
    drop = next((i for i in range(len(lv) - 4) if lv[i:i + 4] == lights.FLASH_DROP[:4]), None)
    swell = lv[drop - lights.BUILD:drop] if drop else []
    log(drop is not None and abs(drop / lights.FPS - 15) < 0.5 and swell[0] < 0.3 and swell[-1] > 0.7
        and sum(b >= a - 0.01 for a, b in zip(swell, swell[1:])) > len(swell) * 0.9,
        "clear drops build up for 2 s, then double-flash",
        f"drop at {drop / lights.FPS:.1f}s, glow {swell[0]:.2f} → {swell[-1]:.2f}" if drop else "no drop found")
    from cuivar.library import _haptics_mode, _light_colours, _pixel_style
    log([_light_colours(v) for v in ("video", "energy", "rainbow", "fixed", "disco")] == ["artwork", "rainbow", "rainbow", "fixed", "artwork"]
        and _pixel_style("crisp") == "arcade" and _pixel_style(None) == "retro",
        "older light colour and pixel style choices carry over")
    hues = lights.rainbow_hues([0.0] * 300 + [1.0] * 300)
    # How far round the colours it gets in 1 s (30 frames), calm vs intense.
    calm, intense = (hues[29] - hues[0]) % 1, (hues[329] - hues[300]) % 1
    log(intense > calm * 5, "rainbow moves faster when the song is intense", f"calm {calm:.3f}, intense {intense:.3f} of a turn per second")
    log([_haptics_mode(v) for v in (True, False, "balanced", "performance")] == ["subtle", "off", "subtle", "strong"],
        "older feel-the-beat settings carry over")
    lights.tap = real_tap
    await plugin.set_lights(True)
    await plugin.toggle()  # pause

    # --- voice search -----------------------------------------------------------------------
    from cuivar import voice as voice_mod
    log(voice_mod.tidy("Hello by Adel. Hello by Adel. Hello by Ad") == "Hello by Adel"
        and voice_mod.tidy("Bye Bye Bye") == "Bye Bye Bye" and voice_mod.tidy("[music]") == ""
        and voice_mod.tidy("(upbeat music) Hello") == "Hello" and voice_mod.tidy("*sad music* ♪") == ""
        and voice_mod.tidy("[S") == "" and voice_mod.tidy("Hello (mus") == "Hello", "voice: repeats and sound tags cleaned up, real repeats kept")
    status = await plugin.voice_status()
    refused = await plugin.voice_start()
    log(not status["ready"] and not refused["ok"], "voice: asks for the download before first use")
    # If this device already has voice search downloaded, use it for a real listen.
    installed = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(plugin_dir))), "data", "CuivarPulse")
    if voice_mod.Voice(installed, plugin.player).ready:
        plugin.voice.dir = os.path.join(installed, "voice")
        await plugin.set_volume(45)
        await plugin.play_list(playlist["tracks"], 0, False)
        await wait_for(lambda: plugin.player.playing and stream_volume() == "45%", 6)
        started = await plugin.voice_start()
        await asyncio.sleep(0.6)
        ducked = stream_volume()
        await asyncio.sleep(0.6)
        t0 = time.time()
        heard = await plugin.voice_stop()
        took = time.time() - t0
        await asyncio.sleep(0.3)
        log(started["ok"] and ducked == "9%" and stream_volume() == "45%",
            "voice: music turned down while listening, back after", f"{ducked} → {stream_volume()}")
        # The mic only hears the test song: nothing should come back as a search.
        log(took < 5 and not heard["ok"] and "catch" in heard["error"],
            "voice: listens and transcribes on the device", f"{took:.1f}s, heard {heard.get('text') or 'nothing'!r}")
        plugin.voice.dir = os.path.join(fake.DECKY_PLUGIN_RUNTIME_DIR, "voice")
        # Stop and let the rings go back, so the crash test below has them to itself.
        await plugin.player.stop()
        await wait_for(lambda: led() == USER_LIGHTING, 4)
    else:
        log(True, "voice: real listen skipped (not downloaded on this device)")

    # --- pixel video (made-up videos, no YouTube) -------------------------------------------
    await pixel_video_checks(plugin, work)

    # Crash recovery: a run that died while holding the rings.
    with open(saved_file, "w") as f:
        json.dump(USER_LIGHTING, f)
    for name, value in (("effect", "monocolor"), ("multi_intensity", "255 0 0"), ("brightness", "100")):
        with open(os.path.join(LED, name), "w") as f:
            f.write(value)
    lights.Lights(plugin.player, saved_file)
    log(led() == USER_LIGHTING and not os.path.exists(saved_file), "lighting restored after a crash")

    # --- download allowance, stale songs, settings -----------------------------------------
    await plugin.set_settings("songs", 50, 5)
    real = dict(plugin.library.data["downloads"])
    for i in range(48):
        plugin.library.data["downloads"][f"fake{i:07d}"] = {"track": track, "file": "none", "size": 1, "added": time.time()}
    events.clear()
    await plugin.download_tracks([fake_track(3)])
    await wait_for(lambda: any(e == "notice" for e, _ in events), 10)
    notices = [a[0] for e, a in events if e == "notice"]
    log(bool(notices) and "limit" in notices[0], "allowance reached: download refused with a reminder",
        notices[0] if notices else "")
    log(plugin.library.usage()["nearLimit"], "near-limit flag at 90%+", f"{plugin.library.usage()['ratio']:.0%}")
    plugin.library.data["downloads"] = real

    plugin.library.data["plays"][track["id"]] = time.time() - 40 * 86400
    plugin.library.data["plays"][other["id"]] = time.time()
    stale = plugin.library.stale()
    log([t["id"] for t in stale] == [track["id"]], "song unplayed for 40 days is suggested for removal")

    settings = await plugin.set_settings("storage", 999, 0.1)
    log(settings["settings"]["limitGB"] == 2.0 and settings["settings"]["limitSongs"] == 500
        and settings["settings"]["limitMode"] == "storage", "settings snap to the allowed steps")

    regions = await plugin.get_regions()
    log(regions["selected"] == "auto" and any(r["code"] == regions["auto"] for r in regions["regions"]),
        "region defaults to automatic", f"automatic = {regions['auto']}")
    await engine_checks(plugin)

    # --- shuffle and repeat (made-up queue; no playback needed) -----------------------------
    fakes = [dict(track, id=f"zz{i:09d}", title=f"Song {i}") for i in range(8)]
    await plugin.player.stop()
    plugin.token += 1
    plugin.queue, plugin.index, plugin.shuffle, plugin.unshuffled = list(fakes), 2, False, None
    before = [t["id"] for t in plugin.queue]
    await plugin.set_shuffle(True)
    shuffled = [t["id"] for t in plugin.queue]
    log(plugin.shuffle and shuffled[:3] == before[:3] and sorted(shuffled) == sorted(before),
        "shuffle keeps played songs and the current one, mixes the rest")
    await plugin.set_shuffle(False)
    log([t["id"] for t in plugin.queue] == before, "shuffle off restores the original order")
    modes = [(await plugin.cycle_repeat())["repeat"] for _ in range(3)]
    log(modes == ["all", "one", "off"], "repeat cycles off → all → one → off", " → ".join(modes))

    # --- online extras (opt-in) -------------------------------------------------------------
    if online:
        found = await plugin.search("Someone Like You Adele")
        log(found["ok"] and found["results"], "search", f"{len(found.get('results', []))} songs")
        real_track = found["results"][0]
        await plugin.set_settings("songs", 500, 5)
        t0 = time.time()
        await plugin.download_tracks([real_track])
        done = await wait_for(lambda: plugin.library.is_downloaded(real_track["id"]), 120)
        log(done, "download from YouTube Music", f"{time.time() - t0:.1f}s")
        log(plugin.engine.cached(real_track["id"]) is None, "download moved out of the automatic cache")
        switched = await plugin.set_region("IN")
        again = await plugin.search("Tum Hi Ho")
        log(switched["ok"] and again["ok"] and again["results"], "switch region, then search")
        updated = await plugin.update_engine()
        log(updated["ok"], "music engine self-update", updated.get("message") or updated.get("error"))
        await plugin.remove_downloads([real_track["id"]])

    # --- session survives a restart ---------------------------------------------------------
    plugin.queue, plugin.index = [track], 0
    plugin.repeat, plugin.shuffle = "all", True
    plugin.player.offset = 42.0
    plugin._save_session()
    await plugin._unload()
    log(led() == USER_LIGHTING, "lighting left as you had it after unloading")
    fresh = main.Plugin()
    await fresh._main()
    log(fresh.queue and fresh.queue[fresh.index]["id"] == track["id"] and abs(fresh.player.offset - 42.0) < 1
        and fresh.repeat == "all" and fresh.shuffle,
        "queue, position, shuffle and repeat restored after restart", f"index {fresh.index}, {fresh.player.offset:.0f}s")
    await fresh._unload()

    await plugin.remove_downloads([track["id"], other["id"]])
    log(not plugin.library.is_downloaded(track["id"]) and not os.listdir(plugin.library.downloads_dir),
        "remove downloads deletes the files")
    await plugin.delete_playlist(pid)
    log(not plugin.library.playlists(), "delete playlist")


try:
    asyncio.run(run())
except BaseException:
    failed = True  # a crash part-way is a failure, not a pass
    raise
finally:
    shutil.rmtree(work, ignore_errors=True)
    print(f"\n({'online' if online else 'offline'} run)")
    print("PLUGIN CHECK FAILED" if failed else "PLUGIN CHECK PASSED")
