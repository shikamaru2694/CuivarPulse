"""Builds the release ZIP: out/CuivarPulse-<version>.zip

    python scripts/package.py

Steps: build the frontend, make sure bin/ holds the exact programs listed
under "remote_binary" in package.json (downloading and checking their SHA-256
if needed), then zip only what the plugin needs to run. The ZIP installs via
Decky's "Install plugin from ZIP" (developer mode)."""

import hashlib
import json
import os
import shutil
import subprocess
import sys
import urllib.request
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FOLDER = "CuivarPulse"  # folder name inside the ZIP (= plugin folder on the device)

FILES = ["plugin.json", "package.json", "main.py", "LICENSE", "README.md", "THIRD-PARTY-NOTICES.md"]
DIRS = ["dist", "cuivar", "py_modules", "bin"]
SKIP = {"__pycache__", ".DS_Store", "Thumbs.db"}


def sha256(path):
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def fetch_binaries(package):
    os.makedirs(os.path.join(ROOT, "bin"), exist_ok=True)
    for item in package.get("remote_binary", []):
        path = os.path.join(ROOT, "bin", item["name"])
        if os.path.exists(path) and sha256(path) == item["sha256hash"]:
            print(f"  bin/{item['name']}: ok")
            continue
        print(f"  bin/{item['name']}: downloading {item['url']}")
        partial = path + ".part"
        urllib.request.urlretrieve(item["url"], partial)
        actual = sha256(partial)
        if actual != item["sha256hash"]:
            os.remove(partial)
            sys.exit(f"  checksum mismatch for {item['name']}: got {actual}")
        os.replace(partial, path)


def main():
    with open(os.path.join(ROOT, "package.json"), encoding="utf-8") as f:
        package = json.load(f)
    version = package["version"]

    print("Building frontend…")
    subprocess.run("npm run build", shell=True, cwd=ROOT, check=True)
    print("Checking programs…")
    fetch_binaries(package)

    for name in FILES + DIRS:
        if not os.path.exists(os.path.join(ROOT, name)):
            sys.exit(f"missing: {name}")

    out_dir = os.path.join(ROOT, "out")
    os.makedirs(out_dir, exist_ok=True)
    zip_path = os.path.join(out_dir, f"{FOLDER}-{version}.zip")
    if os.path.exists(zip_path):
        os.remove(zip_path)

    def add(zf, path):
        rel = os.path.relpath(path, ROOT).replace(os.sep, "/")
        info = zipfile.ZipInfo.from_file(path, f"{FOLDER}/{rel}")
        executable = rel.startswith("bin/")
        info.external_attr = ((0o755 if executable else 0o644) | 0o100000) << 16
        info.compress_type = zipfile.ZIP_DEFLATED
        with open(path, "rb") as f:
            zf.writestr(info, f.read())

    count = 0
    with zipfile.ZipFile(zip_path, "w") as zf:
        for name in FILES:
            add(zf, os.path.join(ROOT, name))
            count += 1
        for name in DIRS:
            for folder, subdirs, files in os.walk(os.path.join(ROOT, name)):
                subdirs[:] = sorted(d for d in subdirs if d not in SKIP)
                for file in sorted(files):
                    if file in SKIP or file.endswith((".pyc", ".part")):
                        continue
                    add(zf, os.path.join(folder, file))
                    count += 1

    size = os.path.getsize(zip_path) / 1024 / 1024
    print(f"\n{zip_path}\n{count} files, {size:.1f} MB")


if __name__ == "__main__":
    main()
