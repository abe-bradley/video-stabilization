# Contributing

Adaptations are welcome. Explain the shot, which measurements you relied on, which motion you held fixed, and what you actually checked. Share a minimal example only when you have permission to share the footage.

For this release, the three original scripts are preserved snapshots. Put experiments in a new directory or clearly named script so the original examples stay readable. A future cleanup can deliberately replace this structure; see `ROADMAP.md`.

For documentation or packaging changes, run:

```text
python tools/check_release.py
```

For an algorithm adaptation, show a diagnostic preview and discuss crop cost, sustained drift, transitions/occlusion, and media behavior. The release-content checks cannot establish those properties.

Do not commit camera originals, full renders, private paths, environments, or generated analysis caches. Example footage has separate terms in `MEDIA_LICENSE.md`. Contributions to code and documentation are offered under the repository's MIT license.
