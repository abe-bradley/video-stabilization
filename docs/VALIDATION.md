# Release preparation checks

Prepared on 2026-11-08. This records packaging checks for the v0.1.0 freeze and v0.1.1 local-workflow update; historical stabilization measurements are in [CASE_STUDIES.md](CASE_STUDIES.md).

## Source preservation and release contents

- All three experiment scripts match their working-copy SHA-256 and byte counts in [code-snapshot.json](../provenance/code-snapshot.json).
- The original dependency-minimum file is retained.
- Historical source/output identifiers are recorded separately from the bundled proxy hashes.
- Historical analysis caches are excluded. Source originals, masters, local environments, and private user paths are excluded from the release export.
- The seven bundled proxies' format/stream tags were inspected; no private user path or GPS/location tag was found. No metadata-removal edit was necessary.

## Current environment

A fresh local virtual environment was created on Windows with Python 3.12.1, then the unmodified `requirements.txt` was installed. The resolved packages were:

| Package | Version |
| --- | --- |
| NumPy | 2.5.4 |
| opencv-python-headless | 5.0.0.93 |
| Pillow | 13.0.0 |
| SciPy | 1.18.1 |

FFmpeg/FFprobe were already installed separately (FFmpeg 8.0 full build). These are the **release-check environment**, not a claim about the exact August production dependencies and not a lockfile guaranteeing future compatibility.

The optional smoke command is:

```text
python tools/check_release.py --smoke --media-decode
```

It compiles Python files in memory, checks frozen hashes and JSON, verifies local documentation links and the media inventory, invokes all three experiment scripts with `--help`, and fully decodes the seven included videos. It never runs an original-media stabilization render.

For the initial v0.1.0 freeze, this command passed in the fresh environment: 41 release files, three frozen experiment scripts, five Python files in total, valid local documentation links, and seven fully decoded media files. The documentation-link check was repeated after the README revision.

The ZIP builder includes a checksum for every archived file, plus a checksum sidecar for the ZIP. The archive was extracted into a new temporary directory: all 41 internal checksums passed, and the extracted snapshot passed the same content/startup/media-decode checks using the fresh virtual environment. Rebuilding from that extracted copy produced an identical ZIP hash. No files from the original working folder were needed except the separately installed Python dependencies and FFmpeg.

## v0.1.1 local-workflow checks

The active repository was relocated, its virtual environment recreated at the new location, and its dependency installation, three script startup checks, and seven proxy decode checks repeated successfully. The three frozen-source hashes and seven media hashes remain unchanged. The v0.1.0 release ZIP's hash was checked before and after relocation.

The updated release check passed with 46 exported files, eight Python files, and 50 local documentation links. Six focused standard-library tests passed: settings paths resolve relative to the external file, dry-run creates nothing, copies match and repeated exports skip, conflicts preserve the existing file, and corrupt outer/internal checksums prevent export.

The private configuration is outside the repository. Public helpers accept only an explicit settings path; the generic example contains no actual machine destination. New release packages exclude environments, Git metadata, and private configuration. Local exports verify bytes and checksums; remote cloud synchronization is a separate operation handled by the drive application.

## Limits of the checks

The historical masters were visually reviewed and decoded during the original project. They were not rerendered as part of this documentation/package release. Startup and media checks do not establish behavior on new footage, cross-platform rendering support, or output-byte reproducibility. The bundled proxies have different dimensions/timing/color from the camera originals.

The GitHub Actions workflow performs the default release-content check and focused export-helper tests. It has not run on GitHub until the repository is published. It is not an algorithm-quality or arbitrary-footage test suite.
