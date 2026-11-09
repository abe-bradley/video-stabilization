# Algorithm map for an AI coding assistant

These are the algorithms in the published scripts, including their clip-specific
choices. Read the implementation before changing it. The release preserves two
successful experiments; it does not establish that these detectors work on new
footage. [Case studies](CASE_STUDIES.md) distinguishes the observed results from
the assumptions an adaptation must revisit.

## Shared geometry and conventions

Both branches solve three rigid image-motion parameters per frame: rotation,
horizontal translation, and vertical translation. A constant crop/zoom is then
applied to every frame. Neither branch estimates a varying perspective warp or
uses changing zoom to hide borders.

| Quantity | Meaning in this code |
| --- | --- |
| Image coordinates | Origin at the top left; x grows rightward and y downward. |
| Frame numbers | Zero-based. CLI end frames are exclusive; many JSON frame ranges have inclusive endpoints. |
| Analysis coordinates | Usually 960 x 540 pixels, half the 1920 x 1080 source dimensions. |
| Source coordinates | Original pixel coordinates; aircraft tracks are recorded in this space. |
| Roll | `atan(fitted_slope)` in degrees, passed to OpenCV's rotation matrix to level the detected line. |
| Pixel scale | A displacement in a 960-wide output is twice as large in the 1920-wide output. Do not confuse this with the extra stabilization zoom. |
| Time | Calculated from frame index and the selected FPS, rather than preserving every original presentation timestamp. |

The feature called a "horizon" is the **lower land-to-water boundary** in these
recordings. It is dark above and bright below. The irregular mountain skyline
has the opposite signed transition. A coastline is not a perfect geometric
horizon: intentional horizontal panning can change which terrain is visible
and move the apparent line. This distinction matters to validation.

The transform fixes a subject's horizontal coordinate while allowing its
vertical coordinate to change relative to the land. Camera roll and vertical
motion are still removed. The sun may rise and the aircraft may descend.

## Sunrise branch

Source: [sunrise/stabilize_sunrise.py](../sunrise/stabilize_sunrise.py).

### Measurements

`detect_horizon` converts the analysis frame to grayscale, applies an anisotropic
Gaussian blur, and computes a signed vertical step: brightness below minus
brightness above. It searches y = 0.38H through 0.70H. Per-column maxima feed a
RANSAC line hypothesis; nearby edge snapping and `weighted_line_fit` refine the
line with Huber-like residual weights. The signed step suppresses the wrong
sky-to-land edge and many wave glints.

`rotation_for_horizon` levels the frame. `detect_sun_x` then measures horizontal
position in that leveled coordinate system using three cues:

1. A compact bright core, using luma above 232 and extra red-channel energy
   above 242.
2. A reflection column in the water, using `0.6*luma + 0.8*max(R-B, 0)`, spatial
   smoothing, per-row median subtraction, and a column sum.
3. A broad warm glow above the waterline, with coarser spatial smoothing.

The core, reflection, and glow scores are weighted by prominence and agreement.
The preferred core threshold includes prominence >= 4 and energy >=
`135 * width/960`. These are 8-bit thresholds from this recording, not physical
brightness measurements.

### Base solution and crop

`build_transforms` interpolates weak horizon samples and weak subject samples,
levels the fitted boundary, and translates it to the median leveled y. It
translates the selected sun anchor to its median x. The compact core is used
when valid; reflection/glow provides fallback evidence.

`common_valid_mask` intersects the valid transformed source region over time.
`largest_safe_16_9_crop` chooses a fixed 16:9 crop. `crop_to_canvas_matrix` composes
that crop with the correction. `render_direct_affines` applies the composed
matrix to the input frames using Lanczos interpolation.

### Early island reference and sun handoff

The final hybrid path uses `track_early_island_velocity`, not the older
`track_early_island_skyline` template helper. It levels the first 175 frames,
extracts a land band above the waterline, removes broad horizontal brightness
variation, applies a Hanning window, and phase-correlates adjacent frames.

`build_velocity_hybrid_anchor` smooths those horizontal increments with sigma
0.35 frames and integrates them. A constant offset aligns the carrier to the
broad sun estimate near emergence. Frames 175 through 234 perform a
raised-cosine transition to the core track. Frame 235 onward uses the core
anchor. At 30 FPS, the 60-frame transition lasts two seconds.

