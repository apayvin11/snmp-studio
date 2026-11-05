#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Build a native desktop bundle and an installer for the current architecture."""
from __future__ import annotations

import argparse
import hashlib
import os
from pathlib import Path
import platform
import plistlib
import shutil
import subprocess
import sys
import time

from generate_icons import generate
from collect_licenses import collect

ROOT = Path(__file__).resolve().parents[1]
VERSION = "0.1.0"
BUNDLE_ID = "io.snmpstudio.desktop"
DISPLAY_NAME = "SNMP Studio"


def run(*args, **kwargs):
    print("+", " ".join(map(str, args)), flush=True)
    return subprocess.run(list(map(str, args)), check=True, cwd=ROOT, **kwargs)


def architecture():
    machine = platform.machine().lower()
    if machine in ("arm64", "aarch64"):
        return "arm64"
    if machine in ("x86_64", "amd64"):
        return "x86_64"
    raise SystemExit(f"Unsupported architecture: {machine}")


def bundle(arch, icons):
    licenses=collect(ROOT/"build"/"third-party-licenses")
    args = [sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "--onedir",
            "--specpath", ROOT/"build"/"spec",
            "--windowed", "--name", "SNMPStudio", "--paths", ROOT,
            "--collect-all", "pysnmp", "--collect-all", "pysmi", "--collect-all", "keyring", "--collect-data", "snmpstudio",
            "--add-data", f"{ROOT/'assets'/'icons'/'snmpstudio-512.png'}:assets",
            "--add-data", f"{ROOT/'THIRD_PARTY_NOTICES.md'}:licenses",
            "--add-data", f"{ROOT/'LICENSE'}:licenses",
            "--add-data", f"{ROOT/'NOTICE'}:licenses",
            "--add-data", f"{licenses}:licenses/third-party",
            "--exclude-module", "PySide6.QtWebEngineCore", "--exclude-module", "PySide6.QtWebEngineWidgets",
            "--exclude-module", "PySide6.QtQml", "--exclude-module", "PySide6.QtQuick"]
    if sys.platform == "darwin":
        args += ["--target-architecture", arch, "--icon", icons/"SNMPStudio.icns", "--osx-bundle-identifier", BUNDLE_ID]
        identity = os.environ.get("MACOS_CODESIGN_IDENTITY")
        if identity:
            args += ["--codesign-identity", identity]
    args += [ROOT/"scripts"/"launcher.py"]
    run(*args)
    if sys.platform == "darwin":
        generated = ROOT/"dist"/"SNMPStudio.app"
        app = ROOT/"dist"/f"{DISPLAY_NAME}.app"
        if app.exists():
            shutil.rmtree(app)
        generated.rename(app)
        info = app/"Contents"/"Info.plist"
        with info.open("rb") as stream:
            data = plistlib.load(stream)
        data.update(CFBundleShortVersionString=VERSION, CFBundleVersion="0.1.0",
                    NSHighResolutionCapable=True, LSMinimumSystemVersion="15.0",
                    CFBundleName=DISPLAY_NAME, CFBundleDisplayName=DISPLAY_NAME, NSHumanReadableCopyright=f"{DISPLAY_NAME} contributors")
        with info.open("wb") as stream:
            plistlib.dump(data, stream)
        # Editing Info.plist invalidates the bundle seal; sign it again.
        identity = os.environ.get("MACOS_CODESIGN_IDENTITY", "-")
        signing = ["codesign", "--force", "--deep", "--sign", identity]
        if identity != "-":
            signing += ["--options", "runtime", "--timestamp"]
        run(*signing, app)
        run("codesign", "--verify", "--deep", "--strict", app)
        return app
    return ROOT/"dist"/"SNMPStudio"


def dmg(app, arch, output):
    stage = ROOT/"build"/"dmg-stage"
    if stage.exists():
        shutil.rmtree(stage)
    stage.mkdir(parents=True)
    # ditto preserves the .app's framework symlinks and metadata.
    run("ditto", app, stage/f"{DISPLAY_NAME}.app")
    (stage/"Applications").symlink_to("/Applications", target_is_directory=True)
    target = output/f"SNMPStudio-{VERSION}-macos-{arch}.dmg"
    if target.exists():
        target.unlink()
    run("hdiutil", "create", "-volname", DISPLAY_NAME, "-srcfolder", stage,
        "-ov", "-format", "UDZO", target)
    profile = os.environ.get("MACOS_NOTARY_PROFILE")
    if profile:
        run("xcrun", "notarytool", "submit", target, "--keychain-profile", profile, "--wait")
        run("xcrun", "stapler", "staple", target)
        run("xcrun", "stapler", "validate", target)
    for attempt in range(3):
        try:
            run("hdiutil", "verify", target)
            break
        except subprocess.CalledProcessError:
            if attempt == 2:
                raise
            time.sleep(attempt + 1)
    return target


