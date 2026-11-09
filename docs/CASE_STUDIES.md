# What worked on the two Kerkyra recordings

These notes preserve the development evidence for another person and their AI
coding assistant. The measurements describe these recordings and these
specific renders. They are not a support matrix or a promise about another
shoreline, subject, camera, frame rate, or codec.

Read [ALGORITHMS.md](ALGORITHMS.md) for the implementation map and hardcoded
assumptions. The original source files and full diagnostic history are not
required reading for an adaptation; when reproducing a historical number,
however, use the corresponding original media and tracks rather than a small
demonstration preview.

## Sunrise from a moving boat

The original is a landscape 1920 x 1080 H.264/BT.709 recording with 658 frames at
30 FPS, lasting 21.933 seconds, with no audio. The delivered shot keeps frames
0 through 649, lasting 21.667 seconds. Frame 650 starts a final pan away from
the scene, and the next frame reveals the boat motor.

The scene provided unusually useful constraints: a dark continuous island
above bright water and a compact sun/reflection/glow column. The goal was to
keep the lower island boundary level and at fixed height, and to hold sun x
while allowing the real sunrise in y.

### Development sequence

1. `sunrise_sun-only.mp4` used the signed boundary detector and the sun's bright
   core, reflection, and glow.
2. `sunrise_sun-land.mp4` improved pre-sun horizontal motion by measuring land
   velocity. A raised-cosine transition over frames 175 through 234 transferred
   the anchor to the compact sun.
3. `sunrise_sun-land-2pass.mp4` was a deliberate feedback experiment. The hybrid
   output became the next hybrid input, followed by another geometric warp and
   H.264 encode. This was retained as an experiment alongside the first result.

The distinction in step 3 matters. The code also supports measuring a residual
on a preview, composing that residual with original-input transforms, and
rendering from the original once. A literal reprocessing experiment and a
single-render composed correction have different resampling histories.

### Historical feedback measurements

The archived comparison in `compare_feedback.py` used a 960 x 540 detector
canvas, a factor of 2 to report 1920 x 1080 output pixels, and a Gaussian
detrend with sigma 15 frames (0.5 seconds). Metrics exclude segment-edge samples
using the script's trim rule. Values below are rounded.

| Measured residual | Before | After one extra pass | RMS reduction |
| --- | ---: | ---: | ---: |
| Horizon roll, high-pass RMS | 0.06494 degrees | 0.02571 degrees | 2.53x |
| Horizon y, high-pass RMS | 0.8500 output px | 0.2783 output px | 3.05x |
| Sun x, high-pass RMS, frames 235–649 | 0.5764 output px | 0.2580 output px | 2.23x |
| Early island displacement RMS | 0.6260 output px/frame | 0.1252 output px/frame | 5.00x |

The empirical least-squares residual gains had magnitude below one in this
comparison. That was evidence of contraction for this measured pass, not a
general feedback-stability proof. Some segment estimates were slightly negative
near the measurement floor. Repeating the process indefinitely may chase
detector/codec noise and adds crop or resampling cost. We stopped after the
extra pass.

The original archive's README and JSON contain historical naming and provenance
paths. Path relocation alone does not make those caches valid render inputs:
the scripts compare the analyzed input's absolute path with the render input.
Regenerate analysis for a relocated source, or explicitly adapt the cache
identity mechanism with an understanding of what the tracks represent. Do not
silently change a provenance path to claim a different source was analyzed.

### Lessons for an adaptation

- A signed land-to-water step was more selective than an undirected edge map.
- The broad glow and reflection were useful cues but less precise horizontal
  carriers before the disk was clear; land velocity supplied the missing
  short-term information.
- Holding sun x while leaving sun y free retained the natural event.
- Frame-based handoffs and detector regions were chosen for this clip. A new
  scene needs its own emergence interval and valid land band.
- Re-detecting an output is useful, but output measurements share detector
  assumptions. View the motion and check an independent feature when possible.

## Aircraft landing from the boat

The original is a landscape 1920 x 1080 HEVC Main 10 recording with 1223 frames,
20.393333 seconds, and average FPS `183450/3059` (about 59.97058). It carries HLG
BT.2020 with Dolby Vision profile 8.4 metadata. Its first audio stream is AAC-LC
48 kHz stereo; an additional Apple spatial-audio stream is present.

