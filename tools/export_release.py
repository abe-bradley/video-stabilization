#!/usr/bin/env python3
"""Copy checked release ZIPs and checksums to a privately configured folder.

The destination may be a locally mounted cloud-drive folder. Upload/sync status
is managed by that drive application, not by this helper.
"""

from __future__ import annotations

import argparse
import hashlib
from pathlib import Path
import re
import shutil
import sys
import zipfile

from build_release import ARCHIVE_PREFIX
from check_release import sha256
from local_settings import load_settings


def validate_archive(archive: Path) -> Path:
    if not re.fullmatch(r"kerkyra-stabilizer-v\d+\.\d+\.\d+\.zip", archive.name):
        raise ValueError("Expected a versioned kerkyra-stabilizer release ZIP")
    sidecar = archive.with_name(archive.name + ".sha256")
    parts = sidecar.read_text(encoding="utf-8").strip().split()
    if len(parts) != 2 or parts[1] != archive.name or parts[0].lower() != sha256(archive):
        raise ValueError(f"ZIP checksum mismatch: {archive.name}")
    prefix = archive.stem + "/"
    with zipfile.ZipFile(archive) as bundle:
        inventory_name = prefix + "SHA256SUMS.txt"
        entries = bundle.read(inventory_name).decode("utf-8").splitlines()
        expected = {inventory_name}
        for entry in entries:
            digest, relative = entry.split("  ", 1)
            if relative.startswith(("/", "\\")) or ".." in Path(relative).parts:
                raise ValueError("Unsafe archive inventory path")
            name = prefix + relative
            if name in expected or hashlib.sha256(bundle.read(name)).hexdigest() != digest:
                raise ValueError("Internal release checksum mismatch")
            expected.add(name)
        if set(bundle.namelist()) != expected or len(bundle.namelist()) != len(expected):
            raise ValueError("Release contains unlisted or duplicate archive entries")
    return sidecar


def export_archive(archive: Path, destination: Path, *, dry_run: bool = False) -> list[str]:
    archive = archive.resolve()
    destination = destination.resolve()
    sidecar = validate_archive(archive)
    pairs = [(source, destination / source.name) for source in (archive, sidecar)]
    pending: list[tuple[Path, Path]] = []
    for source, target in pairs:
        if target.exists():
            if not target.is_file() or sha256(source) != sha256(target):
                raise ValueError(f"Export refused: different existing file {target.name}")
        else:
            pending.append((source, target))
    if dry_run:
        return [f"Would copy {target.name}" for _, target in pending] or [f"Already matches {archive.name}"]
    destination.mkdir(parents=True, exist_ok=True)
    created: list[Path] = []
    try:
        for source, target in pending:
            with target.open("xb") as output:
                created.append(target)
                with source.open("rb") as input_file:
                    shutil.copyfileobj(input_file, output)
            if sha256(source) != sha256(target):
                raise ValueError(f"Export verification failed: {target.name}")
    except Exception:
        for target in reversed(created):
            target.unlink(missing_ok=True)
        raise
    return [f"Copied and verified {target.name}" for _, target in pending] or [f"Already matches {archive.name}"]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--settings", type=Path, required=True, help="Private JSON file outside the repository")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--archive", type=Path, help="Specific release ZIP; defaults to the current version")
    group.add_argument("--all-releases", action="store_true", help="Export every versioned ZIP in the local release directory")
    parser.add_argument("--dry-run", action="store_true", help="Validate and describe copies without creating files")
    args = parser.parse_args()
    try:
        settings = load_settings(args.settings)
        if args.all_releases:
            archives = sorted(settings.release_directory.glob("kerkyra-stabilizer-v*.zip"))
            if not archives:
                raise ValueError("No release ZIPs found in the configured release directory")
        else:
            archives = [args.archive or settings.release_directory / f"{ARCHIVE_PREFIX}.zip"]
        for archive in archives:
            for message in export_archive(archive, settings.export_directory, dry_run=args.dry_run):
                print(message)
        if not args.dry_run:
            print("Local export verified. Cloud sync, if applicable, is handled by your drive application.")
    except (OSError, ValueError, KeyError, zipfile.BadZipFile) as error:
        print(error, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