`render_hybrid_island_version` combines this x anchor with the horizon roll/y
solution and computes a fresh safe crop. It renders directly from its supplied
input. When that input is the original, this is one geometric resampling.

### Mode names are historical

`all` runs `analyze`, the base `render`, output analysis, `refine_video`, and
final QA. **It does not call `hybrid`.** Its default output basename includes
`sun-land`, so the basename alone cannot identify the executed algorithm.

`hybrid` consumes an existing analysis directory and calls
`render_hybrid_island_version`. `refine` consumes the appropriate first-pass QA
and builds an empirical correction. Check `main` and the analysis input identity
before reproducing a particular variant; do not infer stage behavior from the
old docstrings or filenames.

`refine_video` composes a measured residual correction onto original-input
transforms and renders from that input. A separate historical feedback
experiment deliberately uses an already stabilized hybrid video as the next
input; that experiment incurs an additional resampling and encode.

### Function map and adaptation points

| Purpose | Functions / settings to inspect |
| --- | --- |
| Boundary polarity, search band, line fitting | `detect_horizon`, `weighted_line_fit` |
| Disk/glow/reflection selection | `detect_sun_x`, `build_transforms` |
| Land band and early timing | `track_early_island_velocity`, `build_velocity_hybrid_anchor`, `render_hybrid_island_version` |
| Cropping and affine composition | `common_valid_mask`, `largest_safe_16_9_crop`, `homogeneous`, `crop_to_canvas_matrix` |
| Residual refinement | `build_qa_corrections`, `refine_video` |
| Video writing | `ffmpeg_raw_writer`, `render_direct_affines`, `render_video` |
| Feedback comparison | [sunrise/compare_feedback.py](../sunrise/compare_feedback.py) |

The default source trim ends before frame 650. The island band uses ratios based
on a 960 x 540 analysis canvas, and the hybrid handoff frames 175/235 are set in
code. A second absolute-template skyline implementation and another hybrid
builder remain as experimental history; changing those functions will not
change the active velocity-hybrid path.

The feedback comparison script separately hardcodes 30 FPS, a 2x analysis-to-
output pixel scale, a 15-frame Gaussian detrend, and segment boundaries
175/235/300. It must be adapted when those assumptions change.

## Airplane branch

Source: [airplane/stabilize_airplane.py](../airplane/stabilize_airplane.py).

### Measurements

`detect_shoreline` uses the same signed-edge idea with a broader source-frame
search band, y = 0.34H through 0.84H. `PlaneTracker` creates a static binary
bright-airframe template from the first frame. Its initial box is scaled from
`x=235, y=545, width=140, height=65` on a 1920 x 1080 source. It thresholds gray
above 215 and locally matches the template near a velocity prediction, refining
the correlation peak to subpixel precision. This is not a general aircraft
detector or a learned object tracker.

`land_mask` selects textured land above the waterline, excludes side margins,
and masks the plane. `estimate_land_motion` uses corner features, forward/backward
KLT consistency, and RANSAC partial-affine registration. It measures translation,
roll, and scale; production registration uses displacement at image center.
The final image transform still has a constant scale. Estimated incremental
scale is also used when carrying the virtual point during the ending.

### Outlier gate and complementary fusion

`solve_transforms` computes absolute shoreline weights from confidence and
contrast. It rejects samples more than 40 analysis pixels from a 61-frame
rolling median of shoreline y. On this clip that rejects frames 924 through
943. Those frame indices are a result of the gate; they are not a hardcoded
list to remove from other footage.

`weighted_fused_path` lightly smooths land increments with sigma 0.35 frames and
integrates them. A weighted Gaussian low-pass of the difference between the
absolute shoreline path and this relative path supplies the slow correction,
using sigma 45 frames. The fused result retains sharp land motion while
anchoring it to absolute shoreline measurements. Because this is a noncausal
offline filter, a bad future sample can contaminate an earlier output frame.
The outlier gate must run **before** this fusion.

### Composition and disappearance

