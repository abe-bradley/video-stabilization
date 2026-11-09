#!/usr/bin/env python3
"""Validate and build a deterministic source ZIP, including the compact examples."""

from __future__ import annotations

import argparse
import hashlib
from pathlib import Path
import sys
import zipfile

from check_release import REPO_ROOT, check_release, iter_export_files
from local_settings import load_settings


VERSION = "0.1.1"
ARCHIVE_PREFIX = f"kerkyra-stabilizer-v{VERSION}"
ZIP_TIMESTAMP = (2026, 11, 8, 0, 0, 0)


def _zip_info(name: str) -> zipfile.ZipInfo:
    info = zipfile.ZipInfo(name, date_time=ZIP_TIMESTAMP)
    info.compress_type = zipfile.ZIP_DEFLATED
    info.create_system = 3
    info.external_attr = 0o100644 << 16
    return info


def build_release(output: Path, *, force: bool = False) -> tuple[Path, str]:
    check_release()
    output = output.resolve()
    if output.suffix.lower() != ".zip":
        raise ValueError("The release output must have a .zip extension")
    sidecar = output.with_name(output.name + ".sha256")
    if output == sidecar:
        raise ValueError("ZIP and checksum paths must differ")
    if not force and (output.exists() or sidecar.exists()):
        raise ValueError("Output ZIP or checksum already exists; use --force to replace this generated release")
    if output.is_dir() or sidecar.is_dir():
        raise ValueError("Output ZIP and checksum targets must be files")
    files = list(iter_export_files())
    if output in files or sidecar in files:
        raise ValueError("Output must not replace a release source file")
    output.parent.mkdir(parents=True, exist_ok=True)
    checksums: list[str] = []
    archive_contents: dict[str, bytes] = {}
    for path in files:
        relative = path.relative_to(REPO_ROOT).as_posix()
        data = path.read_bytes()
        archive_contents[relative] = data
        checksums.append(f"{hashlib.sha256(data).hexdigest()}  {relative}\n")
    # This inventory covers every other archive file, including the media.
    archive_contents["SHA256SUMS.txt"] = "".join(checksums).encode("utf-8")
    with zipfile.ZipFile(output, mode="w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for relative, data in sorted(archive_contents.items()):
            archive.writestr(_zip_info(f"{ARCHIVE_PREFIX}/{relative}"), data, compresslevel=9)
    digest = hashlib.sha256(output.read_bytes()).hexdigest()
    sidecar.write_text(f"{digest}  {output.name}\n", encoding="utf-8", newline="\n")
    return output, digest


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, help="Explicit ZIP path; overrides the settings release directory")
    parser.add_argument("--settings", type=Path, help="Private local settings JSON outside the repository")
    parser.add_argument("--force", action="store_true", help="Replace an existing generated ZIP and checksum")
    args = parser.parse_args()
    try:
        if args.settings:
            release_directory = load_settings(args.settings).release_directory
        else:
            release_directory = REPO_ROOT / "dist"
        target = args.output or release_directory / f"{ARCHIVE_PREFIX}.zip"
        output, digest = build_release(target, force=args.force)
    except (OSError, ValueError, zipfile.BadZipFile) as error:
        print(error, file=sys.stderr)
        return 1
    print(f"Created {output.name} ({output.stat().st_size:,} bytes)")
    print(f"SHA-256 {digest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
