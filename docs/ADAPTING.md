# Adapting this with an AI coding assistant

This repository is a record of code that worked on two particular videos. Use
it as working material for your own experiment. The release keeps the existing
scripts intact, including their clip-specific numbers, separate workflows, and
rough edges. See [START_HERE](../START_HERE.md) for the main handoff prompt and
[ALGORITHMS](ALGORITHMS.md) for the function map.

Start by watching [the seven bundled previews](../examples/README.md). They are
included in both the repository and release ZIP and make the original goals
visible without a separate download. They are 640×360 SDR viewing proxies;
their pixels, color, and sometimes frame rate differ from the original camera
inputs. Use them to explain the desired effect to your assistant. If you want
to process a proxy, ask for an adaptation to that proxy rather than running the
unchanged camera-source settings.

## Give your assistant a concrete brief

Open the whole repository in your coding tool and give it access to your video.
The useful information is:

- The input file and a new output directory.
- What should hold still: roll, shoreline height, subject x, subject y, or a
  subset of those. In both Kerkyra examples, subject y is intentionally free.
- Where the subject and shoreline are visible, and where either disappears.
- Desired composition, such as “keep the subject at its average original x”
  and “put the waterline 30% above the bottom.”
- Whether you want an SDR inspection copy, an HDR master, or both.

If you do not know the technical details, ask the assistant to inspect the
media and sampled frames first. It should report what it found before choosing
the tracking signals. A clean island shoreline, an open-sea horizon, and an
irregular mountain skyline are different visual features.

Suggested addition to your prompt:

> Start with the existing script closest to my shot. Read its hardcoded
> assumptions and media pipeline. Inspect my source metadata and representative
> frames, then make the smallest useful adaptation in a new file. Keep the
> original recording untouched. Save detections, confidence, transforms, and a
> compact color preview so we can inspect the result before a full render.
> Explain which coordinates and timing values you changed and why.

## Choose the closest experiment

Use [the sunrise script](../sunrise/stabilize_sunrise.py) when a sun or bright
reflection is a useful horizontal reference and a shoreline supplies roll and
vertical position. Its `hybrid` path uses distant-land velocity before the
compact sun disk becomes reliable.

Use [the airplane script](../airplane/stabilize_airplane.py) when a recognizable
moving subject should hold a chosen x position while retaining its real
vertical motion. It uses textured land for camera motion, the shoreline as an
absolute reference, and a background handoff after the subject is occluded.

The airplane script is currently wired to HLG/BT.2020 input and output. An
ordinary SDR clip needs an intentional media-pipeline edit as well as a new
tracking box. The sunrise script uses an 8-bit SDR path. Neither script is a
generic color-management frontend.

## Hardcoded settings the assistant should inspect

| Area | Current assumption | Where to adapt it |
| --- | --- | --- |
| Input/output paths | Historical script-relative filenames and directories | `DEFAULT_*` definitions; use explicit CLI paths while experimenting |
| Sunrise shoreline | Positive dark-land-to-bright-water step, searched at 38–70% of image height | `detect_horizon()` |
| Sunrise brightness | Near-white disk, warm glow, and water reflection thresholds in 8-bit detector values | `detect_sun_x()` and `build_transforms()` |
| Sunrise land band | Horizontal range 20–940 and a band 120–5 px above the waterline, expressed relative to a 960×540 analysis canvas | `track_early_island_velocity()` |
| Sunrise timing | End at frame 650; land reference through 174, handoff 175–234, sun reference afterward | CLI `--end-frame`; constants inside `render_hybrid_island_version()` |
| Aircraft seed | Box `(x=235, y=545, width=140, height=65)` on the 1920×1080 source; white-airframe threshold 215 | `PlaneTracker.__init__()` and `update()` |
| Aircraft QA seed | A second copy of the original box, scaled by a literal `/ 2` | `analyze_preview_qa()`; keep it consistent with the production tracker and analysis dimensions |
| Aircraft shoreline | Positive land/water step, searched at 34–84% of image height | `detect_shoreline()` |
| Aircraft timing | Fully visible frames 0–1127; 32-frame handoff; 1223 output frames | `--plane-visible-end`, `--handoff-frames`, `--output-end` |
| Aircraft outlier gate | 61-frame rolling median and a 40-analysis-pixel rejection threshold | `solve_transforms()` |
| Aircraft fusion | 45-frame slow absolute correction and 0.35-frame increment smoothing | `solve_transforms()` and `weighted_fused_path()` |
| Aircraft composition | Trimmed mean of raw subject x; shoreline 30% from bottom | `solve_transforms()`; `--shoreline-from-bottom` |
| Color and sound | Sunrise SDR without audio; airplane HLG10 with the first AAC audio track | Writer/decoder helpers in each script |
| Feedback metrics | 30 fps, 960×540 measurements, ×2 output-pixel scale, fixed handoff segments | Constants in `sunrise/compare_feedback.py` |

