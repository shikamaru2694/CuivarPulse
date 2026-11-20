"""Cuivar Pulse for SteamOS: backend (runs inside Decky Loader as the deck user)."""

import os


def child_env():
    """Environment for external programs (ffmpeg, pw-cat, yt-dlp).

    Decky's bundled Python sets LD_LIBRARY_PATH to its own libraries, which
    breaks system programs, and the plugin service may not have the user's
    runtime dir that PipeWire needs to find the sound server.
    """
    env = dict(os.environ)
    env.pop("LD_LIBRARY_PATH", None)
    env.setdefault("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}")
    env.setdefault("HOME", os.path.expanduser("~"))
    return env
