"""
Automated Build & Release Script for dotix
Handles:
  1. Archiving previous dist/ contents cleanly into dist_archive/
  2. Cleaning temporary build/ cache
  3. Compiling binary via PyInstaller
  4. Building installer via Inno Setup (ISCC)
  5. Syncing installer to website/ for immediate download serving
  6. Generating release summary & SHA-256 checksums
"""
from __future__ import annotations

import datetime
import hashlib
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DIST_DIR = ROOT / "dist"
ARCHIVE_DIR = ROOT / "dist_archive"
BUILD_DIR = ROOT / "build"
WEBSITE_DIR = ROOT / "website"
SPEC_FILE = ROOT / "MasterPrompt.spec"
ISS_FILE = ROOT / "installer.iss"


def get_version() -> str:
    import master_prompt
    return getattr(master_prompt, "__version__", "2.0.0")


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(65536):
            h.update(chunk)
    return h.hexdigest()


def find_iscc() -> Path | None:
    candidates = [
        Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Inno Setup 6" / "ISCC.exe",
        Path("C:/Program Files (x86)/Inno Setup 6/ISCC.exe"),
        Path("C:/Program Files/Inno Setup 6/ISCC.exe"),
        Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Inno Setup 7" / "ISCC.exe",
        Path("C:/Program Files (x86)/Inno Setup 7/ISCC.exe"),
        Path("C:/Program Files/Inno Setup 7/ISCC.exe"),
    ]
    # Also check PATH
    which_iscc = shutil.which("iscc")
    if which_iscc:
        candidates.insert(0, Path(which_iscc))

    for p in candidates:
        if p.is_file():
            return p
    return None


def archive_existing_dist() -> None:
    """If dist/ contains previous builds, move them cleanly into dist_archive/<timestamp>."""
    if not DIST_DIR.exists():
        return

    items = list(DIST_DIR.iterdir())
    if not items:
        return

    ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    target_archive = ARCHIVE_DIR / f"build_{timestamp}"

    print(f"\n[1/5] Archiving previous build to dist_archive/{target_archive.name}...")
    target_archive.mkdir(parents=True, exist_ok=True)
    for item in items:
        shutil.move(str(item), str(target_archive / item.name))
    print(f"      Archived {len(items)} items cleanly.")


def clean_build_cache() -> None:
    """Remove temporary build/ directory."""
    if BUILD_DIR.exists():
        print("[2/5] Cleaning temporary build/ cache...")
        shutil.rmtree(BUILD_DIR, ignore_errors=True)


def run_pyinstaller() -> None:
    """Run PyInstaller using virtualenv Python."""
    print("\n[3/5] Running PyInstaller build...")
    pyinstaller = ROOT / ".venv" / "Scripts" / "pyinstaller.exe"
    if not pyinstaller.is_file():
        pyinstaller = Path(sys.executable).parent / "pyinstaller.exe"
    if not pyinstaller.is_file():
        pyinstaller = Path("pyinstaller")

    cmd = [
        str(pyinstaller),
        "--noconfirm",
        "--clean",
        "--log-level", "WARN",
        str(SPEC_FILE),
    ]
    res = subprocess.run(cmd, cwd=str(ROOT))
    if res.returncode != 0:
        raise RuntimeError(f"PyInstaller failed with code {res.returncode}")
    print("      PyInstaller compiled successfully.")


def run_inno_setup(version: str) -> Path:
    """Compile Inno Setup installer."""
    print("\n[4/5] Building installer with Inno Setup...")
    iscc = find_iscc()
    if not iscc:
        raise FileNotFoundError(
            "Inno Setup 6 compiler (ISCC.exe) not found.\n"
            "Please install Inno Setup 6 from https://jrsoftware.org/isdl.php"
        )

    cmd = [
        str(iscc),
        "/Q",
        f"/DAppVersion={version}",
        str(ISS_FILE),
    ]
    res = subprocess.run(cmd, cwd=str(ROOT))
    if res.returncode != 0:
        raise RuntimeError(f"Inno Setup failed with exit code {res.returncode}")

    installer_name = f"dotix-Setup-{version}.exe"
    installer_path = DIST_DIR / installer_name
    if not installer_path.exists():
        fallback = DIST_DIR / f"MasterPrompt-Setup-{version}.exe"
        if fallback.exists():
            shutil.move(str(fallback), str(installer_path))

    print(f"      Installer created: {installer_path.name}")
    return installer_path


def sync_to_website(version: str, installer_path: Path) -> None:
    """Keep website/ clean with only the latest installer and archive old .exe files."""
    print("\n[5/5] Managing website/ installers and sync...")
    if not WEBSITE_DIR.is_dir():
        print("      website/ directory not found, skipping sync.")
        return

    # Archive any old or redundant .exe files in website/ to dist_archive/website_archived_exes
    target_name = f"dotix-Setup-{version}.exe"
    website_archive = ARCHIVE_DIR / "website_archived_exes"
    existing_exes = [f for f in WEBSITE_DIR.glob("*.exe") if f.name != target_name]
    if existing_exes:
        website_archive.mkdir(parents=True, exist_ok=True)
        for old_exe in existing_exes:
            dest = website_archive / old_exe.name
            print(f"      Archiving old website exe: {old_exe.name} -> dist_archive/website_archived_exes/{old_exe.name}")
            shutil.move(str(old_exe), str(dest))

    # Copy current installer to website/
    dest_dotix = WEBSITE_DIR / target_name
    shutil.copy2(installer_path, dest_dotix)
    print(f"      Copied {installer_path.name} -> website/{dest_dotix.name}")

    # Ensure website/api/download.js redirects to the current installer version
    download_js = WEBSITE_DIR / "api" / "download.js"
    if download_js.is_file():
        content = download_js.read_text(encoding="utf-8")
        import re
        new_content = re.sub(
            r"const FILE\s*=\s*['\"].*?['\"];",
            f"const FILE = '/{target_name}';",
            content,
        )
        if new_content != content:
            download_js.write_text(new_content, encoding="utf-8")
            print(f"      Updated website/api/download.js to point to /{target_name}")


def main() -> int:
    version = get_version()
    print("=" * 64)
    print(f"  dotix Shipping & Build Pipeline - Version {version}")
    print("=" * 64)

    try:
        archive_existing_dist()
        clean_build_cache()
        run_pyinstaller()
        installer = run_inno_setup(version)
        sync_to_website(version, installer)

        # Print final release summary
        size_mb = installer.stat().st_size / (1024 * 1024)
        checksum = sha256_file(installer)

        print("\n" + "=" * 64)
        print("  BUILD & PACKAGING COMPLETE!")
        print("=" * 64)
        print(f"  Installer:  {installer.resolve()}")
        print(f"  File size:  {size_mb:.2f} MB")
        print(f"  SHA-256:    {checksum}")
        print("\n  Files ready to upload to GitHub Releases:")
        print(f"    - {installer.resolve()}")
        print("=" * 64)
        return 0
    except Exception as e:
        print(f"\n[ERROR] Build failed: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