The historical skyline-template helpers also remain in the sunrise file. They
are useful ideas, but the released `hybrid` command calls the velocity/phase
correlation path. Follow the actual call graph when changing behavior.

The reference JSON files, if consulted, describe the original experiments.
The scripts do not load them as configuration. Editing one does not change a
render; the assistant must edit the script or add that capability explicitly.

## Keep coordinate systems straight

Track whether a value belongs to the full source, the downscaled analysis
frame, the rotated analysis frame, or the cropped output. OpenCV image y grows
downward. “30% from bottom” therefore means output y = 70% of height from top.
Source and output pixel coordinates can differ substantially after zoom.

Several thresholds are fixed pixels rather than normalized values. Changing
`--analysis-width` alone does not rescale every setting, and airplane QA still
contains the explicit 960×540 seed assumption. Both examples were landscape
1920×1080 recordings; different aspect ratios need inspection and edits.

Frame counts describe these exact recordings. Before changing frame rate,
trim, speed, or sampling, convert transition windows and smoothing widths to
their intended durations. At 30 fps, sunrise frame 175 is about 5.83 s and
frame 235 about 7.83 s. At the airplane source's average 59.97058 fps, frame
1128 is about 18.81 s and 32 frames about 0.53 s.

## A useful adaptation loop

1. Probe the source, then inspect the first frame, difficult intervals, and the
   ending. Check rotation, frame timing, color tags, and audio streams.
2. Change the seed, search region, masks, and timing appropriate to those
   observations. Keep the geometric objective explicit.
3. Run analysis and inspect the overlay and raw tracks. Fix false detections
   before smoothing them. Noncausal smoothing can spread one false block into
   an earlier visible dip, as the airplane case demonstrated.
4. Solve and report the required crop. A fixed zoom avoids breathing; a large
   correction can demand substantial crop even when the detector is correct.
5. Render a compact preview and inspect it in motion, especially at handoffs.
   A contact sheet helps composition but cannot establish absence of jitter.
6. Measure both frame-to-frame residuals and sustained drift. Compare the
   detector overlay to actual visual features when a confidence score looks
   reassuring but the picture moves incorrectly.
7. Render the chosen result from the original frames. If adding a residual
   correction, compose transforms where practical rather than accumulating
   repeated geometric resampling and compression.

Use a distinct run directory for a different input or experiment. The scripts
cache absolute input paths in JSON and reject some mismatched analysis/input
combinations. They do not identify inputs by content hash, and existing FFmpeg
outputs are overwritten with `-y`.

## What the assistant should not assume

- `all` has the same meaning in both scripts. Sunrise `all` follows the
  sun-only/refinement workflow; airplane `all` includes its full refined path.
- Sunrise `refine` knows about the hybrid base. It rebuilds `build_transforms()`
  and the sun-only crop; hybrid residual correction needs its own composition.
- A high detector confidence means the physical feature is correct. The
  airplane foreground-edge error passed that test and required a temporal gate.
- A low whole-clip velocity RMS means there is no noticeable excursion.
- Running the feedback experiment indefinitely guarantees convergence. Its
  observations apply to one second pass on this clip.
- HDR tags can be copied onto an 8-bit SDR render. The HLG master uses a
  separate uint16 rendering path and deliberately drops Dolby Vision RPU data.
- The average-frame-rate rawvideo writer preserves arbitrary variable-frame
  timestamps. It does not; a future timestamp-aware path is separate work.

If an adaptation becomes broadly useful, keep the new behavior and its evidence
in the fork. A later editor-oriented interface can grow from those observed
needs; this release's purpose is to make the original experiment legible.
