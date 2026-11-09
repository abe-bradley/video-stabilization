# Instructions for AI coding assistants

## Purpose

This is a source snapshot of two successful, circumstance-specific video experiments. Help a user adapt the code to their shot. Keep the scope small unless they explicitly ask for a reusable tool or editor interface.

## Read first

- `START_HERE.md`: the intended user handoff.
- `docs/ADAPTING.md`: adaptation workflow and hardcode map.
- `docs/ALGORITHMS.md`: the actual call paths, coordinate conventions, and media behavior.
- `docs/REPRODUCING.md`: explicit-path commands for this repository layout.
- `presets/*.json`: descriptive records only, not executable configuration.

## Work from evidence

- Inspect the user's source metadata and sample frames before changing detectors or rendering.
- Establish constrained axes and preserved real motion with the user. Sun/aircraft y is not locked in the original examples.
- Inspect code, not just filenames: Sunrise `all` is sun-only; `hybrid` is island-to-sun; `refine` rebuilds the sun-only base.
- The aircraft seed, its QA seed, frame boundaries, HLG pipeline, and shoreline search are specific to the original video. Changing one CLI flag does not generalize them.
- Use fresh run/output paths. Analysis caches contain input paths and must match the intended input. FFmpeg uses `-y`.
- Preserve source footage. Start an adaptation in a separate script or branch; keep the frozen examples available for comparison. If the user asks to change them directly, update the snapshot record with an honest description.
- Render a diagnostic preview before a full master. Check crop borders, coherent drift, occlusion/handoff, frame count, and audio/color assumptions. Adjacent-frame RMS alone can miss a visible dip.
- Compose corrections and resample from the original once where practical. A literal rerun of encoded output adds a geometric and encoding generation.
- Write down what was changed, why, and which exact footage was checked. Avoid claims of general reliability or formal feedback stability from these two cases.

## Repository maintenance

The three original `.py` files are byte-for-byte snapshots identified in `provenance/code-snapshot.json`. `.gitattributes` preserves their line endings. Packaging and docs can change without altering them. `python tools/check_release.py` validates the release contents, not behavior on arbitrary footage.

Do not add raw masters, local environments, private absolute paths, or generated analysis runs to Git. Compact example proxies in `examples/media/` are intentionally tracked. Treat those proxies as visual references: their dimensions, timing, and color differ from the camera originals.

Code/docs are MIT licensed; example footage follows `MEDIA_LICENSE.md`. The future editor experience is in `ROADMAP.md` and is outside this snapshot's implementation scope.

## Local release/export workflow

This is the active development copy. Keep the original working archive and the v0.1.0 ZIP as historical baselines. Read `docs/LOCAL_WORKFLOW.md` before building or exporting releases. Actual destinations belong in an explicitly supplied settings file outside this repository; only `docs/local-settings.example.json` is public. The exporter copies versioned release ZIPs and checksum sidecars, never source footage or analysis caches. Do not put actual local/cloud paths into code, docs, manifests, or commits.
