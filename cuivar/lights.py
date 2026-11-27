"""Controller lights that dance to the music (Legion Go S joystick rings).

SteamOS gives the logged-in user write access to the ring LEDs in sysfs, so
no extra rights are needed. When a song starts we analyse it once (a quick
ffmpeg pass over the bass) into a list of light levels, 30 per second; while
it plays we look up the level at the player's position, so pause, seek and
skip stay in sync for free. The colour comes from the song's artwork.

The user's own lighting (effect, colour, brightness) is saved before we take
over and put back whenever music stops, the feature is switched off, or the
plugin unloads. It's also saved to disk so a crash can't leave the rings in
our colours: the next start restores it.
"""

import array
import asyncio
import colorsys
import json
import math
import os

from . import child_env

LED = "/sys/class/leds/go_s:rgb:joystick_rings"
FPS = 30
LEAD = 0.0              # timing offset; a click-track test on the Legion showed 0 looks locked to the beat
FALLBACK = (26, 159, 255)  # Steam blue, when the artwork has no clear colour
SAVED_FIELDS = ("effect", "multi_intensity", "brightness")


def available():
    return all(os.access(os.path.join(LED, f), os.W_OK) for f in ("effect", "multi_intensity", "brightness"))


def _read(name):
    with open(os.path.join(LED, name)) as f:
        return f.read().strip()


def _write(name, value):
    with open(os.path.join(LED, name), "w") as f:
        f.write(str(value))


# --- analysis -------------------------------------------------------------------------------

def _decode(args, limit):
    import subprocess
    out = subprocess.run(["ffmpeg", "-nostdin", "-loglevel", "error", *args],
                         capture_output=True, env=child_env(), timeout=60).stdout
    return out[:limit]


