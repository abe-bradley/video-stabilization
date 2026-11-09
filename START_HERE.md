# Start here: hand this repository to your AI coding tool

Copy the prompt below and replace the bracketed fields. The source video path is the only essential field to fill immediately; the assistant can help choose the rest.

```text
I want to adapt this repository to a particular video, not turn it into a general application.

My source video: [path or attached file]
The subject/scene: [what is happening]
The motion I want held fixed: [e.g. shoreline angle and height; subject x]
The motion I want preserved: [e.g. descent, sunrise, deliberate camera pan]
My preferred composition: [e.g. subject at average raw x, shoreline 30% up from bottom]
Crop/zoom tolerance: [preference, or help me decide]

Read AGENTS.md, docs/ADAPTING.md, and the relevant sections of docs/ALGORITHMS.md.
Use presets/ as descriptive reference; the scripts do not parse those files.

First inspect my file's metadata and representative frames. Explain which of the
two examples is the better starting point and which hardcoded assumptions need
changing. Use the original as a reference and work on an adaptation copy. Do not
run either script's default all command blindly.

Identify the actual scene boundary and subject cues. Adapt the seed box, search
regions, color handling, time windows, and output paths to this recording. Prefer
small edits that serve my shot. Preserve real subject motion along unconstrained
axes. Use confidence/outlier checks and a stable background when the subject is
hidden. Keep all intermediate tracks and transforms interpretable.

Analyze first, then show me a lightweight diagnostic preview, crop/zoom cost, and
tracking confidence. Inspect full-speed motion, handoff boundaries, and cumulative
drift as well as adjacent-frame jitter. Only then render the full-resolution
result, preferably by composing transforms and resampling the original once.

Record the final settings, known limitations, and exact command so I can revisit
this adaptation. Do not promise arbitrary-footage support or build an editor UI
unless I ask for it.
```

For tool-specific instruction files, point them to `AGENTS.md` rather than maintaining several competing copies. This prompt also works when the tool has no automatic instruction-file support.

Want to understand the original results? Read [CASE_STUDIES.md](docs/CASE_STUDIES.md), then watch [the bundled previews](examples/README.md). To rerun the original recordings, use [REPRODUCING.md](docs/REPRODUCING.md); the full-resolution camera files are not bundled.
