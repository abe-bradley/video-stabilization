# Publishing this prepared release

The release directory contains the repository contents. Publication target: [abe-bradley/video-stabilization](https://github.com/abe-bradley/video-stabilization). The active local folder is named `Video stabilization`; historical release archives retain their `kerkyra-stabilizer` filenames.

Repository name: `video-stabilization`.

Suggested description:

> Scene-aware stabilization experiments for a boat sunrise and an aircraft landing. Working vibe code, example videos, and AI-coder adaptation notes.

Suggested topics: `video-stabilization`, `opencv`, `python`, `ffmpeg`, `computer-vision`, `vibe-coding`.

## Check and build

From the release directory:

```text
python tools/check_release.py
python tools/build_release.py
```

The builder writes `dist/kerkyra-stabilizer-v0.1.1.zip` and its checksum sidecar by default. It includes the compact example videos and excludes local virtual environments, originals, generated runs, and Git metadata. It refuses to overwrite an existing ZIP without `--force`. The v0.1.0 ZIP remains the original freeze.

To use a privately configured release folder and export to a mounted drive, follow [LOCAL_WORKFLOW.md](LOCAL_WORKFLOW.md). Supply `--settings` explicitly; no actual destination belongs in this public repository.

After installing requirements, optional deeper checks are:

```text
python tools/check_release.py --smoke --media-decode
python -m unittest discover -s tools -p "test_*.py"
```

These check script startup and bundled-media decode, not stabilization on new footage. See [VALIDATION.md](VALIDATION.md).

## Publish the repository

Create an empty GitHub repository under your chosen account. Do not generate an additional README or license there; they are already provided here. Then, from this release directory, initialize a local repository if necessary and use your normal Git identity:

```text
git init -b main
git add .
git status --short
git commit -m "Release Kerkyra stabilization experiments v0.1.1"
```

Inspect the staged list before committing. It should include seven compact previews under `examples/media/`, not full masters, camera originals, `.venv`, or generated analysis. Set the remote to the exact URL GitHub gives you, then push `main`. Do not use a literal placeholder account as a remote.

Tag the intended commit `v0.1.1`, push that tag, and create a GitHub Release from it. Use [RELEASE_NOTES.md](RELEASE_NOTES.md) for the description. Attach the prepared ZIP and its `.sha256` sidecar. The included GitHub Actions workflow checks snapshot contents and the export helper; its first hosted run only occurs after publishing. The retained v0.1.0 ZIP can be shared as a historical artifact, but do not tag the updated tree as though it were that earlier snapshot.

The MIT code license is already selected. Example video permissions are in [MEDIA_LICENSE.md](../MEDIA_LICENSE.md); keep that file with the footage when sharing the ZIP or a fork. No full camera originals are publicly promised.
