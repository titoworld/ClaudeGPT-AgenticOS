# Architecture decision records (ADRs)

Every important decision is documented in a numbered file, `NNNN-short-title.md`. That way any session (a human's or Claude's) knows what was decided, why, and which alternatives were discarded, without reopening the debate.

An accepted ADR is not edited: if the decision changes, a new ADR is written to supersede it, and the old one moves to the status "Superseded by NNNN". Translating an ADR is not changing it: [ADR 0011](0011-internationalization.md) put them all into English, with every decision as it was.

## Index

| No. | Decision | Status |
| --- | --- | --- |
| 0001 | [Python with uv as the project's base](0001-python-with-uv.md) | Accepted |
| 0002 | [Subscriptions through the official CLIs, with API keys as an alternative](0002-subscriptions-via-official-clis.md) | Accepted |
| 0003 | [The Council mode and the token-saving strategy](0003-council-and-token-savings.md) | Accepted |
| 0004 | [Web interface and deployment](0004-web-and-deployment.md) | Accepted |
| 0005 | [Answer integrity: truncated answers, refusals and the output budget](0005-answer-integrity.md) | Proposed |
| 0006 | [Settings revisions and default prices in the price table](0006-settings-revisions.md) | Proposed |
| 0007 | [The turn outcome, stored on the question](0007-turn-outcome.md) | Proposed |
| 0008 | [Token accounting and declined attempts](0008-token-accounting.md) | Proposed |
| 0009 | [Attachments: images, PDFs and text files in the chat](0009-attachments.md) | Proposed |
| 0010 | [The Refine mode: a document that both AIs improve until you stop it](0010-refine-mode.md) | Proposed |
| 0011 | [Internationalization: the repository in English, the interface in English, Spanish and Catalan](0011-internationalization.md) | Proposed |

## Template

```markdown
# NNNN. Title

- Status: Proposed | Accepted | Superseded by NNNN
- Date: YYYY-MM-DD

## Context

What problem needs solving, and what constrains the decision.

## Decision

What is decided.

## Alternatives considered

What other options there were, and why they were discarded.

## Consequences

What the decision implies, both the good and the bad.
```
