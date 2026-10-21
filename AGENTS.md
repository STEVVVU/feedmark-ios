# Feedmark development

Follow `CLAUDE.md` for upstream Swift conventions and architecture. Preserve the full NetNewsWire history, MIT notices and existing native reader behavior.

The Windows checkout has no Xcode. Use `.agents/skills/verify-feedmark/SKILL.md` and `tools/verify-feedmark.py` through the `Feedmark checks` workflow on branch `feedmark`. The dedicated macOS runner uses Xcode26.3, an owned simulator, the existing unsigned iOS configuration, the existing iOS test plan and native Maestro flows. Do not accept platform terms, configure developer credentials or modify unrelated simulators to make a run pass.

Keep feature acceptance in the verification skill's feature map. A compile, unit test or cloned upstream feature does not establish original native workflow evidence. Inspect actual retained screenshots and side effects. Generated run data belongs in ignored `.audit/verification/`; maintained drivers belong in `tools/`.

The iOS app uses filesystem-synchronized sources. The iOS test target has explicit source membership in `NetNewsWire.xcodeproj/project.pbxproj`; update it when adding native tests. Existing Swift compiler and warning gates remain enforced.

The owned workflow also runs the repository's existing SwiftLint rules against changed native sources using a checksum-pinned official portable release. The Windows SwiftLint binary was tried and could not load sourcekitdInProc.dll; that local attempt is not a lint pass. Use the actual hosted SourceKit/Xcode environment rather than disabling checks.

Push CI builds and runs the iOS test plan with `--build-only`. The user prioritized code delivery and waived connecting devices or browsers as a delivery gate. Full native UI acceptance is an explicit workflow-dispatch option, `drive_native=true`; a build-only result never establishes native UI acceptance. Preserve existing UI assertions when that optional path runs.
