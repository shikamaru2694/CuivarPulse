"""Cuivar Pulse for SteamOS: backend (runs inside Decky Loader as the deck user)."""

import os

# The only environment variables passed on to the programs the plugin runs
# (ffmpeg, PipeWire, yt-dlp, QuickJS, the voice engine). Nothing else from the
# plugin's environment is handed over.
PASSED_ON = ("PATH", "HOME", "USER", "LANG", "LANGUAGE", "LC_ALL", "LC_CTYPE", "LC_MESSAGES",
             "TZ", "TMPDIR", "XDG_RUNTIME_DIR", "XDG_CACHE_HOME", "PULSE_SERVER", "PIPEWIRE_REMOTE")


def child_env():
    """Environment for external programs: just what they need to run.

    Not copied from the plugin's own environment: Decky's bundled Python sets
    LD_LIBRARY_PATH for itself (it breaks system programs), and nothing else
    there is any business of ffmpeg or yt-dlp.
    """
    env = {name: os.environ[name] for name in PASSED_ON if os.environ.get(name)}
    env.setdefault("PATH", "/usr/local/bin:/usr/bin:/bin")
    env.setdefault("HOME", os.path.expanduser("~"))
    env.setdefault("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}")  # where PipeWire listens
    return env