The camera moves strongly while a small white aircraft crosses textured land.
The intended composition places the plane in the left half. Its robust mean
raw x is 620.368 pixels, or 32.31% of the original width. That becomes the
horizontal output target. The plane's real descent remains visible.

### First result and refinements

The first `airplane_plane-lock.mp4` used shoreline/land motion for roll and y,
plane x for horizontal composition, and a fixed 2.17151x safe zoom. It ended
before source frame 1160, after the plane's foreground occlusion.

The refined `airplane_plane-lock-refined.mp4` made three requested changes:

1. Removed a visible vertical excursion around 14.5 seconds by rejecting a
   false absolute shoreline fit before temporal fusion.
2. Restored every source frame and handed horizontal tracking to the land
   background as the plane disappeared.
3. Moved the shoreline to 30% from the bottom, y = 756 on the 1080-high output.

The new framing helped the crop: the minimum zoom was 2.1172869x, and the 1.02
safety multiplier gave 2.1596326x. Restoring the ending therefore required no
extra zoom compared with the first result. The limiting border was near the
beginning, not in the restored tail.

### The dip and why aggregate QA missed its importance

The absolute shoreline detector picked an unrelated foreground edge in frames
924 through 943, about 15.41–15.72 seconds. Its detected y jumped by roughly
184 analysis pixels, and its confidence remained high. The 45-frame Gaussian
fusion propagated that bad evidence backward in time, producing the visible
disturbance around 14.5 seconds and a later recovery.

The repair is a general-form local gate with values tuned here: a 61-frame
median of absolute shoreline y, with samples more than 40 analysis pixels away
given zero absolute weight. It happens to identify exactly frames 924–943.
The strong KLT path bridges them. This is not a frame-index edit or a reason
to discard those picture frames.

Decoded land-motion measurements across frames 830–890 changed from an
accumulated -40.25 preview pixels before the repair to about +0.09 afterward.
These are accumulated signed displacements, not the same statistic as per-pair
RMS. The first result's small-looking per-pair RMS concealed a long coherent
excursion. Cumulative vertical displacement was added to the QA report.

### Disappearance and complete ending

The plane is fully visible through frame 1127. Frames 1128–1159 use a 32-frame
(about 0.53-second) raised-cosine reduction of its relative velocity while a
virtual reference follows land registration. Frames 1160–1222 follow land
motion alone for x. Roll and vertical stabilization remain active throughout.

The final recording contains substantial downward camera motion after the
aircraft disappears. A dissolve into the raw picture would reintroduce that
motion and mismatch the crop. A handoff between motion references kept the
ending continuous with the stabilized shot.

### Refined rendered-preview measurements

These were measured on the decoded 960 x 540 SDR analysis preview, not directly
on the 1920 x 1080 HLG master. Every one of the 1222 adjacent-frame land
registrations passed the QA quality gate. The median registration used 900
inliers.

| Quantity | Observed value |
| --- | ---: |
| Plane x error RMS, visible interval | 1.019 preview px |
| Plane x error p95 absolute | 1.316 preview px |
| Land roll RMS per frame | 0.00539 degrees/frame |
| Land vertical displacement RMS | 0.07820 preview px/frame |
| Land vertical displacement p95 absolute | 0.12519 preview px/frame |
| Cumulative land vertical displacement peak-to-peak | 10.94365 preview px |
| Shoreline geometry coverage | 1223 of 1223 frames within the configured gate |

The cumulative drift value is deliberately included: a low per-frame residual
does not mean zero long-term motion. Coastline shape, intentional scene panning,
and detector conventions also affect measurements. The horizon guide and
appearance were visually checked at the repaired interval and across the tail.

The final master contains all 1223 picture frames, with HLG BT.2020 Main 10
video and the ordinary AAC stereo track. A complete video/audio decode passed.
Dolby Vision configuration and RPU data were not retained through the geometric
render, so the delivered master is plain HLG. The 360p/480p inspection copies
are tone-mapped SDR and are not interchangeable with original source media for
numerical reproduction.

## What remains deliberately specific

The source scripts are useful starting points precisely because the measurement
and rendering choices are visible. This release does not add a general subject
selector, a universal horizon detector, an automatic occlusion detector, arbitrary
color/timing preservation, editor integration, or a new configuration layer.
Those are possible future projects. For a new clip, have the coding assistant
inspect representative frames, identify observable constraints, then adapt the
appropriate branch with new diagnostics and an honest visual comparison.
