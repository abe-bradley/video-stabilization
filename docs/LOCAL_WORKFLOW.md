# Local development, release builds, and drive exports

The original working archive is retained separately. This repository is the active development copy, starting from the v0.1.0 freeze. Keep that ZIP as the baseline; later packaging changes use their own version numbers. The three stabilization scripts remain the same frozen source files.

A convenient local layout is:

```text
Media tools/
  Video stabilizer/               # this Git repository
  Releases/
    Video stabilizer/             # versioned ZIPs and checksum sidecars
  Local settings/
    video-stabilizer.json         # actual destinations; outside Git and ZIPs
```

The directory name on disk can differ from the repository's public name. Virtual environments should be recreated after a move because they can contain absolute paths. The package's relative documentation links and frozen script hashes do not depend on the local folder name.

## Configure locally

Copy [local-settings.example.json](local-settings.example.json) to a location **outside** the repository, such as the `Local settings` folder above. Edit the copy with your actual `release_directory` and `export_directory`.

- Relative paths resolve from the settings file's directory, not the shell's current directory.
- `export_directory` can be a folder mounted by your cloud-drive application.
- Neither destination nor the settings file may be inside this repository.
- Only the generic example belongs in Git. The real destination is never written into a public source file or release manifest.

The helpers require the private file explicitly; no machine-specific destination is embedded or auto-discovered.

## Build and export

Run these from the repository root, using the layout above:

```text
python tools/build_release.py --settings "../Local settings/video-stabilizer.json"
python tools/export_release.py --settings "../Local settings/video-stabilizer.json" --dry-run
python tools/export_release.py --settings "../Local settings/video-stabilizer.json"
```

The builder places the current version in the private settings' release directory. Without `--settings`, its portable default is `dist/` inside the repository; `--output` selects a specific ZIP path. An existing ZIP requires an explicit `--force` to rebuild. Use a new version when retaining a freeze.

The exporter checks the ZIP's sidecar and every internal file checksum before copying the ZIP and sidecar. It verifies destination bytes afterward, skips identical exports, and refuses a different existing file under the same name. `--dry-run` creates no files. To export all local release versions:

```text
python tools/export_release.py --settings "../Local settings/video-stabilizer.json" --all-releases
```

`--archive` can select one particular version instead. This helper exports release packages only: it does not alter camera originals, existing renders, or historical diagnostic folders. Stabilization renders still use explicit output paths as described in [REPRODUCING.md](REPRODUCING.md).

A successful export means the local mounted-drive copy matches. The drive application handles upload and remote synchronization separately.

## Checks

```text
python tools/check_release.py --smoke --media-decode
python -m unittest discover -s tools -p "test_*.py"
```

Private settings are outside the exporter file set. The release checker also rejects accidental settings JSON copies in the repository, with the generic example as the sole exception. Existing checks for private absolute user/storage paths remain active.
