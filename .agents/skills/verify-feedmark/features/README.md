# Native feature map

| Path | Proof | Status |
| --- | --- | --- |
| [Local feed reading](local-reading.md) | Actual local RSS fetch, feed selection, article rendering and native screenshots | Proven on hosted run39508929461; screenshot inspected and cleanup confirmed |
| [Original reading notebook](notebook.md) | Native capture/save, disk record and exact restart retention; resolver/repair/error tests | Save/relaunch and exact archive retention proven39520091249; native PNGs inspected; content-change/repair/removal pending |

Use `python tools/verify-feedmark.py <unique-run-name>` only on the dedicated macOS hosted runner. Do not mark cloned upstream features as original work. Update this map when behavior changes; use PStack's maintain-verification-skill workflow for broader driver drift.

Current full-flow attempt 41554682942 passed the producer's lint, 13 driver tests, 23 Swift tests, warning gate and artifact/cleanup checks, then failed installation on the fresh SDK-matched iOS 26.2 simulator at the unchanged 60-second limit. No native feature phase ran. Installation-timeout diagnostics now retain service logs and a diagnostic-only app-identity lookup; their hosted execution and all eight native phases remain pending. A later installed app never converts the timeout into acceptance.

The diagnostic run 41558584026 stopped earlier: lint and 15 driver tests passed, but `AccountStatsView.body` exceeded the unchanged 800 ms compiler gate at 833 ms. Explicit existing closure element types are the next compiler candidate; no view composition or feature acceptance changed. Full native proof and the hosted diagnostic branch remain pending.

Run 41561395578 then stopped in `manualRefresh` at 1,257 ms against 800 ms. Function/expression CPU timing output and the exact compiler version are now retained for a build-only investigation. These timings do not prove native behavior or a compiler performance improvement. All native paths remain required before restoring the project to the résumé.

Run 41564626328 reports Apple Swift 6.2.4 and a 1,116.29 ms enclosing function, with individually small expression timers. The next diagnostic uses built-in request/entity profiling retained in `compiler-evidence/` to investigate the broader scope. No source cause, native acceptance or measured improvement is inferred from that gap.

Run 41569061036 timed out during simulator boot and cleanup before the compiler ran. The next driver compiles the same test plan before creating the owned simulator, then requires that simulator and all 23 current tests before staging. This sequence awaits hosted proof; all native feature paths remain required.

Run 41573526173 reached compilation before simulator setup but crashed in the compiler's entity-profile printer while emitting the Zip module. All 23 driver checks passed; no Swift tests or native phases ran. The added entity-profile flag is removed, with aggregate request statistics and all original acceptance checks retained. Partial profiles and the failure remain preserved; the revised hosted build is still required.
