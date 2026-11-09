# Kerkyra sunrise semantic stabilizer

This project stabilizes the uploaded boat timelapse from scene geometry rather
than generic optical flow:

- The dark-land-to-bright-water boundary supplies roll and vertical position.
- Before the disk is visible, the broad glow and vertically averaged reflection
  supply horizontal position.
- Once the compact solar disk appears, its saturated core becomes the horizontal
  anchor.

The source video is never modified. The default exclusive end frame is 650,
because frame 650 begins the final pan away from the sunrise and frame 651 shows
the outboard motor.

## Version names

Full-resolution videos beside the project folder use `sunrise_[method]`:

- `sunrise_source.mov` — original camera file
- `sunrise_sun-only.mp4` — horizon plus glow/reflection/sun anchoring
- `sunrise_sun-land.mp4` — island-velocity anchoring before sunrise, then sun
- `sunrise_sun-land-2pass.mp4` — literal closed-loop experiment: run the
  sun-land result through the same hybrid algorithm once more

Future methods should follow the same naming scheme. Preview copies use the
same basenames in resolution-specific folders:

- `Low-resolution previews/` — 854x480, roughly 6-7 MB each
- `Low-bandwidth previews/` — 640x360 H.264 Baseline, roughly 1.8 MB each

## Closed-loop feedback experiment

The two-pass version deliberately uses `sunrise_sun-land.mp4` as its input,
which tests the stabilizer in the feedback sense. It is kept separate from the
one-pass master because it incurs a second geometric resampling and H.264
encode. The second pass used only 1.053x additional zoom and produced the
following high-frequency residual reductions:

- horizon roll RMS: 0.0649 to 0.0257 degrees (2.53x)
- horizon-height RMS: 0.850 to 0.278 output pixels (3.05x)
- early island velocity RMS: 0.626 to 0.125 pixels/frame (5.00x)
- late visible-sun x RMS: 0.576 to 0.258 output pixels (2.23x)

All fitted residual gains are between zero and one, so this iteration is
convergent rather than oscillatory. The remaining signal is close to the
detector/codec floor; a third literal pass is not recommended. The experiment
can be reproduced without touching the first-pass diagnostics:

```powershell
python .\stabilize_sunrise.py analyze `
  --input ..\sunrise_sun-land.mp4 `
  --analysis-dir '.\Diagnostics\Sun-land 2-pass feedback'

python .\stabilize_sunrise.py hybrid `
  --input ..\sunrise_sun-land.mp4 `
  --analysis-dir '.\Diagnostics\Sun-land 2-pass feedback' `
  --output ..\sunrise_sun-land-2pass.mp4
```

If the two-pass composition is chosen as the final look, its residual affine
can instead be composed with the original transforms and rendered once from
`sunrise_source.mov`, avoiding a second resampling generation.

## Run

Install the packages in `requirements.txt`, then run the complete measured and
verified pipeline:

```powershell
python .\stabilize_sunrise.py all
```

Useful individual stages are:

```powershell
python .\stabilize_sunrise.py analyze
python .\stabilize_sunrise.py render
python .\stabilize_sunrise.py refine
python .\stabilize_sunrise.py hybrid
```

The default final render is written beside the source as
`sunrise_sun-land.mp4`. Tracking CSVs, detector overlays, plots, crop
summaries, and residual QA measurements are under `Diagnostics/` beside this
script. The diagnostics include separate `QA first pass/` and `QA final/`
folders.

The final correction is composed back onto the original frames, so the delivered
video undergoes one geometric resampling rather than accumulating two passes.

The `hybrid` mode improves the pre-sun section by tracking the distant island
silhouette band as a sharp horizontal velocity reference. Adjacent leveled
frames are phase-correlated, and the integrated motion track replaces the broad
glow/reflection detector before emergence. A two-second raised-cosine transition
then hands control to the compact solar disk. The result is still rendered
directly from the original source in one resampling pass.
