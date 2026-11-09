# Reproducing the Kerkyra experiments

These commands address the scripts as released. They reproduce the intended
workflows with the original media; encoder/library changes can change output
bytes and small detector measurements. They are also examples for an AI coding
assistant adapting the scripts. Read [ADAPTING](ADAPTING.md) before using other
footage.

## Environment

The original work ran on Windows with Python, NumPy, OpenCV, Pillow, SciPy, and
an FFmpeg 8.0 full build. The release's `requirements.txt` records the existing
minimum dependency specifications, not a historical environment lockfile.
The scripts use Python 3.10+ syntax. Other operating systems are adaptation
targets; they were not validated in the original work.

From the repository root, make an isolated environment and install the listed
dependencies. On PowerShell:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
ffmpeg -version
ffprobe -version
```

The equivalent activation on a Unix-like shell is `source .venv/bin/activate`
after `python3 -m venv .venv`. Install FFmpeg separately so `ffmpeg` and
`ffprobe` are on `PATH`. The scripts invoke them as external processes.
The required FFmpeg capabilities include `libx264`; the airplane HDR and SDR
pipelines additionally need `libx265`, `zscale`, and `tonemap`. A Python
OpenCV wheel alone does not supply these external commands.

## Original media

The repository and release ZIP include seven compact before/after previews in
[`examples/media/`](../examples/README.md): four sunrise versions and three
airplane versions. Even the files named `*_source.mp4` are viewing proxies of
the camera sources. They are 640×360 SDR copies; in particular, the airplane
previews run at 30 fps while its original is HLG at about 60 fps.

The full original camera files are excluded. No public raw-source download URL
is provided by this release. The commands below require those camera files;
they are a reproduction recipe for someone who has them, not a one-command
demo using the bundled proxies. Otherwise, use your own media through the
adaptation workflow. Processing one of the proxies is also an adaptation: its
resolution, color, timing, and detector thresholds must be considered first.

Place any original files in the following local layout:

```text
kerkyra-stabilizer/
  sunrise/
    stabilize_sunrise.py
    compare_feedback.py
    sunrise_source.mov       # optional local original; not tracked
  airplane/
    stabilize_airplane.py
    airplane_source.MOV      # optional local original; not tracked
  runs/                      # generated locally; not tracked