def analyse(path):
    """Everything the lights need for one song, 30 values per second:
      levels   light level per frame: small / normal / big flashes on the bass
               hits (drops flash twice), over a faint glow
      energy   smoothed loudness 0-1 (for the Energy colours)
      changes  frames where the colour moves on: the biggest hits and the
               moments the music builds or drops (for the Artwork colours)"""
    rate = 6000
    raw = _decode(["-i", path, "-af", "lowpass=f=180", "-ac", "1", "-ar", str(rate), "-f", "s16le", "-"],
                  rate * 2 * 60 * 30)
    samples = array.array("h")
    samples.frombytes(raw[: len(raw) - len(raw) % 2])
    window = rate // FPS
    energy = []
    for i in range(0, len(samples) - window + 1, window):
        chunk = samples[i:i + window]
        energy.append(math.sqrt(sum(x * x for x in chunk) / window))
    if not energy:
        return {"levels": [], "energy": [], "changes": [], "taps": [], "beats": []}
    ranked = sorted(energy)
    top = ranked[int(len(ranked) * 0.97)] or 1.0
    loud = [e / top for e in energy]       # uncapped: how hard each hit really is
    norm = [min(v, 1.0) for v in loud]     # capped 0-1: for glow, energy, sections

    # Find the hits: clearly louder than the last third of a second, not too
    # soon after the previous one (max ~5 per second).
    # A hit's strength is measured on the uncapped loudness, so a huge hit
    # still stands out from an ordinary loud one.
    hits, last_hit = [], -99
    for i, e in enumerate(norm):
        recent = norm[max(0, i - 10):i] or [e]
        average = sum(recent) / len(recent)
        if e - average > 0.12 and e > 0.35 and i - last_hit >= 6:
            raw_recent = loud[max(0, i - 10):i] or [loud[i]]
            hits.append((i, loud[i] - sum(raw_recent) / len(raw_recent)))
            last_hit = i

    # Smoothed loudness over ~1.5 s.
    smooth, value = [], norm[0]
    for e in norm:
        value += (e - value) * 0.03
        smooth.append(value)

    # Colour changes: the strongest hits (top 15%), plus builds and drops
    # (the 2 s average jumping away from the 4 s before it). At least 8 s apart.
    candidates = set()
    if hits:
        cut = sorted(strength for _, strength in hits)[int(len(hits) * 0.85)]
        candidates.update(i for i, strength in hits if strength >= cut)
    span = FPS * 2
    for i in range(span * 3, len(smooth), FPS // 2):
        before = sum(norm[i - span * 3:i - span]) / (span * 2)
        now = sum(norm[i - span:i]) / span
        if abs(now - before) > 0.22:
            candidates.add(i - span)
    changes, last = [], -FPS * 8
    for i in sorted(candidates):
        if i - last >= FPS * 8:
            changes.append(i)
            last = i

    # Haptic taps: only the strongest hits (top 10%) and the drops (the music
    # suddenly getting bigger, snapped to the first hit of the new part).
    # At least 1.5 s apart, so it never becomes a constant buzz.
    tap_frames, drops, builds = set(), set(), set()
    if hits:
        cut = sorted(strength for _, strength in hits)[int(len(hits) * 0.90)]
        tap_frames.update(i for i, strength in hits if strength >= cut)
        hit_frames = [i for i, _ in hits]
        last_drop = -FPS * 4
        for i in range(span * 3, len(smooth), FPS // 2):
            before = sum(norm[i - span * 3:i - span]) / (span * 2)
            now = sum(norm[i - span:i]) / span
            # A drop: the music at least doubling after a quieter stretch.
            # One drop per comeback: at most one every 4 s.
            if now - before > 0.06 and now >= before * 2 and i - span - last_drop >= FPS * 4:
                start = i - span
                first = next((h for h in hit_frames if start <= h <= start + FPS // 2), None)
                if first is not None:
                    tap_frames.add(first)
                    drops.add(first)
                    last_drop = first
                    # A clear drop gets a build-up: the 2 s after it at least 3x
                    # louder than the 4 s before (measured on the whole jump,
                    # we know the song ahead), not in the opening seconds, and
                    # at most one every 20 s so it stays special.
                    after = sum(norm[first:first + span]) / max(len(norm[first:first + span]), 1)
                    prior = sum(norm[first - span * 2:first]) / (span * 2) if first >= span * 2 else 1.0
                    if (after >= prior * BUILD_RATIO and after - prior > BUILD_JUMP and first >= FPS * 8
                            and all(first - b >= FPS * 20 for b in builds)):
                        builds.add(first)
    taps, last = [], -FPS * 2
    for i in sorted(tap_frames):
        if i - last >= int(FPS * 1.5):
            taps.append(i)
            last = i
    # Strong mode: every beat (at most ~3 per second); the big ones
    # (subtle taps and drops) are flagged to hit harder.
    strong = set(tap_frames)
    beats, last = [], -FPS
    for i, _ in hits:
        if i - last >= 9 or i in strong:
            beats.append((i, i in strong))
            last = i
    levels = _levels(norm, loud, hits, strong, drops, builds)

    # Spread each song's own quiet-to-loud range over 0-1, so every song
    # uses the full range of Energy colours.
    ordered = sorted(smooth)
    low, high = ordered[int(len(ordered) * 0.05)], ordered[int(len(ordered) * 0.95)]
    spread = max(high - low, 1e-6)
    smooth = [min(max((v - low) / spread, 0.0), 1.0) for v in smooth]
    return {"levels": levels, "energy": smooth, "changes": changes, "taps": taps, "beats": beats}

# Flash shapes, frame by frame (30 per second). Values above 1 are "white-hot"
# (pushed towards white, see Lights._frame). Big beats get every trick at once
# (full, white, held, slow afterglow) so they're the ones that catch the eye;
# drops get a double flash.
FLASH_SMALL = [0.40, 0.26, 0.17]
FLASH_NORMAL = [0.70, 0.70, 0.45, 0.29, 0.19]
FLASH_BIG = [1.6, 1.6, 1.6, 1.25, 1.0, 0.85, 0.72, 0.61, 0.52, 0.44, 0.37, 0.31, 0.26]
FLASH_DROP = [1.6, 1.6, 0.08, 0.08, 1.6, 1.6, 1.6, 1.25, 1.0, 0.85, 0.72, 0.61, 0.52, 0.44, 0.37, 0.31, 0.26]
# Before a clear drop the glow swells for 2 s (slow at first, then faster),
# stopping short of white so the drop's white-hot double flash still lands.
BUILD = FPS * 2
BUILD_PEAK = 0.85
BUILD_RATIO = 3.0    # a drop needs this much louder after than before...
BUILD_JUMP = 0.12    # ...and this big a jump, to count as clear


def _levels(norm, loud, hits, strong, drops, builds=()):
    """Light level for every frame: a faint ember that follows the loudness,
    with each hit's flash shape laid over it (small, normal, big or drop).
    Small vs normal goes by how loud the hit actually is, compared with the
    song's typical (non-big) hit."""
    levels = [0.05 + 0.15 * e for e in norm]
    for i in builds:
        for k in range(BUILD):
            frame = i - BUILD + k
            if frame >= 0:
                levels[frame] = max(levels[frame], levels[frame] + (BUILD_PEAK - levels[frame]) * ((k + 1) / BUILD) ** 2)
    others = sorted(loud[i] for i, _ in hits if i not in strong)
    typical = others[len(others) // 2] if others else 0
    for i, _ in hits:
        if i in drops:
            shape = FLASH_DROP
        elif i in strong:
            shape = FLASH_BIG
        elif loud[i] >= typical * 1.15:
            shape = FLASH_NORMAL
        else:
            shape = FLASH_SMALL
        for k, value in enumerate(shape):
            if i + k >= len(levels):
                break
            # The double flash's gap must go dark, even over a glow.
            levels[i + k] = value if shape is FLASH_DROP and k < 4 else max(levels[i + k], value)
    return levels


def rainbow_hues(energy):
    """Rainbow position for every frame: the colour moves on faster the more
    intense the music is (from one trip round in RAINBOW_CALM seconds to one in
    RAINBOW_INTENSE). Worked out once per song, so seeking lands on the right colour."""
    hues, hue = [], 0.0
    for e in energy:
        seconds_per_trip = RAINBOW_CALM + (RAINBOW_INTENSE - RAINBOW_CALM) * min(max(e, 0.0), 1.0)
        hue += 1.0 / (seconds_per_trip * FPS)
        hues.append(hue % 1.0)
    return hues


def palette_for(art_url):
    """Up to 3 vivid, clearly different colours from the artwork (strongest first)."""
    if not art_url:
        return []
    try:
        raw = _decode(["-i", art_url, "-vf", "scale=24:24", "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
                      24 * 24 * 3)
    except Exception:
        return []
    if len(raw) < 24 * 24 * 3:
        return []
    bins = {}
    for i in range(0, len(raw), 3):
        r, g, b = raw[i] / 255, raw[i + 1] / 255, raw[i + 2] / 255
        h, s, v = colorsys.rgb_to_hsv(r, g, b)
        if s < 0.25 or v < 0.2:
            continue  # greys, blacks and whites don't make good LED colours
        weight = s * v
        slot = bins.setdefault(int(h * 12) % 12, [0.0, 0.0, 0.0, 0, 0])
        slot[0] += weight
        # Average hue on the circle (red sits at both 0 and 1).
        slot[1] += math.cos(h * 2 * math.pi) * weight
        slot[2] += math.sin(h * 2 * math.pi) * weight
        slot[3] += s * weight
        slot[4] += 1
    picked, used = [], []
    for key, (weight, cos_sum, sin_sum, sat_sum, count) in sorted(bins.items(), key=lambda kv: -kv[1][0]):
        if count < 8 or len(picked) == 3:
            continue
        if any(min(abs(key - u), 12 - abs(key - u)) < 2 for u in used):
            continue  # too close to a colour we already have
        if picked and weight < bins[used[0]][0] * 0.15:
            break     # only a few specks of this colour
        hue = (math.atan2(sin_sum, cos_sum) / (2 * math.pi)) % 1.0
        saturation = max(min(1.0, sat_sum / weight * 1.3), 0.6)
        r, g, b = colorsys.hsv_to_rgb(hue, saturation, 1.0)
        picked.append((int(r * 255), int(g * 255), int(b * 255)))
        used.append(key)
    return picked


def colour_for(art_url):
    palette = palette_for(art_url)
    return palette[0] if palette else None


def _hsv(h, s=1.0, v=1.0):
    r, g, b = colorsys.hsv_to_rgb(h % 1.0, s, v)
    return int(r * 255), int(g * 255), int(b * 255)


def _mix(a, b, t):
    return tuple(int(x + (y - x) * t) for x, y in zip(a, b))


def with_partner(palette):
    """Artwork with one strong colour (or none) still gets a second, related
    colour to move to, so colour changes stay visible."""
    if not palette:
        return [FALLBACK, (138, 60, 255)]  # Steam blue and purple
    if len(palette) == 1:
        h, s, v = colorsys.rgb_to_hsv(*(c / 255 for c in palette[0]))
        return [palette[0], _hsv(h + 0.12, max(s, 0.6))]
    return palette


COLOUR_MODES = ("artwork", "rainbow", "fixed")
# Rainbow: one trip round the colours takes this long when the song is calm...
RAINBOW_CALM = 40.0
# ...and this long at its most intense.
RAINBOW_INTENSE = 5.0
VIDEO_FPS = 12  # pixel video light colours: one per 1/12 s
FADE = FPS // 2  # artwork colour changes cross-fade over half a second


def parse_hex(value):
    value = str(value or "").lstrip("#")
    if len(value) == 6:
        try:
            return tuple(int(value[i:i + 2], 16) for i in (0, 2, 4))
        except ValueError:
            pass
    return FALLBACK


# --- haptics ------------------------------------------------------------------------------
# Taps go through the gamepad's standard Linux force-feedback interface: on
# SteamOS handhelds that's InputPlumber's virtual controller, which passes
# rumble on to the real motors. (InputPlumber's own D-Bus Rumble call is
# refused for background services like Decky plugins; this device is open to
# the logged-in user's account.)

TAP_STRENGTH = 0.6   # chosen on the Legion Go S: 0.4 is too faint to feel
STRONG_STRENGTH = 0.8  # strong mode's big hits and drops
HAPTIC_MODES = ("off", "subtle", "strong")
TAP_LENGTH_MS = 70   # a short thump, not a buzz (preferred over 100 ms)

EVIOCSFF = 0x40304580   # upload an effect (struct ff_effect is 48 bytes on 64-bit)
EVIOCRMFF = 0x40044581  # remove an effect
EV_FF, FF_RUMBLE = 0x15, 0x50


def find_rumble_device():
    """A writable input device with force feedback, or None."""
    import glob
    for folder in sorted(glob.glob("/sys/class/input/event*")):
        try:
            with open(os.path.join(folder, "device", "capabilities", "ff")) as f:
                ff = f.read().split()
        except OSError:
            continue
        device = "/dev/input/" + os.path.basename(folder)
        if any(int(word, 16) for word in ff) and os.access(device, os.W_OK):
            return device
    return None


def haptics_available():
    return find_rumble_device() is not None


class Rumble:
    """One uploaded tap effect on the rumble device; re-opened if it goes away
    (e.g. the controller reconnects)."""

    def __init__(self, logger=None):
        self.logger = logger
        self.fd = None
        self.effect = None    # the effect playing (normal or strong)
        self.effects = []     # [normal, strong]
        self.warned = False

    def _open(self):
        import fcntl
        import struct
        device = find_rumble_device()
        if not device:
            raise OSError("no rumble device")
        fd = os.open(device, os.O_RDWR | os.O_NONBLOCK)
        effects = []
        try:
            for strength in (TAP_STRENGTH, STRONG_STRENGTH):
                magnitude = int(65535 * strength)
                effect = bytearray(struct.pack("HhHHHHH2x", FF_RUMBLE, -1, 0, 0, 0, TAP_LENGTH_MS, 0)
                                   + struct.pack("HH", magnitude, magnitude) + bytes(28))
                fcntl.ioctl(fd, EVIOCSFF, effect)
                effects.append(struct.unpack_from("h", effect, 2)[0])
        except OSError:
            os.close(fd)
            raise
        self.fd, self.effects = fd, effects
        self.effect = effects[0]

    def _send(self, value):
        import struct
        os.write(self.fd, struct.pack("qqHHi", 0, 0, EV_FF, self.effect, value))

    def start(self, strong=False):
        for attempt in (1, 2):
            try:
                if self.fd is None:
                    self._open()
                self.effect = self.effects[1 if strong else 0]
                self._send(1)
                return True
            except OSError as error:
                self.close()
                if attempt == 2 and not self.warned and self.logger:
                    self.warned = True
                    self.logger.info(f"haptics: rumble failed: {error}")
        return False

    def stop(self):
        # InputPlumber's virtual pad ignores the effect's length and keeps
        # rumbling, so every tap is stopped explicitly.
        if self.fd is not None:
            try:
                self._send(0)
            except OSError:
                self.close()

    def close(self):
        if self.fd is not None:
            import fcntl
            for effect in self.effects:
                try:
                    self.effect = effect
                    self._send(0)
                    fcntl.ioctl(self.fd, EVIOCRMFF, effect)
                except OSError:
                    pass
            try:
                os.close(self.fd)
            except OSError:
                pass
        self.fd = self.effect = None
        self.effects = []


async def tap(strong=False):
    """One short thump: start, wait TAP_LENGTH_MS, stop (always)."""
    if _rumble is None or not _rumble.start(strong):
        return
    try:
        await asyncio.sleep(TAP_LENGTH_MS / 1000)
    finally:
        _rumble.stop()


_rumble = None


# --- driver ---------------------------------------------------------------------------------

class Lights:
    def __init__(self, player, state_file, logger=None, art_lookup=None, video_lookup=None):
        self.player = player
        # "Video" light colours: the current song's pixel video colours, if it has one.
        self.video_lookup = video_lookup or (lambda: None)
        self.art_lookup = art_lookup or (lambda: None)  # artwork URL of the current song
        self.state_file = state_file  # the user's own lighting, while we hold the rings
        self.logger = logger
        global _rumble
        _rumble = Rumble(logger)
        self.enabled = False          # lights
        self.haptics = "off"          # "feel the beat": off / subtle / strong
        self.haptics_available = False
        self.taps = []
        self.beats = []
        self.levels = []
        self.energy = []
        self.hue = []                 # rainbow position per frame (0-1 = once round)
        self.changes = []
        self.palette = [FALLBACK]
        self.mode = "artwork"
        self.fixed = FALLBACK
        self.song = None
        self.task = None
        self.analysis = None
        self.holding = False
        self.delay = 0.0              # output delay (Bluetooth ~0.2 s): lights wait for the sound
        self.delay_check = None
        self.recover()

    @property
    def available(self):
        return available()

    def recover(self):
        """Put the user's lighting back if a previous run never did."""
        if os.path.exists(self.state_file):
            self.holding = True
            self.release()

    @property
    def active(self):
        return self.enabled or self.haptics != "off"

    def _ensure_running(self):
        if self.active and (self.task is None or self.task.done()):
            self.task = asyncio.ensure_future(self._run())

    def set_enabled(self, on):
        self.enabled = bool(on) and self.available
        if not self.enabled:
            self.release()
        self._ensure_running()

    def set_haptics(self, mode):
        self.haptics = mode if mode in HAPTIC_MODES and self.haptics_available else "off"
        if self.haptics == "off" and _rumble is not None:
            _rumble.close()
        self._ensure_running()

    def set_colours(self, mode, fixed):
        self.mode = mode if mode in COLOUR_MODES else "artwork"
        self.fixed = parse_hex(fixed)

    def song_changed(self, path, art_url):
        """Analyse the new song in the background; lights follow when ready."""
        if path == self.song:
            return
        self.song = path
        self.levels = []
        self.taps = []
        self.beats = []
        if self.analysis and not self.analysis.done():
            self.analysis.cancel()
        if self.active:
            self.analysis = asyncio.ensure_future(self._analyse(path, art_url))

    async def _analyse(self, path, art_url):
        try:
            result = await asyncio.to_thread(analyse, path)
            palette = await asyncio.to_thread(palette_for, art_url)
        except asyncio.CancelledError:
            raise
        except Exception as error:
            if self.logger:
                self.logger.info(f"lights: analysis failed: {error}")
            return
        if path == self.song:
            self.energy = result["energy"]
            self.hue = rainbow_hues(self.energy)
            self.changes = result["changes"]
            self.taps = result["taps"]
            self.beats = result["beats"]
            if self.logger and self.haptics != "off":
                self.logger.info(f"haptics: {len(self.taps)} taps for {os.path.basename(path)}")
            self.palette = with_partner(palette)
            self.levels = result["levels"]

    def _take(self):
        if self.holding:
            return
        saved = {name: _read(name) for name in SAVED_FIELDS if os.path.exists(os.path.join(LED, name))}
        with open(self.state_file, "w") as f:
            json.dump(saved, f)
        self.holding = True
        _write("effect", "monocolor")
        _write("brightness", 100)

    def release(self):
        """Give the rings back exactly as the user had them."""
        if not self.holding:
            return
        try:
            with open(self.state_file) as f:
                saved = json.load(f)
            for name in SAVED_FIELDS:  # effect first, then colour and brightness
                if name in saved:
                    _write(name, saved[name])
            os.remove(self.state_file)
        except Exception as error:
            if self.logger:
                self.logger.info(f"lights: restore failed: {error}")
        self.holding = False

    async def _check_delay(self):
        delay = await self.player.output_delay()
        if delay is not None:
            if self.logger and abs(delay - self.delay) > 0.02:
                self.logger.info(f"lights: output delay {delay * 1000:.0f} ms")
            self.delay = delay

    async def _run(self):
        idle = 0
        last_index = None
        frames = 0
        try:
            while self.active:
                # Songs resumed without going through song_changed (e.g. after a restart).
                if self.player.playing and self.player.file and self.player.file != self.song:
                    self.song_changed(self.player.file, self.art_lookup())
                if self.player.playing and self.levels:
                    idle = 0
                    # Re-check the output every 5 s (headphones can connect mid-song).
                    if frames % (FPS * 5) == 0 and (self.delay_check is None or self.delay_check.done()):
                        self.delay_check = asyncio.ensure_future(self._check_delay())
                    frames += 1
                    position = self.player.position() + LEAD - self.delay
                    index = min(max(int(position * FPS), 0), len(self.levels) - 1)
                    if self.enabled:
                        try:
                            self._take()
                            _write("multi_intensity", self._frame(self._colour(index, position), self.levels[index]))
                        except OSError:
                            pass
                    elif self.holding:
                        self.release()
                    # A tap for each tap point passed since the last frame, but
                    # not after a seek (a jump of more than a few frames).
                    if self.haptics != "off" and last_index is not None and 0 < index - last_index <= 3:
                        if self.haptics == "strong":
                            due = [strong for t, strong in self.beats if last_index < t <= index]
                            if due:
                                asyncio.ensure_future(tap(any(due)))
                        elif any(last_index < t <= index for t in self.taps):
                            asyncio.ensure_future(tap())
                    last_index = index
                else:
                    last_index = None
                    # Short gaps (next song loading) keep the rings; a real
                    # pause or stop gives them back.
                    idle += 1
                    if idle > FPS * 2:
                        self.release()
                await asyncio.sleep(1 / FPS)
        finally:
            self.release()

    @staticmethod
    def _frame(colour, level):
        """RGB for one frame. Up to 1: the colour at that level. Above 1: full
        colour pushed towards white, so the peak of a flash is white-hot."""
        if level > 1.0:
            white = min((level - 1.0) / 0.6, 1.0) * 0.6   # up to 60% white at the peak
            colour = _mix(colour, (255, 255, 255), white)
            level = 1.0
        r, g, b = colour
        return f"{int(r * level)} {int(g * level)} {int(b * level)}"

    def _colour(self, index, position):
        if self.mode == "fixed":
            return self.fixed
        if self.mode == "rainbow":
            # Flows round the colours, faster as the song gets more intense.
            if index < len(self.hue):
                return _hsv(self.hue[index])
            return _hsv(position / 24.0)  # before the song is analysed: a steady pace
        # Artwork: a song with a TV mode video follows the video's colours
        # (like Ambilight); otherwise the artwork's.
        colours = self.video_lookup()
        if colours:
            # Average a few frames either side (~0.4 s) so cuts blend instead of flickering.
            frame = int(position * VIDEO_FPS)
            near = colours[max(0, frame - 2):frame + 3] or colours[-1:]
            r, g, b = (sum(c[k] for c in near) / len(near) for k in range(3))
            h, s, v = colorsys.rgb_to_hsv(r / 255, g / 255, b / 255)
            if s < 0.15:
                return _mix(self._artwork(index), (int(r), int(g), int(b)), 0.3)  # grey scene: lean on the artwork
            return _hsv(h, min(1.0, s * 1.4), 1.0)  # LEDs need it vivid and bright
        return self._artwork(index)

    def _artwork(self, index):
        # Artwork: step through the palette at each big moment, cross-fading.
        palette = self.palette
        if len(palette) == 1:
            return palette[0]
        passed = [c for c in self.changes if c <= index]
        step = len(passed)
        current = palette[step % len(palette)]
        if passed and index - passed[-1] < FADE:
            previous = palette[(step - 1) % len(palette)]
            return _mix(previous, current, (index - passed[-1]) / FADE)
        return current

    def shutdown(self):
        self.enabled = False
        self.haptics = "off"
        if _rumble is not None:
            _rumble.close()
        if self.analysis and not self.analysis.done():
            self.analysis.cancel()
        if self.task and not self.task.done():
            self.task.cancel()
        self.release()
