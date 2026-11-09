# Dependencies and acknowledgments

The scripts use these external projects, installed separately:

| Project | Role | Project/license information |
| --- | --- | --- |
| Python | Runtime and standard library | https://www.python.org/psf/license/ |
| NumPy | Arrays and numerical operations | https://numpy.org/doc/stable/license.html |
| OpenCV (`opencv-python-headless`) | Detection, tracking, transforms | https://github.com/opencv/opencv/blob/4.x/LICENSE ; https://github.com/opencv/opencv-python/blob/master/LICENSE.txt |
| Pillow | Diagnostic images and plots | https://pillow.readthedocs.io/en/stable/about.html#license |
| SciPy | Filtering and signal processing | https://github.com/scipy/scipy/blob/main/LICENSE.txt |
| FFmpeg / FFprobe | Media probing, decoding, encoding, muxing | https://ffmpeg.org/legal.html |

Their licenses and any bundled third-party components remain their own. The repository does not bundle Python packages, FFmpeg executables, or codecs. In particular, an FFmpeg build's licensing depends on its enabled components; obtain it from an appropriate distributor rather than treating this repository's MIT license as a license for those binaries.

Generic Windows font paths in the feedback plotting script are optional system-font fallbacks; no font files are bundled. No trained models or external detection services are used.

The project was developed through iterative collaboration with AI coding assistants. The release documentation records the specific assumptions and observed results so another person can inspect and adapt the work.
