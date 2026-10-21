---
name: verify-feedmark
description: Build and drive the real iOS reader on an isolated hosted simulator with controlled RSS input and retained native evidence.
---

# Verify Feedmark

Baseline proven on hosted run [39508929461](https://github.com/STEVVVU/feedmark-ios/actions/runs/39508929461), source `d5f728b92`. The unsigned app built, five existing iOS tests passed, the warning gate passed, and the real native reading flow passed. The downloaded article screenshot was inspected; RSS requests and all four cleanup flags were checked after shutdown. No local Xcode or iOS simulator is installed on the Windows workspace. Newly added annotation paths require their own proof.

## Launch

Use the `Feedmark native checks` workflow on branch `feedmark`. Its maintained driver is `python tools/verify-feedmark.py <unique-run-name>`. It refuses execution outside a dedicated macOS GitHub runner. Xcode 26.3, the iPhone 17 device type and an available iOS runtime must already exist. It creates an owned simulator, never resets a user's simulator, accepts no Xcode terms and uses the upstream unsigned simulator configuration. No developer account or provisioning update is used.

The driver builds and runs the upstream iOS test plan. For this isolated build only, it replaces the bundled default feed list with a loopback synthetic RSS source and restores the original bytes during cleanup. This is verification input, not a replacement product feed list. Service API-key environment variables are removed before generating the upstream Secrets source. The test fixture uses the normal local-feed account and reader path.

## Doctor

Capture actual Xcode/Java versions, installed simulator inventory, the created simulator UDID/runtime and build/test logs. Read the bundle identifier from the built app. Do not infer a working simulator from a workflow dispatch. A missing runtime, unaccepted setup, compilation error or native driver failure keeps onboarding a draft.

## Drive

Maestro CLI 2.11.0 is a test-only tool, downloaded from its official GitHub release and verified against SHA256 `5384593cb4e7a106489e75a821d157dd43f4e438df6bc308b72e82c685e1283a`. It is Apache-2.0 licensed. Run only against the created simulator. Analytics and update checks are disabled; no cloud or AI command is invoked. All requested app permissions are denied explicitly.

The generated baseline flow selects `Feedmark verification feed`, opens `Evidence survives the network` and verifies the synthetic article text. The driver also verifies that the actual app fetched `/feed.xml`. The original baseline retained an explicit article PNG; subsequent runs set Maestro's actual `--test-output-dir` so the requested sidebar/timeline/notebook PNGs are also uploaded. See [the feature map](features/README.md). Existing iOS unit tests are additional evidence, not a substitute for this native path.

The notebook flow uses the real article toolbar, captures its whole text, saves a user-entered note and checks its actual sandbox JSON. A separate terminate/relaunch flow checks the visible note and exact retained bytes. Save/relaunch passed on39520091249 with inspected native PNGs, exact unchanged archive bytes and all cleanup flags true. The expanded mutation flow failed before a new RSS fetch; content-change/repair/removal acceptance is still pending. The one-article timeline regression remains in the driver. Subsequent flows reuse the same pinned driver installation on the same owned simulator via the actual `test --no-reinstall-driver` option; the baseline still installs it normally.

## Evidence

Keep `.audit/verification/<run>/result.json`, HTTP request records, native PNGs, Maestro artifacts/JUnit, notebook JSON before/after restart and Xcode result bundle/logs. The hosted workflow uploads them for inspection. Download and view actual screenshots before accepting native rendering. The older baseline does not verify the notebook. Run39520091249 provides actual save/relaunch proof but does not clear its failed mutation step. No collaboration feature is implemented.

## Cleanup

Restore the original OPML bytes, shut down only the created simulator UDID and close the owned loopback RSS server. Retain the simulator profile, all evidence and logs until the hosted job ends. The driver does not delete user profiles or stop unrelated simulators. Confirm shutdown, fixture restoration and evidence retention in `result.json` after failures as well as success.

## Sources and limits

Upstream is Ranchero-Software/NetNewsWire at `94032d95fe04a05d44591450234c3c5da0a40842`, with original MIT notices and complete history retained. The Windows working tree sparsely excludes only `/AppStore/` marketing assets because an upstream filename contains a quote; hosted macOS checkout is complete. Existing build warnings remain enforced by `buildscripts/fail_on_warnings.sh`. The app dependency audit, physical-device behavior, macOS app and original feature acceptance remain outside this onboarding proof.

Maestro primary references: [release](https://github.com/mobile-dev-inc/Maestro/releases/tag/cli-2.11.0), [source license](https://github.com/mobile-dev-inc/Maestro/blob/cli-2.11.0/LICENSE), [CLI](https://docs.maestro.dev/maestro-cli), [environment variables](https://docs.maestro.dev/maestro-cli/environment-variables).

## Current save-tap diagnosis

Run [39523077552](https://github.com/STEVVVU/feedmark-ios/actions/runs/39523077552) passed lint, 20 tests and the native reader, then failed its Save feedback assertion before refresh. The inspected PNG shows the filled form and unchanged capture feedback. Maestro reported a tap, but the UI does not show the save action taking effect. The next probe waits for screen animations after keyboard dismissal, retains before/after Save PNGs and copies any notebook archive on failure. The tap-position/timing cause remains a hypothesis. The previous save/relaunch proof remains scoped to run39520091249. No mutation acceptance is inferred.

Run39524522587 passed save/relaunch and exact archive retention with the animation wait; all cleanup flags passed. Its pull gesture still made no new RSS request despite vertical bounce. Native hierarchy places the only article at y116..203 on the observed402x874 screen; the previous gesture began at30percent height in blank space below it. The next probe uses the pinned Maestro2.11.0 supported element-origin swipe from the article title, retains before/after PNGs and collects the app's unified logs on failure. This tests gesture targeting and does not yet establish the refresh cause. RSS fetch, changed marker, attachment status and unchanged-original archive assertions remain mandatory.

Run39527997973 again passed save/relaunch with exact archive retention and all cleanup flags. The element-origin swipe reached `LocalAccountRefresher`, whose retained log explicitly skipped a six-minute-old check. Source policy uses a nine-minute default when `CacheControlInfo` is absent; the parser accepts only positive max-age values, so the previous `no-store` fixture did not set this metadata. The controlled server now supplies `Cache-Control: max-age=1`, recorded alongside each request. The real app must fetch each source version and render the changed content. No app-state injection, clock change, timing bypass, extra gesture retry or weakened archive assertion is used. The speculative `alwaysBounceVertical` product edit was removed. This fixture correction awaits hosted proof.
