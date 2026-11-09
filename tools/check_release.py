#!/usr/bin/env python3
"""Check the frozen source release without rendering any source footage.

Uses only the Python standard library. Optional --smoke needs the dependencies
in requirements.txt; --media-decode additionally needs FFmpeg on PATH.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys
from urllib.parse import unquote, urlsplit


REPO_ROOT = Path(__file__).resolve().parent.parent
EXPORT_DIRECTORIES = {
    ".github", "airplane", "sunrise", "docs", "examples", "presets",
    "provenance", "tools",
}
ROOT_TEXT_NAMES = {
    "LICENSE", "NOTICE", "COPYING", "AUTHORS", "VERSION", ".gitignore",
    ".gitattributes", ".editorconfig",
}
TEXT_SUFFIXES = {
    ".md", ".txt", ".rst", ".json", ".toml", ".yaml", ".yml",
    ".ini", ".cfg", ".py", ".csv",
}
ROOT_TEXT_SUFFIXES = TEXT_SUFFIXES - {".py", ".csv"}
IGNORED_PARTS = {
    ".git", ".venv", "venv", ".deps", "__pycache__", ".pytest_cache",
    "node_modules", "dist", "build", "outputs", "runs", "raw", "originals",
}
IGNORED_NAMES = {".DS_Store", "Thumbs.db", "SHA256SUMS.txt"}
IGNORED_SUFFIXES = {".pyc", ".pyo", ".zip", ".sha256"}
MEDIA_SUFFIXES = {".mp4", ".mov", ".m4v", ".mkv", ".avi", ".webm"}
FROZEN_SCRIPTS = {
    "sunrise/stabilize_sunrise.py", "sunrise/compare_feedback.py",
    "airplane/stabilize_airplane.py",
}
PRIVATE_PATH = re.compile(
    r"\b[A-Za-z]:[\\/]+(?:Users[\\/]+|Cloud[ _]Storage(?:[\\/]+|\b))",
    re.IGNORECASE,
)
INLINE_LINK = re.compile(r"!?\[[^\]\n]*\]\((<[^>\n]+>|(?:\\.|[^)\n])+)\)")
REFERENCE_LINK = re.compile(r"^ {0,3}\[[^\]]+\]:\s*(<[^>]+>|\S+)", re.MULTILINE)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _ignored(relative: Path) -> bool:
    if relative.name in IGNORED_NAMES or relative.suffix.lower() in IGNORED_SUFFIXES:
        return True
    if relative.name == ".env" or relative.name.startswith(".env."):
        return True
    return any(
        part.lower() in IGNORED_PARTS
        or part.lower().startswith(("diagnostics", "analysis_"))
        for part in relative.parts
    )


def iter_export_files(root: Path = REPO_ROOT):
    """Yield the exact sorted source-release file set used by the ZIP builder."""
    root = root.resolve()
    for path in sorted(root.rglob("*"), key=lambda item: item.relative_to(root).as_posix()):
        relative = path.relative_to(root)
        if _ignored(relative) or not path.is_file():
            continue
        if len(relative.parts) == 1:
            if path.name not in ROOT_TEXT_NAMES and path.suffix.lower() not in ROOT_TEXT_SUFFIXES:
                continue
        elif relative.parts[0] not in EXPORT_DIRECTORIES:
            continue
        if path.is_symlink():
            raise ValueError(f"Symlinks are not part of this release: {relative.as_posix()}")
        yield path


def _manifest_entries(data: object, label: str) -> list[dict]:
    if not isinstance(data, dict) or not isinstance(data.get("files"), list):
        raise ValueError(f"{label}: expected an object with a files array")
    entries = data["files"]
    if not all(isinstance(entry, dict) for entry in entries):
        raise ValueError(f"{label}: every files entry must be an object")
    return entries


def _check_manifest(root: Path, label: str, data: object, exported: set[str], errors: list[str]) -> set[str]:
    listed: set[str] = set()
    try:
        entries = _manifest_entries(data, label)
    except ValueError as error:
        errors.append(str(error))
        return listed
    for index, entry in enumerate(entries):
        value = entry.get("path")
        if not isinstance(value, str) or not value or "\\" in value:
            errors.append(f"{label} entry {index}: path must be a repository-relative POSIX path")
            continue
        relative = Path(value)
        path = root / relative
        if relative.is_absolute() or ".." in relative.parts or ":" in value:
            errors.append(f"{label}: unsafe path {value!r}")
            continue
        if value in listed:
            errors.append(f"{label}: duplicate entry {value}")
        listed.add(value)
        if value not in exported:
            errors.append(f"{label}: file is missing or excluded from release: {value}")
            continue
        size = entry.get("bytes")
        expected_hash = entry.get("sha256")
        if type(size) is not int or size < 0:
            errors.append(f"{label}: invalid byte count for {value}")
        elif path.stat().st_size != size:
            errors.append(f"{label}: byte count mismatch for {value}")
        if not isinstance(expected_hash, str) or not re.fullmatch(r"[0-9a-fA-F]{64}", expected_hash):
            errors.append(f"{label}: invalid SHA-256 for {value}")
        elif sha256(path).lower() != expected_hash.lower():
            errors.append(f"{label}: SHA-256 mismatch for {value}")
    return listed


def markdown_without_code(text: str) -> str:
    """Remove fenced and inline code before looking for ordinary Markdown links."""
    lines: list[str] = []
    fence_char = ""
    fence_size = 0
    for line in text.splitlines():
        match = re.match(r"^ {0,3}(`{3,}|~{3,})", line)
        if match:
            marker = match.group(1)
            if not fence_char:
                fence_char, fence_size = marker[0], len(marker)
            elif marker[0] == fence_char and len(marker) >= fence_size:
                fence_char, fence_size = "", 0
            lines.append("")
        elif fence_char:
            lines.append("")
        else:
            lines.append(re.sub(r"(`+).*?\1", "", line))
    return "\n".join(lines)


def _local_link_target(value: str) -> str:
    value = value.strip()
    if value.startswith("<"):
        return value[1:value.index(">")]
    # The optional title follows whitespace; spaces in paths use <...> or %20.
    return re.split(r"\s+[\"']", value, maxsplit=1)[0].strip()


def _check_links(path: Path, text: str, root: Path, exported: set[str], errors: list[str]) -> int:
    cleaned = markdown_without_code(text)
    targets = [match.group(1) for pattern in (INLINE_LINK, REFERENCE_LINK) for match in pattern.finditer(cleaned)]
    checked = 0
    for raw in targets:
        value = _local_link_target(raw)
        if not value or value.startswith(("#", "//")):
            continue
        parsed = urlsplit(value)
        if parsed.scheme:
            continue
        decoded = unquote(parsed.path)
        if not decoded:
            continue
        target = (path.parent / decoded).resolve()
        try:
            relative = target.relative_to(root).as_posix()
        except ValueError:
            errors.append(f"{path.relative_to(root).as_posix()}: link escapes repository: {value}")
            continue
        checked += 1
        directory_present = target.is_dir() and any(
            name.startswith(relative.rstrip("/") + "/") for name in exported
        )
        if target == root:
            directory_present = True
        if relative not in exported and not directory_present:
            errors.append(f"{path.relative_to(root).as_posix()}: missing release link target: {value}")
    return checked


def check_release(root: Path = REPO_ROOT, *, smoke: bool = False, media_decode: bool = False) -> dict[str, int]:
    root = root.resolve()
    files = list(iter_export_files(root))
    exported = {path.relative_to(root).as_posix() for path in files}
    errors: list[str] = []
    json_files: dict[str, object] = {}
    links = 0
    python_files = 0
    for path in files:
        relative = path.relative_to(root).as_posix()
        if path.suffix.lower() not in TEXT_SUFFIXES and path.name not in ROOT_TEXT_NAMES:
            continue
        try:
            text = path.read_text(encoding="utf-8-sig")
        except UnicodeError as error:
            errors.append(f"{relative}: not UTF-8 text: {error}")
            continue
        match = PRIVATE_PATH.search(text)
        if match:
            line = text.count("\n", 0, match.start()) + 1
            errors.append(f"{relative}:{line}: private absolute user/storage path")
        if path.suffix.lower() == ".py":
            python_files += 1
            try:
                compile(text, relative, "exec")
            except SyntaxError as error:
                errors.append(f"{relative}: syntax error: {error}")
        elif path.suffix.lower() == ".json":
            try:
                json_files[relative] = json.loads(text)
                data = json_files[relative]
                if (
                    isinstance(data, dict)
                    and {"release_directory", "export_directory"}.issubset(data)
                    and relative != "docs/local-settings.example.json"
                ):
                    errors.append(f"{relative}: keep private local settings outside the repository")
            except json.JSONDecodeError as error:
                errors.append(f"{relative}: invalid JSON: {error}")
        elif path.suffix.lower() == ".md":
            links += _check_links(path, text, root, exported, errors)

    code_manifest = "provenance/code-snapshot.json"
    media_manifest = "examples/media-manifest.json"
    for required in (code_manifest, media_manifest):
        if required not in json_files:
            errors.append(f"Missing readable manifest: {required}")
    code_paths = _check_manifest(root, code_manifest, json_files.get(code_manifest), exported, errors)
    if code_paths != FROZEN_SCRIPTS:
        errors.append("Code snapshot must contain exactly the three frozen experiment scripts")
    media_paths = _check_manifest(root, media_manifest, json_files.get(media_manifest), exported, errors)
    actual_media = {name for name in exported if Path(name).suffix.lower() in MEDIA_SUFFIXES}
    if actual_media != media_paths:
        errors.append(f"Media inventory mismatch; unlisted={sorted(actual_media - media_paths)}, missing={sorted(media_paths - actual_media)}")
    for name in actual_media:
        if not name.startswith("examples/media/") or Path(name).suffix.lower() != ".mp4":
            errors.append(f"Unexpected raw/master media in release: {name}")

    if not errors and smoke:
        for relative in sorted(FROZEN_SCRIPTS):
            result = subprocess.run(
                [sys.executable, str(root / relative), "--help"], cwd=root,
                capture_output=True, text=True, timeout=60,
            )
            if result.returncode:
                errors.append(f"Import/help smoke check failed: {relative}\n{result.stderr.strip()}")
    if not errors and media_decode:
        ffmpeg = shutil.which("ffmpeg")
        if ffmpeg is None:
            errors.append("--media-decode requires FFmpeg on PATH")
        else:
            for relative in sorted(media_paths):
                result = subprocess.run(
                    [ffmpeg, "-hide_banner", "-loglevel", "error", "-xerror",
                     "-i", str(root / relative), "-map", "0:v:0", "-map", "0:a?",
                     "-f", "null", "-"],
                    cwd=root, capture_output=True, text=True, timeout=180,
                )
                if result.returncode:
                    errors.append(f"Media decode failed: {relative}\n{result.stderr.strip()}")
    if errors:
        raise ValueError("Release check failed:\n" + "\n".join(f"- {error}" for error in errors))
    return {"files": len(files), "frozen_scripts": len(code_paths), "python_files": python_files,
            "local_links": links, "media_files": len(media_paths)}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--smoke", action="store_true", help="Import each frozen script through its --help command")
    parser.add_argument("--media-decode", action="store_true", help="Fully decode the bundled proxy videos using FFmpeg")
    args = parser.parse_args()
    try:
        counts = check_release(smoke=args.smoke, media_decode=args.media_decode)
    except (ValueError, OSError, subprocess.SubprocessError) as error:
        print(error, file=sys.stderr)
        return 1
    print("Release check passed: " + ", ".join(f"{value} {name.replace('_', ' ')}" for name, value in counts.items()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