def deb(app, arch, output, icons):
    deb_arch = "amd64" if arch == "x86_64" else "arm64"
    stage = ROOT/"build"/f"deb-{deb_arch}"
    if stage.exists():
        shutil.rmtree(stage)
    (stage/"DEBIAN").mkdir(parents=True)
    lib = stage/"usr"/"lib"/"snmpstudio"
    shutil.copytree(app, lib, symlinks=True)
    executable = stage/"usr"/"bin"
    executable.mkdir(parents=True)
    (executable/"snmpstudio").symlink_to("../lib/snmpstudio/SNMPStudio")
    applications = stage/"usr"/"share"/"applications"
    applications.mkdir(parents=True)
    shutil.copy2(ROOT/"packaging"/"snmpstudio.desktop", applications/"snmpstudio.desktop")
    for size in (16, 32, 48, 64, 128, 256, 512):
        directory = stage/"usr"/"share"/"icons"/"hicolor"/f"{size}x{size}"/"apps"
        directory.mkdir(parents=True)
        shutil.copy2(icons/f"snmpstudio-{size}.png", directory/"snmpstudio.png")
    scalable = stage/"usr"/"share"/"icons"/"hicolor"/"scalable"/"apps"
    scalable.mkdir(parents=True)
    shutil.copy2(ROOT/"assets"/"snmpstudio.svg", scalable/"snmpstudio.svg")
    docs = stage/"usr"/"share"/"doc"/"snmpstudio"
    docs.mkdir(parents=True)
    for filename in ("README.md", "README.ru.md", "THIRD_PARTY_NOTICES.md", "LICENSE", "NOTICE"):
        shutil.copy2(ROOT/filename, docs/filename)
    shutil.copytree(ROOT/"docs"/"screenshots", docs/"docs"/"screenshots", dirs_exist_ok=True)
    installed_size = sum(p.stat().st_size for p in stage.rglob("*") if p.is_file() and not p.is_symlink())//1024
    dependencies = ("libc6 (>= 2.39), libstdc++6 (>= 13), libgl1, libegl1, libopengl0, "
        "libxkbcommon0, libxkbcommon-x11-0, libxcb1, libxcb-cursor0, libxcb-icccm4, "
        "libxcb-image0, libxcb-keysyms1, libxcb-randr0, libxcb-render0, libxcb-render-util0, "
        "libxcb-shape0, libxcb-shm0, libxcb-sync1, libxcb-xfixes0, libxcb-xinerama0, "
        "libdbus-1-3, libfontconfig1, libfreetype6, libx11-6, libx11-xcb1, libxi6, libxrender1, "
        "libglib2.0-0t64 | libglib2.0-0")
    (stage/"DEBIAN"/"control").write_text(f"""Package: snmpstudio
Version: {VERSION}
Section: utils
Priority: optional
Architecture: {deb_arch}
Maintainer: Alex Payvin <106412250+apayvin11@users.noreply.github.com>
Installed-Size: {installed_size}
Depends: {dependencies}
Description: Desktop SNMP monitoring and MIB editing workstation
 Configure devices and parameters, monitor current values,
 edit MIB files, send SET commands, and inspect decoded/HEX messages.
 Russian and English interface. Python runtime is included.
""", encoding="utf-8")
    for script in ("postinst", "postrm"):
        path = stage/"DEBIAN"/script
        path.write_text("#!/bin/sh\nset -e\nif command -v update-desktop-database >/dev/null 2>&1; then update-desktop-database -q /usr/share/applications || true; fi\nif command -v gtk-update-icon-cache >/dev/null 2>&1; then gtk-update-icon-cache -q -t /usr/share/icons/hicolor || true; fi\nexit 0\n")
        path.chmod(0o755)
    for path in stage.rglob("*"):
        if path.is_symlink():
            continue
        if path.is_dir():
            path.chmod(0o755)
        elif path.name == "SNMPStudio" or path.parent.name == "DEBIAN" and path.name in ("postinst", "postrm"):
            path.chmod(0o755)
        elif not path.stat().st_mode & 0o111:
            path.chmod(0o644)
    target = output/f"snmpstudio_{VERSION}_{deb_arch}.deb"
    run("dpkg-deb", "--root-owner-group", "--build", stage, target)
    run("dpkg-deb", "--info", target)
    return target


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--format", choices=("bundle", "dmg", "deb"), required=True)
    parser.add_argument("--arch", choices=("arm64", "x86_64"), help="Expected native architecture; prevents mislabeled builds")
    parser.add_argument("--skip-bundle", action="store_true", help="Package an existing desktop bundle")
    args = parser.parse_args()
    arch = architecture()
    if args.arch and args.arch != arch:
        raise SystemExit(f"Use a native {args.arch} runner. Current Python architecture: {arch}")
    if args.format == "dmg" and sys.platform != "darwin" or args.format == "deb" and sys.platform != "linux":
        raise SystemExit("DMG requires macOS; DEB requires Linux")
    os.chdir(ROOT)
    icons = generate()
    app = (ROOT/"dist"/(f"{DISPLAY_NAME}.app" if sys.platform == "darwin" else "SNMPStudio")) if args.skip_bundle else bundle(arch, icons)
    if not app.exists():
        raise SystemExit(f"Bundle does not exist: {app}")
    if args.format == "bundle":
        print(f"Built: {app}")
        return
    output = ROOT/"release"
    output.mkdir(exist_ok=True)
    artifact = dmg(app, arch, output) if args.format == "dmg" else deb(app, arch, output, icons)
    digest = hashlib.sha256(artifact.read_bytes()).hexdigest()
    artifact.with_suffix(artifact.suffix+".sha256").write_text(f"{digest}  {artifact.name}\n")
    print(f"Release: {artifact}\nSHA256: {digest}")


if __name__ == "__main__":
    main()
