"""Builds the release ZIP: out/CuivarPulse.zip (+ out/SHA256SUMS).

The name has no version in it, so the install link
.../releases/latest/download/CuivarPulse.zip always gets the newest release.

    python scripts/package.py               local test build
    python scripts/package.py --tag v1.1.0  release build (GitHub Actions, see
                                            .github/workflows/release.yml)

Steps: build the panel (dist/index.js) from src/, then zip the files the
plugin needs to run. The source files come from git (tracked files only), so
the ZIP holds exactly what's in the repository at that commit, plus the built
panel. Nothing else: no programs (the music engine is downloaded and checked
on first launch, see cuivar/tools.py) and no source maps.
Files get a fixed date so the same commit always gives the same ZIP.
"""

import argparse
import hashlib
import json
import os
import subprocess
import sys
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FOLDER = "CuivarPulse"  # folder name inside the ZIP (= plugin folder on the device)
FILES = ["plugin.json", "package.json", "main.py", "LICENSE", "README.md", "THIRD-PARTY-NOTICES.md"]
TRACKED_DIRS = ["cuivar", "py_modules"]   # taken from git, as committed
BUILT = ["dist/index.js"]                 # built from src/ by `npm run build`
FIXED_DATE = (2026, 1, 1, 0, 0, 0)


def sha256(path):
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def tracked(folder):
    out = subprocess.run(["git", "ls-files", "-z", "--", folder], cwd=ROOT, capture_output=True, check=True).stdout
    return sorted(path for path in out.decode().split("\0") if path)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--tag", help="release tag; must be v<version from package.json>")
    args = parser.parse_args()

    with open(os.path.join(ROOT, "package.json"), encoding="utf-8") as f:
        version = json.load(f)["version"]
    if args.tag and args.tag != f"v{version}":
        sys.exit(f"Tag {args.tag} doesn't match package.json version {version}.")

    print("Building the panel…")
    subprocess.run("npm run build", shell=True, cwd=ROOT, check=True)

    paths = FILES + [p for d in TRACKED_DIRS for p in tracked(d)] + BUILT
    for path in paths:
        if not os.path.isfile(os.path.join(ROOT, path)):
            sys.exit(f"missing: {path}")

    out_dir = os.path.join(ROOT, "out")
    os.makedirs(out_dir, exist_ok=True)
    name = f"{FOLDER}.zip"
    zip_path = os.path.join(out_dir, name)
    def content(path):
        # A release takes the committed bytes straight from git (the tag), so
        # any computer builds the same ZIP whatever its line-ending settings.
        # A test build takes the files as they are on disk.
        if args.tag and path not in BUILT:
            return subprocess.run(["git", "show", f"{args.tag}:{path}"], cwd=ROOT,
                                  capture_output=True, check=True).stdout
        with open(os.path.join(ROOT, path), "rb") as f:
            return f.read()

    with zipfile.ZipFile(zip_path, "w") as zf:
        for path in paths:
            info = zipfile.ZipInfo(f"{FOLDER}/{path}", FIXED_DATE)
            info.external_attr = (0o644 | 0o100000) << 16
            info.create_system = 3  # "made on Unix" whatever builds it, so the bytes match
            info.compress_type = zipfile.ZIP_DEFLATED
            zf.writestr(info, content(path))

    digest = sha256(zip_path)
    with open(os.path.join(out_dir, "SHA256SUMS"), "w", newline="\n") as f:
        f.write(f"{digest}  {name}\n")
    size = os.path.getsize(zip_path) / 1024 / 1024
    print(f"\n{zip_path} (version {version})\n{len(paths)} files, {size:.1f} MB\nSHA-256 {digest}")


if __name__ == "__main__":
    main()
