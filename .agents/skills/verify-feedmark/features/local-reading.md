# Local feed reading

## Sub-features

First-launch local account, default OPML import, normal RSS refresh, feed timeline and rendered article. No sync-service login is needed.

## How to get to it (user POV)

Launch the isolated test build, select `Feedmark verification feed`, then select `Evidence survives the network`.

## Driving it with Maestro

The maintained Python driver starts the synthetic RSS server, builds the app with that fixture and executes its generated `baseline.yaml` on the owned simulator UDID. It captures the action trace, three native screenshots and an actual `/feed.xml` request. The article must show `Local synthetic article for native reader verification.`

## Gotchas

The fixture replaces only build-time OPML input and is restored afterward. It does not prove production remote feeds, accounts, offline restart, annotations or editing. Unit tests may launch the app first; the native reader proof must still pass through its normal account/feed path. Notification and other app permissions remain denied. A missing simulator, failed flow or unavailable screenshot is a failure or draft, never inferred acceptance.

The refreshed-summary path also requires a second `/feed.xml` request after a controlled content prepend and visible `New introduction` text in the retained timeline evidence. Run39530261297 fetched both fixture versions but retained the old summary and failed that assertion. A body-aware summary-cache fix and focused iOS tests are now present; hosted native proof remains pending.
