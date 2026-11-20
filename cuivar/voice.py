"""Voice search: hold Y, say a song, let go.

Speech becomes text on the device itself, with sherpa-onnx (Apache-2.0)
running OpenAI's Whisper "tiny" model (MIT). Nothing you say leaves the
handheld. The engine and model are a one-time download (~135 MB) the first
time voice search is used, so the plugin itself stays small."""

import asyncio
import hashlib
import json
import os
import re
import shutil
import signal
import tarfile
import time

from . import child_env

_RELEASES = "https://github.com/k2-fsa/sherpa-onnx/releases/download"
# (url, sha256, {path inside the archive: where it goes}) for each download.
DOWNLOADS = [
    (f"{_RELEASES}/v2.0.2/sherpa-onnx-v2.0.2-linux-x64-shared-no-tts.tar.bz2",
     "c6725b8994a5e559678f72ddffbf1c14b41fc3f6f650618b98eeb61581b63da2",
     {"sherpa-onnx-v2.0.2-linux-x64-shared-no-tts/bin/sherpa-onnx-offline": "bin/sherpa-onnx-offline",
      "sherpa-onnx-v2.0.2-linux-x64-shared-no-tts/lib/libonnxruntime.so": "lib/libonnxruntime.so"}),
    (f"{_RELEASES}/asr-models/sherpa-onnx-whisper-tiny.tar.bz2",
     "c46116994e539aa165266d96b325252728429c12535eb9d8b6a2b10f129e66b1",
     {"sherpa-onnx-whisper-tiny/tiny-encoder.int8.onnx": "model/encoder.onnx",
      "sherpa-onnx-whisper-tiny/tiny-decoder.int8.onnx": "model/decoder.onnx",
      "sherpa-onnx-whisper-tiny/tiny-tokens.txt": "model/tokens.txt"}),
]
DOWNLOAD_MB = 135
EXPECTED_BYTES = 26_000_000 + 115_000_000  # both archives, for the progress bar
MAX_SECONDS = 10      # recording stops by itself after this
MIN_SECONDS = 0.4     # shorter than this is a tap, not speech
DUCK = 0.2            # music plays at 20% of its volume while listening
# What Whisper "hears" in silence or noise; never worth searching for.
HALLUCINATIONS = {"", "you", "thank you", "thanks for watching", "bye", "music"}


class VoiceError(Exception):
    pass


