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

failed = False


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


def fake_track(n):
    return {"kind": "track", "id": f"CUIVARTEST{n}", "title": f"Test song {n}",
            "artists": [{"name": "Cuivar", "id": None}], "album": None, "duration": 30, "art": None}


def stream_volume():
    out = subprocess.run(["pactl", "-f", "json", "list", "sink-inputs"], capture_output=True, text=True,
                         env=child_env()).stdout
    ours = [s for s in json.loads(out or "[]") if (s.get("properties") or {}).get("application.name") == "Cuivar Pulse"]
    return ours[0]["volume"]["front-left"]["value_percent"] if ours else "stream not found"


async def run():
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
    for mode, fixed in (("fixed", "#ff0000"), ("energy", ""), ("rainbow", ""), ("artwork", "")):
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
    from cuivar.library import _haptics_mode
    log([_haptics_mode(v) for v in (True, False, "balanced", "performance")] == ["subtle", "off", "subtle", "strong"],
        "older feel-the-beat settings carry over")
    lights.tap = real_tap
    await plugin.set_lights(True)
    await plugin.toggle()  # pause

    # --- voice search -----------------------------------------------------------------------
    from cuivar import voice as voice_mod
    log(voice_mod.tidy("Hello by Adel. Hello by Adel. Hello by Ad") == "Hello by Adel"
        and voice_mod.tidy("Bye Bye Bye") == "Bye Bye Bye" and voice_mod.tidy("[music]") == ""
        and voice_mod.tidy("(upbeat music) Hello") == "Hello" and voice_mod.tidy("*sad music* ♪") == "", "voice: repeats and sound tags cleaned up, real repeats kept")
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
    engine = await plugin.get_engine()
    log(bool(engine["version"]) and plugin.engine.ytdlp.startswith(fake.DECKY_PLUGIN_RUNTIME_DIR),
        "engine runs from the user's data folder", engine["version"] or "")
    qjs = plugin.engine.js_runtime.split(":", 1)[1]
    log(qjs.startswith(fake.DECKY_PLUGIN_RUNTIME_DIR) and os.access(qjs, os.X_OK),
        "JS runtime copied to the data folder and runnable")

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
