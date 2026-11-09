# Provenance

`code-snapshot.json` identifies the three unchanged working Python files. Their original line endings are preserved, and `.gitattributes` disables Git text conversion for those paths. The original NumPy/OpenCV/Pillow/SciPy minimum requirements were also retained.

`original-media.json` identifies the camera inputs and selected full-resolution outputs. Those hashes identify the historical files; a rerender with different library or encoder versions need not produce the same output hash. The files are not included and this release does not promise a public download for them.

The bundled proxies have their own inventory and checksums in [examples/media-manifest.json](../examples/media-manifest.json). They are copies of the existing compact previews. Their metadata was inspected during packaging; no private user path or GPS/location tag was found in their format/stream tags. They are different files from the originals and masters.

Historical analysis JSON/CSV caches are not shipped. Some contain private absolute paths, and a path moved between folders can invalidate a cache's identity checks. The case study measurements were transcribed from the corresponding historical QA reports with their units and limitations stated. New runs should generate new measurements.

[The archived READMEs](../docs/original/) retain the context of the final working folders. Their default paths are historical: use [current reproduction instructions](../docs/REPRODUCING.md) in this layout.
