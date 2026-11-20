# Third-party software

Cuivar Pulse is licensed under the GNU General Public License v3.0 (see `LICENSE`).
It includes or downloads the following open-source software, each under its own licence.

## Programs (downloaded at build time from their official releases)

| Software | Version | Licence | Source |
|---|---|---|---|
| yt-dlp | 2026.10.26 | The Unlicense (public domain) | https://github.com/yt-dlp/yt-dlp |
| QuickJS-ng (`qjs`) | 0.18.0 | MIT | https://github.com/quickjs-ng/quickjs |

## Voice search (downloaded on first use from the official sherpa-onnx releases, not shipped in the plugin)

| Software | Version | Licence | Source |
|---|---|---|---|
| sherpa-onnx (`sherpa-onnx-offline`) | 2.0.2 | Apache-2.0 | https://github.com/k2-fsa/sherpa-onnx |
| ONNX Runtime (`libonnxruntime.so`) | bundled with sherpa-onnx 2.0.2 | MIT | https://github.com/microsoft/onnxruntime |
| Whisper tiny model (int8 ONNX export by sherpa-onnx) | tiny | MIT (OpenAI) | https://github.com/openai/whisper |

PipeWire's `pw-record` (part of SteamOS) records the microphone while Y is held.

## Python libraries (in `py_modules/`; each one's full licence is in its `*.dist-info/licenses/` folder)

| Library | Version | Licence |
|---|---|---|
| ytmusicapi | 1.12.4 | MIT |
| requests | 2.34.2 | Apache-2.0 (see also its `NOTICE`) |
| urllib3 | 2.8.0 | MIT |
| charset-normalizer | 3.5.2 | MIT |
| idna | 3.20 | BSD-3-Clause |
| certifi | 2026.10.23 | MPL-2.0 |

## Frontend (bundled into `dist/index.js`)

| Library | Licence |
|---|---|
| react-icons | MIT (Material Design icons: Apache-2.0; Font Awesome icons: CC BY 4.0) |
| @decky/api, @decky/ui | LGPL-2.1 (Decky Loader project) |

## Used from the system (not shipped)

ffmpeg and PipeWire (`pw-cat`, `pactl`) come with SteamOS and are only called as separate programs.

## Not affiliated

Cuivar Pulse is an independent, unofficial project. It is not affiliated with, endorsed by or
connected to YouTube, Google, Valve or Lenovo. All trademarks belong to their owners.