```

| Original | Expected properties | SHA-256 |
| --- | --- | --- |
| `sunrise_source.mov` | 40,543,136 bytes; 1920×1080; 658 frames; 30 fps; 21.9333 s; H.264, full-range BT.709 SDR; no audio | `9FD6C6ACC69EB8A40FA9A5EFCFB886892069B1DD31895E37DC99838659E4C53D` |
| `airplane_source.MOV` | 35,217,815 bytes; 1920×1080; 1223 frames; 20.3933 s; HEVC Main10 HLG/BT.2020 with Dolby Vision profile 8.4 metadata; AAC stereo plus Apple APAC/data streams | `02378055B3535FA703716C788F176B145DAE6D5B5B06B8C6D7C92A2696691B89` |

For example, PowerShell can verify a file with
`Get-FileHash -Algorithm SHA256 -LiteralPath sunrise/sunrise_source.mov`.
The airplane source has slight frame-duration variation. The current render
uses its average rate `183450/3059` (about 59.97058 fps), rather than preserving
each original presentation timestamp.

All commands below run from the repository root. They use explicit paths
because the preserved scripts' historical `DEFAULT_INPUT` resolves relative to
the script's parent. In this repository layout that default points at the
repository root, not the adjacent local-media location shown above. They also
use separate analysis/output paths to avoid mixing experimental results.

FFmpeg uses `-y`, so rerunning a command replaces its generated outputs. The
input/output identity guards do not prevent replacing a previous render at a
different path. The commands below never use the source path as an output.

## Sunrise: island-to-sun result

The source contains 658 frames, but the experiment keeps frames 0–649. Frame
650 begins the pan away and the following frames reveal the outboard motor.
The 175/235-frame handoff values are hardcoded in the hybrid function.

First analyze the original; then run the hybrid renderer using that analysis:

```powershell
python sunrise/stabilize_sunrise.py analyze --input sunrise/sunrise_source.mov --analysis-dir runs/sunrise/source --end-frame 650 --analysis-width 960
python sunrise/stabilize_sunrise.py hybrid --input sunrise/sunrise_source.mov --analysis-dir runs/sunrise/source --output runs/sunrise/sunrise_sun-land.mp4 --crf 16 --preset slow
```

The result should contain 650 frames (21.6667 s), a level waterline, early
island-based horizontal stabilization, and a handoff to the visible sun.
`horizontal_anchor_v2.csv`, `horizontal_anchor_v2.png`, and
`hybrid_render_summary.json` are saved in the source analysis directory.

To independently measure that render, run analysis on it in a new directory:

```powershell
python sunrise/stabilize_sunrise.py analyze --input runs/sunrise/sunrise_sun-land.mp4 --analysis-dir runs/sunrise/hybrid-qa --end-frame 650 --analysis-width 960
```

That measures the result; it does not automatically apply a residual correction.
Sunrise `refine` rebuilds a sun-only base, so it should not be fed hybrid QA and
described as a hybrid-preserving correction. The two workflows differ.

The source analysis summary stores the input's absolute path. If media is moved,
reanalyze it at the new location rather than reusing stale analysis metadata.
The hybrid summary's `input_is_configured_original` flag compares against the
historical default path, so it may be false with the explicit local layout even
when the file's hash is the correct original.

## Sunrise: earlier sun-only workflow

`all` runs analyze, sun-only render, analysis of that render, source-based
sun-only refinement, and final QA. It does not call `hybrid`, despite the
historical output default using a `sun-land` filename. Give it the explicit
sun-only output name:

```powershell
python sunrise/stabilize_sunrise.py all --input sunrise/sunrise_source.mov --analysis-dir runs/sunrise/sun-only-analysis --qa-dir runs/sunrise/sun-only-qa --output runs/sunrise/sunrise_sun-only.mp4 --end-frame 650 --analysis-width 960 --crf 16 --preset slow
```

This path is retained as part of the experiment. The island-to-sun commands
above are the intended route for the improved early horizontal stabilization.

## Sunrise: literal feedback experiment

This deliberately uses the first hybrid render as the next input. It incurs
another geometric resample and H.264 encode. Run it once for the recorded
experiment; repeated passes were not established as a useful general strategy.

```powershell
python sunrise/stabilize_sunrise.py analyze --input runs/sunrise/sunrise_sun-land.mp4 --analysis-dir runs/sunrise/feedback --end-frame 650 --analysis-width 960
python sunrise/stabilize_sunrise.py hybrid --input runs/sunrise/sunrise_sun-land.mp4 --analysis-dir runs/sunrise/feedback --output runs/sunrise/sunrise_sun-land-2pass.mp4 --crf 16 --preset slow
python sunrise/stabilize_sunrise.py analyze --input runs/sunrise/sunrise_sun-land-2pass.mp4 --analysis-dir runs/sunrise/feedback/QA --end-frame 650 --analysis-width 960
python sunrise/compare_feedback.py --before-tracks runs/sunrise/feedback/tracks.csv --after-tracks runs/sunrise/feedback/QA/tracks.csv --before-video runs/sunrise/sunrise_sun-land.mp4 --after-video runs/sunrise/sunrise_sun-land-2pass.mp4 --output-json runs/sunrise/feedback/feedback_stability_metrics.json --output-plot runs/sunrise/feedback/feedback_stability_before_after.png
```

The “before” CSV must contain measurements of the first output, not measurements
of the unstabilized camera source. `compare_feedback.py` assumes 30 fps,
960×540 analysis, and 1920×1080 output; adapt its constants for other media.
See [CASE_STUDIES](CASE_STUDIES.md) for the recorded second-pass observations.

## Airplane: refined full-length result

The released airplane script is the refined iteration. It retains all 1223
frames, rejects the bad shoreline block at frames 924–943, uses the aircraft's
trimmed mean raw x as its target, and places the waterline 30% above the bottom.
It predicts the occluded aircraft across frames 1128–1159, then uses background
motion alone for the horizontal reference through frame 1222.

The complete workflow, with all generated paths explicit:

```powershell
python airplane/stabilize_airplane.py all --input airplane/airplane_source.MOV --analysis-dir runs/airplane/analysis --preview runs/airplane/airplane_plane-lock-refined_preview.mp4 --output runs/airplane/airplane_plane-lock-refined.mp4 --analysis-width 960 --plane-visible-end 1128 --output-end 1223 --shoreline-from-bottom 0.30 --handoff-frames 32 --zoom-safety 1.02 --preview-crf 18 --master-crf 17 --preset slow
```

For inspection before the expensive master encode, invoke `analyze`, `solve`,
`preview`, and `qa` separately with the same explicit paths and options, then
run `render`. Those are real CLI mode names; `all` performs them in that order.
Do not change the input or analysis settings between stages without rebuilding
the analysis and solution.

Expected master properties are 1920×1080, 1223 frames, about 20.3933 s,
HEVC Main10 / `hvc1` / `yuv420p10le`, limited-range HLG BT.2020, and the first
AAC stereo track. The APAC and metadata streams are omitted. Dolby Vision RPU
metadata is not retained after the geometric render; the output is plain HLG10.
The historical safe zoom was approximately 2.1596326×. The preview is explicitly
tone-mapped SDR H.264 at the 960×540 analysis size.

The original 1160-frame airplane version is historical media rather than a
separate CLI preset in this release. Merely setting `--output-end 1160` does
not restore its former near-center framing or remove the newer outlier gate.

## Check a result before sharing it

Inspect the detection overlay, tracking CSV, solved-transform summary, and
the rendered preview. In the airplane QA, land registration ignores the
intentional horizontal scene motion and measures roll/vertical residuals.
The added cumulative vertical metric helps reveal sustained excursions that
an average adjacent-frame RMS can obscure. Coastline geometry is diagnostic:
horizontal panning can reveal different terrain.

FFmpeg can check the complete mapped video/audio decode, for example:

```powershell
ffmpeg -hide_banner -loglevel error -xerror -i runs/airplane/airplane_plane-lock-refined.mp4 -map 0:v:0 -map 0:a:0 -f null -
ffprobe -v error -count_frames -show_streams -show_format -of json runs/airplane/airplane_plane-lock-refined.mp4
```

For a silent sunrise result, omit `-map 0:a:0`. An independent frame count and
an actual moving preview are needed in addition to a contact sheet. The
original examples and reference measurements describe these videos only;
they are starting points for comparison, not acceptance thresholds for every
new scene.