The horizontal target is the 5%-trimmed mean raw aircraft x from reliable visible
samples (score >= 0.45). At least 95% of the configured visible interval must
pass this gate. The target is 620.368 source pixels for this recording, or 32.31%
of frame width. The output shoreline target is `(1 - shoreline_from_bottom)*H`;
the default 0.30 places it at y = 70% from the top.

Direct plane measurements end before frame 1128. A robust recent aircraft-minus-
land velocity estimates the aircraft's relative motion. Frames 1128 through
1159 taper that relative velocity with a raised cosine while transporting the
virtual reference by the land partial affine. From frame 1160 onward only land
motion transports the reference. Horizontal composition is solved from this
virtual point; shoreline roll/y stabilization remains active through frame
1222. This is a transform handoff, not a dissolve between rendered images.

`build_output_affines` constructs one matrix per frame, including rotation,
translations, and a fixed zoom. `affines_are_safe` inverse-maps all output
corners into the source. `solve_transforms` binary-searches zoom between 1 and
3.5 and applies the 1.02 safety multiplier. If the final crop exposes a border,
it raises rather than providing a fallback crop strategy.

### Function map and adaptation points

| Purpose | Functions / settings to inspect |
| --- | --- |
| Initial subject and appearance | `PlaneTracker.__init__`, `PlaneTracker.update` |
| Boundary and background registration | `detect_shoreline`, `land_mask`, `estimate_land_motion` |
| Outliers, filter windows, trim, handoff | `solve_transforms`, `weighted_fused_path` |
| Target fractions and fixed zoom | `build_output_affines`, `affines_are_safe` |
| HDR master | `open_hdr_decoder`, `open_hdr_writer`, `render_hdr_master` |
| SDR preview | `open_sdr_decoder`, `open_sdr_writer`, `render_sdr_preview` |
| Independent rendered-frame checks | `analyze_preview_qa` |

The default CLI values 1128 visible-end, 1223 output-end, and 32 handoff frames
belong to this source. The 61/45/120-frame windows also depend on FPS. Several
spatial thresholds are analysis pixels rather than normalized image fractions.
The QA seed box is separately hardcoded using `/2`, assuming the default
960 x 540 analysis resolution; changing `--analysis-width` alone does not make
that QA path resolution-independent.

## Color, audio, and timing are branch-specific

The sunrise writer takes 8-bit BGR, writes H.264, declares full-range BT.709,
and uses `-an`. The original sunrise has no audio. Passing arbitrary HDR or
audio-bearing material through that writer will not preserve those properties.

The aircraft HDR decoder explicitly interprets its input as limited-range HLG
BT.2020, converts to 16-bit RGB, and renders with a `uint16` OpenCV warp. The
writer produces 10-bit HEVC `hvc1`, HLG/BT.2020, with the first audio stream
copied. That matches the source's ordinary AAC stereo stream and omits its
additional Apple spatial-audio stream. This branch does not automatically
choose a color pipeline from every possible source's metadata.

The raw-frame boundary drops Dolby Vision configuration and per-frame RPU
metadata. The rendered master is plain HLG, not Dolby Vision. SDR previews use
an explicit Hable tone map and BT.709 output. Preserve that distinction when
adapting the render path.

Both branches render at a constant rate derived from the source. The aircraft
uses `avg_frame_rate` as an exact fraction; it retains all frames but does not
preserve the source's slight frame-by-frame timing variation. Input audio is
limited to the rendered duration, and the writers no longer use `-shortest`:
that option dropped two final picture frames during the full-tail experiment.

## What the QA actually measures

Aircraft QA re-detects plane x and registers adjacent decoded preview frames
using land KLT/RANSAC, masking water and the plane. It evaluates roll and
vertical displacement at the expected shoreline height and ignores intended
horizontal background motion. It reports cumulative vertical displacement as
well as per-pair metrics. Shoreline fitting is a separate geometry-coverage
diagnostic, with no interpolation across bad fits.

Do not call a result perfect because its per-pair RMS is small. A long coherent
drift can be visible even when every individual step is small. Likewise, the
historical feedback gains are empirical comparisons on one clip, not a proof
that repeated processing is stable on every input. Inspect the video, the
tracks, the worst interval, the crop, and the complete ending.
