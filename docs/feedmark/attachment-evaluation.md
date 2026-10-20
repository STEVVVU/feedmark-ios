# Attachment evaluation protocol

Status: the controlled corpus is implemented in `NotebookTests.editCorpusReportsAttachmentTradeoffs`; hosted execution is pending. No observed result is claimed here yet.

The eleven hand-authored edits compare three deterministic policies on the same input:

- **Offset:** retain the original UTF-16 range if it is still a canonical in-bounds range.
- **First quote:** attach to the first exact literal quote occurrence.
- **Conservative:** run the product's snapshot/quote/context resolver, including its ambiguous/missing outcomes.

Each fixture declares its intended target range independently of the resolver. A nil target means the passage was deleted or the fixture deliberately supplies indistinguishable copies without enough identity evidence to justify either. Choosing a copy in that case is counted as unsupported, not proof it was semantically the wrong copy. The recorded categories are `correct`, `incorrectOrUnsupported` and `unresolved`. An unresolved result includes both an appropriate refusal and failure to recover a surviving intended target; the table below makes that distinction inspectable.

| Edit | Intended target | Purpose |
| --- | --- | --- |
| Unchanged text | Original whole passage | Exact snapshot position |
| Unrelated prepend | Shifted whole passage | Position drift |
| Deletion, longer remaining document | None | An in-bounds position is insufficient |
| Two indistinguishable copies | None uniquely justified | Refuse arbitrary duplicate choice |
| Earlier decoy with different context | Later original context | Context can disambiguate |
| Retained short quote, changed context | Surviving quote | Exposes deliberate lost recovery |
| Unicode passage with prepend | Shifted whole passage | UTF-16/grapheme content |
| Same-length edit inside quote | Edited whole passage | Offset can succeed where literal matching abstains |
| Deletion, short remaining document | None | Out-of-bounds position |
| 501 indistinguishable copies | None uniquely justified | Search bound/ambiguity |
| Added whitespace inside quote | Edited whole passage | Literal matching loses recovery |

The test logs every fixture's declared and conservative target, emits `FEEDMARK_CORPUS` JSON totals, and checks the declared expected tally. Those assertions are expectations until the actual native SDK run passes. They do not constitute an independent dataset or a general recovery-rate estimate. Fixtures were selected to explain mechanisms and tradeoffs, not sampled from real articles. In particular, a low incorrect/unsupported count obtained by refusing attachments must be read with unresolved cases. There is no user study, timing benchmark, semantic-identity guarantee or claim of superiority across real feeds.

Research motivation: [Phelps and Wilensky, Robust Intra-document Locations, WWW2000](https://www.ra.ethz.ch/cdstore/www9/312/312.html). The product implements bounded quote/context descriptors and explicit uncertainty, not the paper's complete UID/tree-walk/Multivalent machinery.
