# 0003. The Council mode and the token-saving strategy

- Status: Accepted
- Date: 2026-09-27

## Context

The goal is to get better answers by having Claude and ChatGPT review each other, without multiplying consumption. Typical multi-agent systems resend the whole transcript to every agent and run fixed rounds, which makes the token count soar.

## Decision

- Three turn modes: **Solo**, **Duel** and **Council** (answers in parallel → up to N review rounds → synthesis).
- Reviews are self-contained: the question + the agent's own answer + the other's answer. They do not get the conversation's history.
- A strict review format (`<critique>`, `<answer>`, `<agreement>`), with `UNCHANGED` when there is no need to rewrite, and an early stop when both reach the agreement threshold.
- The conversation's history only holds the question and the final answer of each turn; when it goes over a threshold, it is compacted with the fast model.
- A cache of whole turns for identical questions in the same context.
- Stable system prompts (without dates or identifiers) so that the providers' caches work.
- Every saving is measured and shown on the dashboard: cache, compaction, stop on consensus and unchanged answers.

## Alternatives considered

- **Fixed rounds with the whole transcript:** simpler, but much more expensive.
- **Voting with more than two agents:** out of scope; two models with a synthesis already capture most of the benefit.
- **Vector memory (RAG):** unnecessary for a single user and conversations of this size; it can be added later.

## Consequences

- The savings are estimates (tokens ≈ characters / 4), except the ones the providers report (`cache_read_tokens`).
- The quality depends on the models following the review format; the parser is lenient and, if tags are missing, treats the text as the answer.
