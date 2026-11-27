# Performance and battery

Cuivar Pulse runs in the background while you game, so it has to stay light. These
numbers were measured on a real handheld with the music playing, not estimated.

**Short version:** while music plays, Cuivar Pulse uses **about 1–3% of one CPU core**
(well under 1% of the whole processor) and **about 120 MB of RAM**. That's the same
whether you're gaming or not. Its only heavy work is short and one-off: the first time
TV mode shows a song's video (about a minute), and voice search (under a second).

## Test setup

- **Device:** Lenovo Legion Go S (AMD Ryzen Z2 Go, 8 threads, 16 GB RAM, 55.5 Wh battery), SteamOS 3.9.5, Decky Loader 3.3.1
- **Version:** the development build after 1.0.0 (with TV mode), playing downloaded songs
- **Method:** [`tests/perf_sample.py`](../tests/perf_sample.py) reads Linux's own counters every second for 25–75 s per scenario:
  - **CPU:** includes everything Cuivar Pulse starts (the player, the music engine);
  - **RAM:** proportional set size (PSS), the honest way to count shared memory;
  - **power:** the battery's own power sensor, unplugged.

  Settings were switched by script, with the handheld untouched.

## Results

| Scenario | CPU (% of one core) | RAM | Notes |
|---|---|---|---|
| Installed, nothing playing | **0.5%** | 78 MB | Just waiting. No network, no disk activity |
| Playing music | **1.2%** | 118 MB | Includes the audio player (ffmpeg + PipeWire) |
| Playing + controller lights | **3%** | 118 MB | Updating the joystick rings 30 times a second |
| Playing + Feel the beat (Strong) | **2.6%** | 120 MB | Measured with the lights off. Short rumble taps on the beat |
| Starting a song | about 1 CPU-second in total | up to 136 MB for a moment | One-off: the song's beats are worked out once, for the lights and rumble |
| Panel open, TV mode (pixel video playing) | 2.8% | 116 MB | The video is drawn in Steam's interface: about **+8% of one core** there, compared with the panel open in Music mode |
| TV mode preparing a song's video | **about 32 CPU-seconds**, over about 75 s | up to 440 MB, briefly | One-off per song. Runs at the lowest priority, only while TV mode is on screen |
| Voice search | about 0.6 s per search | — | Only while you hold Y. Speech is turned into text on the device |

"% of one core" means **3% is about 0.4% of the whole processor** (8 threads).

## Battery

Measured from the battery's own sensor, unplugged, with the screen on and brightness fixed:

- **Playing music adds about 0.4–0.8 W** compared with paused. Most of that is the
  speakers and the audio chip actually playing sound, not Cuivar Pulse's code.
- **Controller lights:** no difference we could measure. Switching them off and on twice
  moved the reading by less than the normal ±0.4 W noise.
- **Feel the beat (Strong):** about 0.3 W more, which is within the noise. Subtle taps far less often.
- **TV mode** with the panel open: about **0.2 W** more than Music mode.
- **Preparing a TV mode video:** about **15 W for roughly 75 s**, once per song. That's
  about 0.3 Wh, or 0.5% of the battery.

**In practice:** during a game drawing around 20 W, adding 0.5 W for music is about
**4 minutes less on a ~2 h 45 min charge**. Lights and Feel the beat don't change that noticeably.

## What it never does

- **No TV mode work while you play a game.** With a game running, there's no video at
  all, only audio. TV mode also stops everything within 8 seconds of the panel closing.
- **No background downloads you didn't ask for.** Songs are fetched when you play them
  (plus the next one or two, so they start instantly). Pixel videos are fetched only while TV mode is on screen.
- **No work when nothing is playing.** The lights hand the rings back to your own setting
  after 2 seconds, and no analysis runs.

## Data and storage

| What | Size |
|---|---|
| The plugin itself | 51 MB (mostly the music engine and its libraries) |
| Music engine, copied to the data folder | 42 MB |
| Automatic song cache | up to 1 GB, cleans itself up (Settings → Clear cache) |
| Downloads | your choice: a song is about 3–5 MB; set a limit in Settings |
| TV mode videos | about 1.5–3 MB per song, automatic cache up to 200 MB |
| Voice search (optional) | 128 MB, downloaded only if you use it; removable in Settings |

**Network:** a song is about 3–5 MB the first time you play it. TV mode fetches about
6–9 MB more per song, once (a tiny 144p video plus its audio, used to line the video up with the song).

## How to turn things down

- **Lowest use:** Settings → Lights dance to the music **off**, Feel the beat **Off**, and
  stay in **Music** mode. Playback alone is about 1% of one core.
- **Clear cache** in Settings frees the song and TV mode caches at any time. Downloads are never removed without asking.

## Measure it yourself

Copy [`tests/perf_sample.py`](../tests/perf_sample.py) to your handheld, play something
in Cuivar Pulse, and run:

```
python3 perf_sample.py 30
```

It prints the plugin's CPU, RAM and (on battery) the device's power for those 30 seconds.
If your numbers look very different, please open an issue with them and your device.
