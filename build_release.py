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
        # Fallback if named MasterPrompt
        fallback = DIST_DIR / f"MasterPrompt-Setup-{version}.exe"
        if fallback.exists():
            shutil.copy2(fallback, installer_path)

    # Also keep MasterPrompt-Setup for backward compatibility
    legacy_path = DIST_DIR / f"MasterPrompt-Setup-{version}.exe"
    if not legacy_path.exists() and installer_path.exists():
        shutil.copy2(installer_path, legacy_path)

    print(f"      Installer created: {installer_path.name}")
    return installer_path


def sync_to_website(version: str, installer_path: Path) -> None:
    """Copy installer to website/ so Vercel can host it for download buttons."""
    print("\n[5/5] Syncing installer to website/ folder...")
    if not WEBSITE_DIR.is_dir():
        print("      website/ directory not found, skipping sync.")
        return

    # Destination names
    dest_dotix = WEBSITE_DIR / f"dotix-Setup-{version}.exe"
    dest_legacy = WEBSITE_DIR / f"MasterPrompt-Setup-{version}.exe"

    shutil.copy2(installer_path, dest_dotix)
    shutil.copy2(installer_path, dest_legacy)
    print(f"      Copied {installer_path.name} -> website/{dest_dotix.name}")
    print(f"      Copied {installer_path.name} -> website/{dest_legacy.name}")


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
        print(f"    - {(DIST_DIR / f'MasterPrompt-Setup-{version}.exe').resolve()}")
        print("=" * 64)
        return 0
    except Exception as e:
        print(f"\n[ERROR] Build failed: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
