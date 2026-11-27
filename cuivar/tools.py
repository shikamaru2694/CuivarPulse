"""The music engine's programs. They're downloaded on first launch, not shipped in the plugin ZIP.

  yt-dlp  The official release from github.com/yt-dlp/yt-dlp:
          - its pure-Python build (readable Python, about 3 MB), run with the
            system's Python 3.10+ (SteamOS, Bazzite and most distros have one);
          - the official Linux build when no such Python is found.
          Checked against the SHA2-256SUMS file published with the same release.
  qjs     QuickJS-ng, the small JavaScript engine yt-dlp needs for YouTube, from
          github.com/quickjs-ng/quickjs. Pinned version, checked against the
          SHA-256 below.

Updates use the same download and check. A new yt-dlp is tried on a real
YouTube video before it replaces the working one, and the previous version is
kept, so a bad update never breaks playback. The plugin looks for a newer
yt-dlp once a day, and right away when YouTube changes something
(engine.ENGINE_OUTDATED).
"""

import asyncio
import hashlib
import json
import os
import shutil
import subprocess
import time

from . import child_env

YTDLP_RELEASES = "https://github.com/yt-dlp/yt-dlp/releases"
QJS = {
    "version": "v0.18.0",
    "url": "https://github.com/quickjs-ng/quickjs/releases/download/v0.18.0/qjs-linux-x86_64",
    "sha256": "85ded23b9fabe8c1644fc40a8d2bbdd71157affcbd807359c618cc717ee24d7d",
}
CHECK_EVERY = 24 * 3600          # quiet check for a newer yt-dlp, at most this often
AUTO_UPDATE_GAP = 30 * 60        # after YouTube changes: at most one auto-update per half hour
TEST_VIDEO = "jNQXAC9IVRw"       # "Me at the zoo": a stable video to try a new yt-dlp on
NO_INTERNET = "Couldn't set up the music engine. Check your internet connection."


class ToolsError(Exception):
    pass


def fetch(url, progress=None, timeout=60):
    """Downloads a URL (following GitHub's redirects) and returns the bytes.
    Tests swap this for a local stand-in."""
    import requests  # bundled in py_modules
    chunks, received = [], 0
    with requests.get(url, stream=True, timeout=timeout) as response:
        response.raise_for_status()
        total = int(response.headers.get("content-length") or 0)
        for chunk in response.iter_content(1 << 16):
            chunks.append(chunk)
            received += len(chunk)
            if progress and total:
                progress(received / total)
    return b"".join(chunks)


def latest_ytdlp_tag():
    """The newest yt-dlp release, e.g. "2026.11.23" (GitHub's /latest redirect,
    so no API key or rate-limited API call is needed)."""
    import requests
    response = requests.head(f"{YTDLP_RELEASES}/latest", allow_redirects=False, timeout=20)
    location = response.headers.get("location", "")
    if "/tag/" not in location:
        raise ToolsError("Couldn't find the latest yt-dlp release.")
    return location.rsplit("/tag/", 1)[1].strip()


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def find_python():
    """A system Python 3.10+ with SSL, to run the pure-Python yt-dlp; None if there's none."""
    env = child_env()
    candidates = [shutil.which("python3", path=env.get("PATH")), "/usr/bin/python3", "/usr/local/bin/python3"]
    for path in dict.fromkeys(c for c in candidates if c):
        try:
            result = subprocess.run(
                [path, "-c", "import ssl, sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)"],
                env=env, capture_output=True, timeout=15)
        except (OSError, subprocess.SubprocessError):
            continue
        if result.returncode == 0:
            return path
    return None


