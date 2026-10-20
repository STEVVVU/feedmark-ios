# Native feature map

| Path | Proof | Status |
| --- | --- | --- |
| [Local feed reading](local-reading.md) | Actual local RSS fetch, feed selection, article rendering and native screenshots | Proven on hosted run39508929461; screenshot inspected and cleanup confirmed |
| [Original reading notebook](notebook.md) | Native capture/save, disk record and exact restart retention; resolver/repair/error tests | Implemented, pending hosted feature acceptance |

Use `python tools/verify-feedmark.py <unique-run-name>` only on the dedicated macOS hosted runner. Do not mark cloned upstream features as original work. Update this map when behavior changes; use PStack's maintain-verification-skill workflow for broader driver drift.
