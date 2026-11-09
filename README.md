# Video stabilization

This is my Python code for stabilizing two videos I took from a boat near Corfu: a sunrise and a plane landing at Kerkyra airport.

The boat was moving quite a bit, but both shots had something useful to work with: a clear shoreline and a subject worth keeping in place. The idea is to hold the shoreline level and at a fixed height, then keep the sun or plane at a fixed horizontal position. The sun still rises, the plane still descends, and the camera motion mostly disappears.

I vibe coded this with Codex, working through the footage and refining the result a little at a time. I'm sharing it so someone else can give it to their own AI tool and have a useful starting point for their footage.

What you'll find:

- **The sunrise code**: tracks the shoreline, uses the island for horizontal motion before the sun is clear, then hands over to the sun itself.
- **The airplane code**: tracks the plane while using the land to correct roll and vertical motion. It keeps the plane in the left half, roughly where I had it on average, and hands tracking back to the background as it disappears.
- **A feedback experiment**: I passed the stabilized sunrise through the algorithm again to see whether the last bit of jitter would shrink. It did! The measurements and their limitations are in the [case studies](docs/CASE_STUDIES.md).

## The results

Small before-and-after videos are included in the repository:

| Shot | Before | After |
| --- | --- | --- |
| Sunrise | [Original](examples/media/sunrise_source.mp4) | [Stabilized](examples/media/sunrise_sun-land-2pass.mp4) |
| Plane landing | [Original](examples/media/airplane_source.mp4) | [Stabilized](examples/media/airplane_plane-lock-refined.mp4) |

There are [earlier versions too](examples/README.md), if you'd like to see how the results developed. These are compressed viewing copies; the scripts' original settings were chosen for the full-resolution camera files.

## Getting started

1. Clone or download this repository and open it in your AI coding tool.
2. Give it [START_HERE.md](START_HERE.md), which has a prompt you can copy and fill in.
3. Point it to your footage and describe what should stay fixed, what should keep moving, and how much cropping you're happy with.

The timings, tracking regions, and color handling are still specific to my recordings, so expect some adaptation. The [adaptation notes](docs/ADAPTING.md) point out those assumptions, and the [algorithm guide](docs/ALGORITHMS.md) explains the pieces. Have your assistant make a small diagnostic preview before committing to a full render.

**Requirements.** Python, NumPy, OpenCV, Pillow, SciPy, and FFmpeg/FFprobe. I developed and ran this on Windows. Install the Python packages in a virtual environment with:

```sh
python -m pip install -r requirements.txt
```

FFmpeg is installed separately. The [reproduction guide](docs/REPRODUCING.md) has the setup details and commands for the original workflows. Heads up: the scripts can overwrite existing outputs, so give each experiment its own folder.

For ongoing work, the [local workflow](docs/LOCAL_WORKFLOW.md) covers keeping the archive separate, building release ZIPs, and exporting them to a mounted drive. Your actual export destination stays in a private settings file outside the repository.

## Coming later

Eventually I'd like to come back to this and make it comfortable to use as an editor: selecting a subject, adjusting the framing, and correcting tracks without digging through Python. For now, the working code and the reasoning behind it are here to build on. There's a little more in the [roadmap](ROADMAP.md).

## License

MIT for the code and documentation. See [LICENSE](LICENSE). The included videos have separate [media terms](MEDIA_LICENSE.md).