class Tools:
    def __init__(self, data_dir, logger=None, on_change=None):
        self.dir = os.path.join(data_dir, "bin")
        self.state_file = os.path.join(self.dir, "tools.json")
        self.logger = logger
        self.on_change = on_change or (lambda: None)
        os.makedirs(self.dir, exist_ok=True)
        try:
            with open(self.state_file) as f:
                self.state = json.load(f)
        except (OSError, ValueError):
            self.state = {}
        self.python = None
        self.status = "ready" if self.ready else "setting_up"
        self.progress = 0.0
        self.message = None
        self.lock = asyncio.Lock()
        self.setup_task = None
        self.last_auto_update = 0.0

    # --- what the engine uses -----------------------------------------------------------

    def _log(self, text):
        if self.logger:
            self.logger.info(f"music engine: {text}")

    @property
    def ytdlp_file(self):
        name = (self.state.get("ytdlp") or {}).get("file")
        path = os.path.join(self.dir, name) if name else None
        if path and os.path.exists(path):
            return path
        # Installs from 1.0.0 have the old bundled copy here: keep using it until set up.
        legacy = os.path.join(self.dir, "yt-dlp_linux")
        return legacy if os.path.exists(legacy) and os.access(legacy, os.X_OK) else None

    @property
    def qjs_file(self):
        path = os.path.join(self.dir, "qjs")
        return path if os.path.exists(path) and os.access(path, os.X_OK) else None

    @property
    def ready(self):
        return bool(self.ytdlp_file and self.qjs_file)

    @property
    def version(self):
        return (self.state.get("ytdlp") or {}).get("version")

    def command(self):
        """How to run yt-dlp, e.g. ["/usr/bin/python3", ".../bin/yt-dlp"]."""
        path = self.ytdlp_file
        if not path:
            return None
        info = self.state.get("ytdlp") or {}
        if info.get("kind") == "python" and info.get("python") and os.path.exists(info["python"]):
            return [info["python"], path]
        return [path]

    def js_runtime(self):
        return f"quickjs:{self.qjs_file}" if self.qjs_file else None

    def public(self):
        """For the panel: ready / setting_up / updating / error, with progress and a message."""
        info = self.state.get("ytdlp") or {}
        return {"state": self.status, "progress": round(self.progress, 2), "message": self.message,
                "version": info.get("version"), "kind": info.get("kind")}

    def _set(self, status, message=None, progress=None):
        self.status, self.message = status, message
        if progress is not None:
            self.progress = progress
        self.on_change()

    def _save(self):
        tmp = self.state_file + ".tmp"
        with open(tmp, "w") as f:
            json.dump(self.state, f)
        os.replace(tmp, self.state_file)

    # --- first launch -------------------------------------------------------------------

    def start_setup(self):
        """Sets the engine up in the background if it isn't yet (first launch, or retry)."""
        if self.setup_task is None or self.setup_task.done():
            self.setup_task = asyncio.ensure_future(self.setup())
        return self.setup_task

    async def setup(self):
        async with self.lock:
            if self.ready and (self.state.get("ytdlp") or {}).get("file"):
                if self.status != "ready":
                    self._set("ready")
                return True
            # Upgrading from 1.0.0: the old copy keeps working meanwhile, so no banner.
            if not self.ready:
                self._set("setting_up", progress=0.0)
            try:
                if not self.qjs_file:
                    await self._install_qjs()
                if not (self.state.get("ytdlp") or {}).get("file"):
                    tag = await asyncio.to_thread(latest_ytdlp_tag)
                    await self._install_ytdlp(tag, try_on_youtube=False)
                self._drop_legacy()
                self._set("ready", progress=1.0)
                self._log(f"set up: yt-dlp {self.version} ({self.state['ytdlp']['kind']}), QuickJS {QJS['version']}")
                return True
            except Exception as error:  # noqa: BLE001 - shown to the user, retried later
                self._log(f"setup failed: {error}")
                if self.ready:  # an older copy still works (installs from 1.0.0)
                    self._set("ready")
                    return True
                self._set("error", NO_INTERNET)
                return False

    async def wait_ready(self):
        """For the engine: set up first if needed; raises ToolsError if that fails."""
        if self.ready:
            return
        await self.start_setup()
        if not self.ready:
            raise ToolsError(self.message or NO_INTERNET)

    def _drop_legacy(self):
        legacy = os.path.join(self.dir, "yt-dlp_linux")
        info = self.state.get("ytdlp") or {}
        if info.get("file") and info["file"] != "yt-dlp_linux" and os.path.exists(legacy):
            os.remove(legacy)

    async def _install_qjs(self):
        data = await asyncio.to_thread(fetch, QJS["url"], lambda p: self._progress(0.0, 0.4, p))
        if sha256(data) != QJS["sha256"]:
            raise ToolsError("QuickJS download didn't match its checksum.")
        self._write("qjs", data, executable=True)

    def _progress(self, start, end, part):
        self.progress = start + (end - start) * part

    def _write(self, name, data, executable=False):
        path = os.path.join(self.dir, name)
        with open(path + ".part", "wb") as f:
            f.write(data)
        if executable:
            os.chmod(path + ".part", 0o755)
        os.replace(path + ".part", path)
        return path

    # --- yt-dlp: install, check, swap ---------------------------------------------------

    async def _install_ytdlp(self, tag, try_on_youtube):
        """Downloads yt-dlp `tag`, checks it, tries it, then makes it the one in use
        (the old one is kept as *.previous). Raises ToolsError and changes nothing
        if any step fails."""
        if self.python is None:
            self.python = await asyncio.to_thread(find_python) or ""
        kind, asset = ("python", "yt-dlp") if self.python else ("binary", "yt-dlp_linux")
        base = f"{YTDLP_RELEASES}/download/{tag}"
        sums = (await asyncio.to_thread(fetch, f"{base}/SHA2-256SUMS")).decode("utf-8", "replace")
        expected = next((line.split()[0] for line in sums.splitlines()
                         if len(line.split()) == 2 and line.split()[1].lstrip("*") == asset), None)
        if not expected:
            raise ToolsError(f"yt-dlp {tag} has no checksum for {asset}.")
        data = await asyncio.to_thread(fetch, f"{base}/{asset}", lambda p: self._progress(0.4, 0.95, p))
        if sha256(data) != expected:
            raise ToolsError(f"yt-dlp {tag} didn't match its published checksum.")

        candidate = self._write(f"{asset}.new", data, executable=(kind == "binary"))
        command = [self.python, candidate] if kind == "python" else [candidate]
        try:
            version = await self._run(command + ["--version"], 30)
            if version.strip() != tag:
                raise ToolsError(f"the new yt-dlp reports version {version.strip()!r}, expected {tag}")
            if try_on_youtube and self.qjs_file:
                await self._run(command + ["--js-runtimes", self.js_runtime(), "--skip-download", "--no-warnings",
                                           "--print", "id", f"https://www.youtube.com/watch?v={TEST_VIDEO}"], 90)
        except Exception:
            os.remove(candidate)
            raise

        current = self.ytdlp_file
        if current and os.path.basename(current) == asset:
            os.replace(current, current + ".previous")
        os.replace(candidate, os.path.join(self.dir, asset))
        self.state["ytdlp"] = {"file": asset, "kind": kind, "python": self.python or None,
                               "version": tag, "installed": time.time()}
        self.state["checked"] = time.time()
        self._save()

    async def _run(self, command, timeout):
        process = await asyncio.create_subprocess_exec(
            *command, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, env=child_env())
        try:
            out, err = await asyncio.wait_for(process.communicate(), timeout)
        except (asyncio.TimeoutError, asyncio.CancelledError):
            process.kill()
            await process.wait()
            raise
        if process.returncode != 0:
            raise ToolsError(f"the new yt-dlp failed its test: {err.decode(errors='replace').strip()[-200:]}")
        return out.decode(errors="replace")

    # --- updates ------------------------------------------------------------------------

    async def update(self, reason="manual", quiet=False):
        """Installs the newest yt-dlp if it's newer than ours (tried first, previous
        kept). Returns a short message; raises ToolsError if the update failed (the
        working version stays in use). `quiet`: no "Updating…" in the panel."""
        async with self.lock:
            was = self.status
            if not quiet:
                self._set("updating", progress=0.0)
            try:
                tag = await asyncio.to_thread(latest_ytdlp_tag)
                self.state["checked"] = time.time()
                self._save()
                if tag == self.version and (self.state.get("ytdlp") or {}).get("file"):
                    return f"Music engine is up to date ({tag})."
                await self._install_ytdlp(tag, try_on_youtube=True)
                self._drop_legacy()
                self._log(f"updated to {tag} ({reason})")
                return f"Music engine updated to {tag}."
            except Exception as error:  # noqa: BLE001
                self._log(f"update failed ({reason}): {error}")
                raise ToolsError("Couldn't update the music engine. The current version is still in use.")
            finally:
                if not quiet or not self.ready:
                    self._set("ready" if self.ready else ("error" if was == "error" else was),
                              None if self.ready else self.message)

    async def auto_update(self):
        """YouTube changed something: update now if there's a newer yt-dlp.
        True if a new version is now in use (worth retrying the song)."""
        if time.time() - self.last_auto_update < AUTO_UPDATE_GAP:
            return False
        self.last_auto_update = time.time()
        before = self.version
        try:
            await self.update("YouTube changed something")
        except ToolsError:
            return False
        return self.version != before

    def due_for_check(self):
        return time.time() - self.state.get("checked", 0) > CHECK_EVERY

    async def check_daily(self, busy):
        """The quiet daily check: only when it's been a day, and nothing is using
        yt-dlp right now (no song or download being fetched)."""
        if not self.ready or not self.due_for_check() or busy() or self.lock.locked():
            return
        try:
            await self.update("daily check", quiet=True)
        except ToolsError:
            pass
