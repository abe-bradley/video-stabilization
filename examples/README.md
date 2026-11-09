# Included viewing examples

The repository and release ZIP include all seven files below, approximately 11.82 MB combined. They are 640x360 H.264 Baseline inspection copies. The airplane clips include AAC audio; the sunrise clips are silent.

| File | Duration | Why it is included |
| --- | ---: | --- |
| [sunrise_source.mp4](media/sunrise_source.mp4) | 21.933 s | Original camera composition and boat motion, downscaled. |
| [sunrise_sun-only.mp4](media/sunrise_sun-only.mp4) | 21.667 s | First sunrise approach; early glow/reflection anchoring. |
| [sunrise_sun-land.mp4](media/sunrise_sun-land.mp4) | 21.667 s | Island velocity improves pre-sun horizontal stability. |
| [sunrise_sun-land-2pass.mp4](media/sunrise_sun-land-2pass.mp4) | 21.667 s | Literal one-extra-pass feedback experiment. |
| [airplane_source.mp4](media/airplane_source.mp4) | 20.393 s | Original boat/camera movement, converted to SDR/30 fps. |
| [airplane_plane-lock.mp4](media/airplane_plane-lock.mp4) | 19.326 s | First aircraft result; the dip and shorter ending motivate the refinement. |
| [airplane_plane-lock-refined.mp4](media/airplane_plane-lock-refined.mp4) | 20.400 s | Final result: dip repaired, ending restored, shoreline 30% from bottom. |

The small duration differences in proxies come from encoding and 30 fps conversion. They are not frame-identical to the full-resolution inputs or masters. The first airplane proxy retains the historical shorter inspection encode; the refined proxy covers the complete final shot.

**Files called `source.mp4` here are previews, not original camera files.** The released airplane renderer assumes the original 1920x1080 HLG/BT.2020 footage at about 60 fps; all these airplane copies are 640x360 SDR/30 fps. The original settings should not be run blindly on them. They can be used as an adaptation exercise after reviewing the resolution, timing, and color assumptions.

Download the repository ZIP or clone it and open the videos in a media player if GitHub shows a download link rather than an inline player. No account, external media download, or API key is required to watch the local examples.

[media-manifest.json](media-manifest.json) records byte sizes, hashes, and basic probe results. [MEDIA_LICENSE.md](../MEDIA_LICENSE.md) describes viewing, experimentation, and repository-redistribution permissions. The code's MIT license does not license these clips as stock footage.