def tidy(text):
    """Clean the model's text into a search: collapse a phrase it repeated
    ("Hello by Adele. Hello by Adele. Hello by…") and drop end punctuation.
    Single repeated words stay ("Bye Bye Bye" is a real song). Sound tags
    Whisper adds for non-speech ("[music]", "(applause)", "*sad music*", ♪)
    are dropped."""
    text = re.sub(r"[\[(][^\])]*[\])]|\*[^*]*\*|[♪♫]", " ", text)
    words = text.split()
    for size in range(2, len(words) // 2 + 1):
        unit = [w.strip(".,!?").lower() for w in words[:size]]
        rest = [w.strip(".,!?").lower() for w in words]
        if all(rest[i] == unit[i % size] or (i >= len(words) - size and unit[i % size].startswith(rest[i]))
               for i in range(len(words))):
            words = words[:size]
            break
    text = " ".join(words).strip().strip(".,!?¿¡ ")
    return re.sub(r"\s+", " ", text)


class Voice:
    def __init__(self, data_dir, player, logger=None):
        self.dir = os.path.join(data_dir, "voice")
        self.player = player
        self.logger = logger
        self.progress = None   # 0-1 while downloading
        self.error = None
        self.task = None
        self.recorder = None
        self.started = None
        self.auto_stop = None

    def _path(self, name):
        return os.path.join(self.dir, name)

    @property
    def ready(self):
        return all(os.path.exists(self._path(target)) for _, _, files in DOWNLOADS for target in files.values())

    def status(self):
        return {"ready": self.ready, "downloading": self.progress is not None,
                "progress": self.progress or 0, "error": self.error, "sizeMb": DOWNLOAD_MB,
                "listening": self.recorder is not None}

    # --- one-time download -------------------------------------------------------------

    def prepare(self):
        if not self.ready and (self.task is None or self.task.done()):
            self.error = None
            self.progress = 0.0
            self.task = asyncio.ensure_future(self._download_all())
        return self.status()

    async def _download_all(self):
        try:
            await asyncio.to_thread(self._download_sync)
        except Exception as error:
            self.error = "Couldn't download voice search. Check your connection and try again."
            if self.logger:
                self.logger.info(f"voice: download failed: {error}")
        finally:
            self.progress = None

    def _download_sync(self):
        import requests  # bundled in py_modules
        os.makedirs(self.dir, exist_ok=True)
        staging = self._path("download.tmp")
        received = 0
        for url, sha, files in DOWNLOADS:
            digest = hashlib.sha256()
            with requests.get(url, stream=True, timeout=30) as response:
                response.raise_for_status()
                with open(staging, "wb") as out:
                    for chunk in response.iter_content(1 << 20):
                        out.write(chunk)
                        digest.update(chunk)
                        received += len(chunk)
                        self.progress = min(received / EXPECTED_BYTES, 0.99)
            if digest.hexdigest() != sha:
                os.remove(staging)
                raise VoiceError(f"checksum mismatch for {url}")
            with tarfile.open(staging, "r:bz2") as archive:
                for member in archive:
                    target = files.get(member.name)
                    if not target or not member.isfile():
                        continue
                    destination = self._path(target)
                    os.makedirs(os.path.dirname(destination), exist_ok=True)
                    with archive.extractfile(member) as source, open(destination + ".part", "wb") as out:
                        shutil.copyfileobj(source, out)
                    os.replace(destination + ".part", destination)
            os.remove(staging)
        os.chmod(self._path("bin/sherpa-onnx-offline"), 0o755)

    def remove(self):
        if self.task and not self.task.done():
            self.task.cancel()
        shutil.rmtree(self.dir, ignore_errors=True)
        return self.status()

    # --- listening ---------------------------------------------------------------------

    async def start(self):
        if not self.ready:
            raise VoiceError("Voice search isn't downloaded yet.")
        if self.recorder is not None:
            return
        os.makedirs(self.dir, exist_ok=True)
        clip = self._path("clip.wav")
        if os.path.exists(clip):
            os.remove(clip)
        self.recorder = await asyncio.create_subprocess_exec(
            "pw-record", "--rate", "16000", "--channels", "1", "--format", "s16", clip,
            stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL,
            env=child_env(), start_new_session=True,
        )
        self.started = time.monotonic()
        # Turn the music down so the mic hears you, not the speakers.
        if self.player.playing:
            await self.player.duck(DUCK)
        self.auto_stop = asyncio.ensure_future(self._stop_recorder_later())

    async def _stop_recorder_later(self):
        await asyncio.sleep(MAX_SECONDS)
        await self._stop_recorder()

    async def _stop_recorder(self):
        recorder, self.recorder = self.recorder, None
        if recorder is None:
            return
        if recorder.returncode is None:
            recorder.send_signal(signal.SIGINT)  # lets pw-record finish the WAV file
            try:
                await asyncio.wait_for(recorder.wait(), 2)
            except asyncio.TimeoutError:
                recorder.kill()
        await self.player.duck(None)

    async def stop(self):
        """Stop listening and return what was said ("" if nothing usable)."""
        if self.auto_stop:
            self.auto_stop.cancel()
            self.auto_stop = None
        was_listening = self.recorder is not None
        await self._stop_recorder()
        length = time.monotonic() - (self.started or time.monotonic())
        if not was_listening and not os.path.exists(self._path("clip.wav")):
            return ""
        if length < MIN_SECONDS:
            return ""
        return await self.transcribe(self._path("clip.wav"))

    async def transcribe(self, wav):
        env = child_env()
        env["LD_LIBRARY_PATH"] = self._path("lib")
        process = await asyncio.create_subprocess_exec(
            self._path("bin/sherpa-onnx-offline"), "--num-threads=4",
            f"--whisper-encoder={self._path('model/encoder.onnx')}",
            f"--whisper-decoder={self._path('model/decoder.onnx')}",
            f"--tokens={self._path('model/tokens.txt')}", wav,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL, env=env,
        )
        try:
            out, _ = await asyncio.wait_for(process.communicate(), 20)
        except asyncio.TimeoutError:
            process.kill()
            raise VoiceError("Voice search took too long.")
        text = ""
        for line in out.decode("utf-8", "replace").splitlines():
            if line.startswith("{"):
                try:
                    text = json.loads(line).get("text") or ""
                except ValueError:
                    pass
        text = tidy(text)
        return "" if text.lower() in HALLUCINATIONS else text

    async def shutdown(self):
        if self.auto_stop:
            self.auto_stop.cancel()
        await self._stop_recorder()
