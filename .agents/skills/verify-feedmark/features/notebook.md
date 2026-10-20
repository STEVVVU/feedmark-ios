# Reading notebook

## Sub-features

Capture a selected passage or a whole article up to4,000 text units, write a note, reopen it, inspect attachment status against a new snapshot, explicitly repair an attachment, or confirm local removal. An unresolved quote is retained. Notes and their original text live in an atomic JSON archive within the app sandbox; this adds no sync account or external recipient.

## How to get to it (user POV)

Open a normal article, tap the `Reading notebook` toolbar icon, select and capture text (or tap `Capture whole article`), write why it matters and tap `Save note`. Scroll to saved notes. Refresh/reopen an article to evaluate attachments against its newly rendered body. Use `Repair note using selected passage` to explicitly replace an attachment while retaining the original quotation and prior descriptors.

## Driving it with Maestro

The maintained hosted driver builds/runs the iOS test plan, drives normal reading, then `notebook.yaml` captures the synthetic article using the visible whole-article control and saves `Source context matters`. It reads the actual app-container `Library/Application Support/FeedmarkNotebook/notes.json`, verifies one real note and its original quote, terminates/relaunches the app, reopens the notebook and compares exact disk bytes. `notebook-reopen.yaml` captures visible retention. This feature path remains pending until that run and screenshot inspection pass.

## Gotchas

The source snapshot is frozen while a sheet is open. Reopen after refreshing to use new text. The pure resolver uses exact snapshot offsets, then exact quotation and bounded context; a long whole-document quotation can match uniquely without surrounding context. Multiple supported candidates or more than500 inspected matches remain ambiguous. Changed context can intentionally lose a valid attachment. This is a bounded adaptation of robust annotation descriptors, not the entire WWW2000 tree-walk/UID algorithm or proof of semantic identity.

Native partial-selection gestures, refreshed-content relocation/ambiguity/deletion, repair and removal still need rendered/real-product drives. Unit tests cover deterministic mechanisms and persistence failures; they do not close those native paths. No encryption, sync, backup exclusion, cross-process writer or real-user evaluation claim is made. A single main-actor store owns writes in the app; failed/corrupt reads do not reset the archive.
