# 0011. Internationalization: the repository in English, the interface in English, Spanish and Catalan

- Status: Proposed
- Date: 2026-10-02

## Context

The owner asked for «all of GitHub in English, the screenshots too, to reach more people», and for an interface in three languages: English, Spanish and Catalan.

Until now the documentation, the interface and every message of the server were in Catalan (CLAUDE.md; ADR 0009: «every message, in Catalan»). Code, identifiers, comments and commit messages were already in English. The prompts are in English too, and they ask the models to answer in the language of the user's message.

## Decision

### The repository: English

- The README, CLAUDE.md, `docs/` and the ADRs are in English, and so are their file names (`docs/DEPLOYMENT.md`, `docs/ARCHITECTURE.md`, the ADR slugs). The Catalan names are gone: old commits keep their own links.
- The README's screenshots show the English interface with English example data (the showcase, `web/src/showcase/`).

### The interface: English, Spanish and Catalan

- Every text lives in the catalogs, one module per area (`web/src/lib/i18n/areas/`), with the same keys in the three languages. English is the source, and TypeScript checks that the other two have the same keys and parameters. A text with parameters or a plural is a function. Bold and code inside a text are written `**…**` and `` `…` `` and drawn by `RichText`: no HTML is ever parsed, and there is no `{@html}`.
- The language is the owner's choice (a picker on the login screen and in the settings, kept in this browser), else the browser's first language among the three (`navigator.languages`), else English. `<html lang>` follows it.
- Numbers, amounts, dates and relative times use `Intl` with `en-GB`, `es-ES` or `ca-ES`. British English gives day-month dates and a 24-hour clock.
- The modes are Solo, Duel, Council and Refine in English; Solo, Duelo, Consejo and Perfecciona in Spanish; Solo, Duel, Consell and Perfecciona in Catalan. The protocol keeps its identifiers (`solo`, `duel`, `debate`, `refine`).
- The three languages ship together in the first chunk. This avoids an extra request, or a flash of another language, before the first paint. If their weight ever matters, the app can load only the active language.

### The server's texts: in the client's language

- Every text the server writes for people has a key and three translations in `src/agentic_os/locales/`: errors, reasons, the agents' status, model descriptions and the command line. `i18n.t(key, **params)` gives it in the language in force:
  - **an HTTP request:** its `Accept-Language`, which the web app sets to its own language on every request;
  - **a WebSocket connection:** its `?lang=`, since a browser cannot set headers on a WebSocket. The turns a connection starts inherit its language, and so does what they write and store (reasons, failures). When the owner changes language, the app reconnects and fetches the agents' status and the models again;
  - **the command line:** the system locale (`LC_ALL`, `LC_MESSAGES`, `LANG`);
  - **without any of these:** English.
- A stored text keeps the language it was written in. Where the client must know what a text says, not only show it, the wire also carries a code:
  - `reason_code` for refine rounds and versions;
  - `attachment_id` for the error about an attachment that no longer exists.

  For turns stored before this change, which have no code, the client recognizes the Catalan texts.
- No cache shared by every client keeps a translated text. It keeps the key and its parameters, and the text is made for each client (the agents' status, the models' descriptions).
- The demo answers (the fake provider) are written in the turn's language.
- The prompts stay in English and keep asking for the language of the user's message. The interface's language does not decide the language of the answers.
- Everything else the server writes for the models is in English too, in every language of the interface: the labels of the attachments and the reference to them in later turns (`[Attachments: report.pdf (PDF, 12 pages), photo.jpg (image)]`), the lines that enclose a file's text (`[File: …]`, `[End of file …]`), ChatGPT's view of a PDF and the page headers of a PDF's extracted text (`--- Page N ---`). A PDF's text extracted before this change keeps its Catalan page headers: it is the file's stored text, and the models read it as it is.

## Alternatives considered

- **A code on the wire for every message, translated by the client.** Stored texts would then follow the language of the moment. But every error, reason and status of the protocol would change shape, and the command line and the logs would still need the translations on the server. Codes are kept only where the client has logic.
- **A library** (svelte-i18n, Paraglide, gettext with Babel). It is more machinery than three languages need. Plain typed modules check the keys and parameters at compile time, without a dependency.
- **Catalan documentation with an English translation.** Two versions drift apart, and the owner asked for English.
- **Loading only the active language.** The first chunk would be smaller, at the cost of an extra request, or a flash of another language, before the first paint.

## Consequences

- Every new text needs its three translations. Otherwise TypeScript fails for the web, and `tests/test_i18n.py` fails for the server.
- The existing tests keep checking the Catalan texts they were written for. Their setups make Catalan the default language (`web/src/lib/i18n/test-setup.ts`, `tests/conftest.py`), and the i18n tests check the other languages.
- CLAUDE.md: the documentation is in English, and the interface's texts live in the catalogs in all three languages.
- ADR 0009's «every message, in Catalan» becomes «in the client's language».
