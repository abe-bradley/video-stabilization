# Kerkyra airplane stabilizer

This is the independent airplane branch of the Kerkyra stabilization project.
The refined pipeline turns `airplane_source.MOV` into
`airplane_plane-lock-refined.mp4`. The earlier `airplane_plane-lock.mp4` is
preserved as the first version rather than overwritten, and the sibling
Sunrise project remains separate.

## Portable layout

```text
Airplane/
|-- airplane_source.MOV
|-- airplane_plane-lock.mp4             # first version
|-- airplane_plane-lock-refined.mp4     # refined full-length master
`-- Stabilization project/
    |-- stabilize_airplane.py
    |-- requirements.txt
    |-- README.md
    `-- Diagnostics refined/
```

The script resolves its defaults from its own location, so the complete
`Airplane` folder can be moved without editing paths. `airplane_source` means
the untouched original; `airplane_plane-lock` and
`airplane_plane-lock-refined` identify the two stabilization methods.

## Refined stabilization model

The image transform has exactly three rigid degrees of freedom followed by one
constant crop/zoom:

- Roll and vertical translation come from absolute shoreline measurements
  fused with land-only KLT/RANSAC feature motion. A temporal-median outlier gate
  rejects frames **924–943**, where a foreground edge briefly impersonates the
  shoreline; this removes the false dip that had appeared around 14.5 seconds.
- The shoreline target is 30% up from the bottom, or **y = 70% from the top**.
- Horizontal translation comes only from the airplane track while it is
  visible. Its robust mean raw-frame x position, **620.368 px (32.31% of the
  1920 px width)**, remains the composition target, keeping the aircraft in the
  left half.
- Airplane y is never locked, so its real descent remains visible.
- After foreground occlusion begins, frames **1128–1159** use a 32-frame
  raised-cosine handoff from predicted airplane motion to background-only x
  motion. Frames **1160–1222** use the land background alone for x. This is a
  smooth transform handoff, not a pixel dissolve: shoreline/KLT roll and
  vertical stabilization remain active through the final frame.
- One fixed **2.1596326x** safe zoom prevents borders without crop breathing.

All **1223 frames (20.393 s)** are retained, including the final post-landing
view.

## Install and run

Python 3.10+ and FFmpeg/FFprobe on `PATH` are required. From
`Airplane/Stabilization project`:

```powershell
python -m pip install -r requirements.txt
python .\stabilize_airplane.py all
```

`all` performs analysis, solves the transforms, renders an SDR preview, runs
preview QA, and renders the HDR master. Each stage can also run independently:

```powershell
python .\stabilize_airplane.py analyze
python .\stabilize_airplane.py solve
python .\stabilize_airplane.py preview
python .\stabilize_airplane.py qa
python .\stabilize_airplane.py render
```

The refined defaults are:

- input: `../airplane_source.MOV`
- analysis: `Diagnostics refined/`
- preview: `Diagnostics refined/airplane_plane-lock-refined_preview.mp4`
- master: `../airplane_plane-lock-refined.mp4`
- analysis width 960; visible-aircraft end 1128; output end 1223
- shoreline 0.30 from bottom; handoff 32 frames; zoom safety 1.02
- preview CRF 18; master CRF 17; `slow` encoder preset

Use `--input`, `--analysis-dir`, `--preview`, or `--output` to override paths.
The numerical behavior can be changed with `--analysis-width`,
`--plane-visible-end`, `--output-end`, `--shoreline-from-bottom`,
`--handoff-frames`, and the encoding/zoom options shown by `--help`.

## Output color and QA

The master is 1920x1080 10-bit HEVC, preserving the source's HLG transfer and
BT.2020 color metadata, with its AAC stereo track retained. A geometric
rerender cannot preserve the source's per-frame Dolby Vision RPU metadata, so
the result is standards-compliant HLG10/BT.2020 rather than Dolby Vision.

`preview` produces an explicitly tone-mapped H.264 SDR inspection copy. The
separately supplied low-resolution and low-bandwidth copies are also SDR for
phone and browser compatibility.

`Diagnostics refined` contains the detections, solved tracks, JSON summaries,
plots, contact sheets, detection overlay, and independent render checks in
`qa_metrics.json` and `qa_tracks.csv`. QA re-detects the airplane to verify its
x composition, then measures adjacent rendered frames using land-only
KLT/RANSAC. That primary check covers all 1222 frame pairs and evaluates
residual roll and vertical motion while intentionally ignoring horizontal land
motion. The shoreline fit is retained as a full-frame geometry/coverage check,
not misread as temporal jitter when the pan reveals different terrain.
