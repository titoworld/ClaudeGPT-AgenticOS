# 0009. Attachments: images, PDFs and text files in the chat

- Status: Proposed
- Date: 2026-09-29

## Context

On 28 September 2026, the owner asked to be able to attach images and documents in the chat, with thumbnails as on claude.ai. If ChatGPT cannot read a PDF, Claude must interpret it and pass its reading on to ChatGPT, and the text extracted from the PDF must be checked against what Claude reads. The owner asked for the best way to do it.

**What each provider accepts** (pinned versions, checked on 28 September 2026 without any call to the APIs):

- **Claude's CLI 2.1.283** (`stream-json` input): its `message.content` accepts `image` blocks (base64, URL or file; JPEG, PNG, GIF and WebP) and `document` blocks (PDF in base64, text, URL or file). Its schema says: "send images and documents in `message.content`".
- **Codex app-server 0.157.1** (`codex app-server generate-ts`): a `UserInput` can be text, `image` (URL), `localImage` (path), audio, a *skill* or a mention. **It accepts no PDF and no document.**
- **Claude's API:** image and document (PDF) blocks. **OpenAI's API** (Responses): `input_image` and `input_file` (PDF).

**Anthropic's limits** (platform.claude.com/docs, `vision.md` and `pdf-support.md`, read on 28 September 2026):

- JPEG, PNG, GIF and WebP images (of an animation, only the first frame), of at most 8,000 × 8,000 pixels and 10 MB encoded in base64 per image (about 7.5 MB of bytes). With more than 20 images in a request, the per-image limit is stricter.
- The model shrinks the image until its long side is 2,576 pixels (Claude 4.7 and later), and an image costs `ceil(w/28) × ceil(h/28)` tokens, at most 4,784.
- PDF: the whole request up to 32 MB, up to 600 pages (100 when the context window is under 1M tokens), without a password or encryption. Each page is sent as text **and** as an image: about 1,500–3,000 tokens of text per page, plus those of the image.

**OpenAI's limits** (Responses API, the "File inputs" guide on developers.openai.com, 29 September 2026). The page cannot be opened from the environment where this change was made (the outbound proxy blocks it), so only what the search engine shows of it could be read:

- Checked: each file must be under 50 MB, and all the files of a request, 50 MB at most. With a model that sees images, the model gets both the text and the image of each page of a PDF. A turn (20 MB of files, about 27 MB in base64) fits.
- Not checked: a limit of pages per request (sources from 2025 that quoted the guide of the time give 100 pages and 32 MB across all the files) and the limits of the images (`input_image`).
- No check of our own is added as long as these limits cannot be read in the documentation (never from memory). If the API rejects a request, ChatGPT's call fails with the API's message (`OpenAI's API rejected the request: …`), and the owner can send the question again with fewer pages.
- The same goes for Claude: the per-request limits (600 or 100 pages, and the context window) can leave out a combination of long PDFs even if each one meets the app's limits.

**Cost consequence:** a 10-page PDF is tens of thousands of input tokens on each call. If it is sent again at each phase of a debate (answers, revisions and synthesis), the cost multiplies.

## Decision

It is done in two stages: **P7a**, attachments in every mode, and **P7b**, the check of the PDF for ChatGPT on the subscription.

### P7a. Attachments in every mode

**What is accepted.** The type always comes from the content, never from the name nor from the `Content-Type`:

- PNG, JPEG, GIF and WebP images (the signature in the first bytes). The dimensions are read from the headers (PNG IHDR, JPEG SOF, GIF, WebP VP8/VP8L/VP8X) in pure Python: the server never decodes an image.
- PDF (`%PDF-` at the start).
- Text: valid UTF-8 without any NUL, with an allowed extension (`txt md markdown csv tsv json yaml yml xml html htm log ini toml cfg py js ts jsx tsx svelte css scss sql sh bash rs go java kt c h cpp hpp cs rb php swift lua r pl`). It is always treated as plain text, and never shown as HTML.
- Everything else gives `415`: SVG too (it can carry code), HEIC (convert it to JPEG), the Office formats (convert them to PDF), audio and compressed archives.

**Limits** (constants of `src/agentic_os/attachments.py`, documented in [PROTOCOL.md](../PROTOCOL.md)):

- At most 5 attachments per message and 20 MB in total, so the stricter limit for many images never applies.
- Image: 7 MB and 8,000 pixels per side. Before uploading it, the browser shrinks any image over 2,576 pixels on the long side: the models see it the same, and the upload is much smaller. It also re-encodes, at the same size, an image over 7 MB, and upright a photo that is shown rotated by its EXIF orientation (like phone photos): the models get its pixels as they are stored, without the metadata, and would see it rotated.
- PDF: 20 MB and 100 pages, not encrypted.
- Text: 200 kB.
- Too large: `413`. Not valid (empty, too many pages or pixels, an encrypted PDF, an invalid name): `422`. Every message, in the client's language ([ADR 0011](0011-internationalization.md)).

**Upload.** `PUT /api/attachments?name=<name>` with the file as the body, without multipart. Like the other writes, it needs the session and an allowed `Origin`, and it has a time limit for receiving the body. The body is written to a private temporary file as it arrives, and cut off at the limit of its type, which the first bytes decide. It is the only route with a body of up to 20 MB and 120 s (the others stay at 1 MiB and 15 s), and it is only read with a session. Caddy lets the same through, 20 MB, and cuts it off at 150 s, when the app has already answered (`deploy/Caddyfile`; for the other requests, 1 MiB and 30 s). The file name goes in the URL's query string, and no log stores it: the app's access log (uvicorn) and Caddy's logs, the error log included, only keep the path.

**Storage.**

- Content-addressed files at `<data_dir>/attachments/<sha256[:2]>/<sha256>` (directories 0700, files 0600), on the data volume: the backups include them. Two uploads of the same file use a single one.
- The `attachments` and `message_attachments` tables (migration 4). A PDF's row stores its extracted text, and a text file's row, its content.
- An attachment that has not been sent in any turn is deleted after 24 h. Deleting a conversation deletes the attachments that only it used. A file is deleted when no row uses it. Every hour, a sweep removes the files that no row uses (leftovers of a crash).

**The text of PDFs.** pypdf (a new dependency) reads it in a separate process: isolated Python (`-I`), without any of the server's environment variables, with a maximum of 60 s and of 512 MiB of address space, a CPU limit, and unable to write files, create processes or dump its memory. That way a hostile or huge PDF cannot stall or exhaust the server, and the server's process never parses a PDF. Two readers at once can take up to 1 GiB of the app container's 1.5 GiB: that is why each reader is the first process the kernel kills if the container runs out of memory (`oom_score_adj` 1000), and not a CLI in the middle of a turn, or the server; the upload of that PDF gives `422`. The text has one block per page, introduced by the line `--- Page N ---`. It is null if nothing could be extracted (a scanned PDF), and it is cut off at a million characters, with a notice.

**How they are served.** Only images are shown inside the app (`Content-Disposition: inline`); PDFs and text files are downloaded (`attachment`). All of them carry the detected type, `X-Content-Type-Options: nosniff` and a `default-src 'none'; sandbox` policy. Nothing that is uploaded is ever served as a document of the app's origin, except images.

**Thumbnails.** The browser makes them only once, when the file is attached (images with a `canvas`; PDFs, their first page with PDF.js), and uploads them: PNG or WebP of at most 100 kB and 512 pixels per side. That way they show in the composer, in live and reloaded turns, and on other devices.

**PDF.js in the browser** (`pdfjs-dist` 6.3.289, `web/src/lib/pdf.ts`), for the thumbnails and the preview, configured so that the CSP does not change:

- It is loaded separately, only when a PDF is attached or previewed.
- It is the "legacy" build: the other one needs JavaScript features that many current browsers do not have yet (`Map.prototype.getOrInsertComputed`...), and this one brings them along.
- The *worker* is a file of the app, on the same origin (`GlobalWorkerOptions.workerSrc`), never a `blob:` *worker*.
- No WebAssembly (`useWasm: false`), which would need `'wasm-unsafe-eval'`: the decoders for scanned pages (JBIG2, JPEG 2000) work with their JavaScript version.
- Glyphs are drawn as paths (`disableFontFace: true`, `useSystemFonts: false`): no `FontFace`, and a PDF's fonts never reach the browser's font engine. No XFA forms (`enableXfa: false`), and a maximum of pixels per image.
- The standard fonts, the CMaps and the decoders are files of the app, in `/assets/pdfjs-<version>/` (`vite.config.ts` keeps their names, in a folder per version, because the server caches `/assets/` forever): no external URL.
- **A change from what was agreed:** the P7a contract asked for `isEvalSupported: false`, but PDF.js 6 no longer has that option, because it never evaluates code (neither `eval` nor `new Function`). A web test (`web/src/lib/pdf.test.ts`) checks it on the pinned build.

**Delivery to the models** (the engine and the providers):

- The answers and the synthesis get all the attachments whole. The revisions get the images and the text files whole, and the PDFs as `pdf_in_revisions` says: `"text"` (the default), the extracted text instead of the document; `"full"`, the document. A PDF from which no text could be extracted (a scanned one) always goes whole: as text, the revisions would get nothing from it to check the answers against.
- Later turns only see a reference to the attachments in the history: "[Attachments: report.pdf (PDF, 12 pages), photo.jpg (image)]". If needed, the owner attaches the file again.
- Claude's CLI: `image` and `document` blocks in the `stream-json` message, with all the mandatory *flags* and `client_composed: true`. Claude's API: the same blocks, with `cache_control` on the last attachment block. OpenAI's API: `input_image` and `input_file`. Codex: images as `localImage`, with the path of a symbolic link to the stored file, named `<sha256>.<extension>` (in `<data_dir>/codex-images`), because Codex infers the type from the name and the stored file has no extension; PDFs as the text the server extracted, marked "unchecked"; text files as text.
- The attachments go before the question, each with a label (name, type, pages), and the system prompt says that their content must be treated as data, never as instructions.
- The text of a file, and that of a PDF when it is passed as text, is treated like the rest of the text that is not the owner's (the models' answers, the history): it goes through `neutralize_tags`, so it cannot open or close any section of the prompt (`<user_message>`, `<current_message>`...), and it goes between an opening line that ends with a code, `[File: report.txt · 1f0c…]`, and the line `[End of file 1f0c…]`, with the same code. The prompt's list of attachments explains that everything between the two lines is the file's content. On Codex, a PDF opens with `[PDF "report.pdf", 12 pages: text extracted by the server, unchecked · 9b2d…]`, with a code of its own since P7b (point 4 of P7b).
- The code is 16 hexadecimal digits of the SHA-256 of the content's `sha256` and of the name. Neither the file (nor the text extracted from it) nor the name can contain it, because they would have to contain their own hash: a forged end of file has another code. The same file with the same name always has the same code, so the prompts do not change from one phase to the next, and the prompt cache keeps hitting.
- The turn cache key includes the content (`sha256`) and the name of each attachment, in order, and `pdf_in_revisions` when it changes what the models get (a debate with revisions and some PDF with text). P7b adds to it the version of the check and what the reader made of each PDF (see the consequences).
- The engine stores the question and its links to its attachments in a single transaction, before `turn.started`. If an attachment has disappeared in the meantime (deleted from another tab, or by the sweep of an unsent attachment older than 24 h), the turn fails without starting and leaves nothing behind: not even the new conversation it had just created, which nobody knew about yet.

**Estimated tokens**, which each attachment's card shows before it is sent (approximate):

- Image: `ceil(w'/28) · ceil(h'/28)`, at most 4,784, with `(w', h')` the image shrunk to 2,576 pixels on the long side.
- PDF: 3,600 per page.
- Text: `ceil(characters / 4)`.

**Cost policy.**

- Images go to every phase: they cost little, and the models need them to check each other's claims.
- PDFs go whole to the answers and to the synthesis. The revisions get their text, unless the owner chooses "PDF in revisions: full" in the settings drawer (`pdf_in_revisions`) or the PDF has none.
- On the API, the prompt cache (`cache_control` on the attachment blocks) makes the repetitions within a turn cheap.
- Each card shows the estimated tokens before sending.

**Limitation of P7a.** Codex gets the PDF's text as the server extracted it, unchecked and marked as such: from a scanned PDF it gets nothing. P7b, below, solves it.

### P7b. The PDF for ChatGPT on the subscription, checked by Claude

Only when ChatGPT runs on Codex (`cli` mode): it cannot open any PDF, and it reads the text the server extracted from it. Claude (CLI or API) and OpenAI's API read the PDFs themselves, and they do not change. This text can mislead ChatGPT in three ways: a scanned page has none, a font without a character map makes it unreadable, and a PDF can carry text that cannot be seen on the page (in an invisible rendering mode, tiny or off the page), which ChatGPT would read as if it were there, a way to slip instructions in.

**1. Analysis of the pages, when the PDF is uploaded** (free, in every mode). P7a's reader, in the same process and in the same pass as the text, analyses each page (`src/agentic_os/attachments.py`, with the operator visitors of pypdf's text extraction):

- Where the page's text is within the stored text. The pages that the text limit leaves out are analysed too: their data come from their own text, which is then discarded.
- The letters and the broken characters of its text: U+FFFD, private-use characters, lone surrogates and control characters (what a font without a character map gives).
- Whether it draws any image: in its `Resources` or in those of its forms (up to 5 levels deep), or an inline image. An image in the `Resources` counts even if the page does not draw it.
- The text it shows without it being visible: in a rendering mode that paints nothing (3 or 7); smaller than 1 point in some direction, counting the scales it is drawn with (the font size, the horizontal scaling `Tz`, the text matrix, the transformation matrix and that of the form that draws it: a text squashed in a single direction cannot be read either); or with its origin, counting the vertical shift `Ts`, more than 1 point outside the visible part of the page (the `CropBox` within the `MediaBox`). pypdf does not track the rendering mode, the size, the horizontal scaling or the vertical shift, so the analysis saves them on `q` and restores them on `Q`. A form is drawn as if between `q` and `Q`: it starts with the state of whoever draws it, its own state does not leak out, and it cannot restore more states than it has saved (a way to make the page's invisible text pass for visible).

From this come each PDF's warnings (`pdf_notes` in the `Attachment`): the pages without text (fewer than 25 letters), the unreadable ones, and the ones that may hide text. Invisible text is only suspicious on a page without images: over a scan, it is the recognized text, and it is legitimate. They are warnings, not verdicts: a page can hide text in other ways that the analysis does not see (white on white, under an image, or clipped by a clipping path that lets none of it show), and the invisible text of a page that has an image in its `Resources` without drawing it is not suspicious. That is why Claude's check looks at the page as it is seen. If the analysis does not match the text, the PDF is stored anyway, unanalysed, and read as in P7a. No odd operand makes a page lose its text: the analysis skips it.

**2. When it is checked: when a turn needs it, not when the PDF is uploaded.**

- An attachment that the owner removes from the composer, or only sends to Claude, costs nothing.
- ChatGPT's answer waits for the check while Claude answers: the first ChatGPT call of the turn that needs it starts it, and all of ChatGPT's calls in the turn (answers, revisions and synthesis) wait for the same task.
- Only in a turn where ChatGPT takes part (solo with ChatGPT, duel or debate) and there is a Claude that can do it. Never in an answer served from the turn cache.
- The demo Claude (`AOS_CLAUDE_MODE=fake`) does none: it answers that every page is correct without reading any, so with a real ChatGPT it would pass a scanned page off as empty, and hidden text as visible. With it, the PDF is read unchecked, as without Claude, and no check is stored or reused. Version 2 of the check reuses none of the checks version 1 stored, since version 1 also stored the demo Claude's.
- The check is stored by the file's content (`sha256`) and the version of the check (not by the model: one from another Claude model serves too), and later turns and other conversations reuse it. Two turns at the same time can check the same PDF twice: that is accepted, because it is rare and it only costs the extra calls.

**3. How.** Claude gets the PDF as a document **and** the text extracted from each page, between two lines with a code different from the one ChatGPT sees, and the analysis's warnings. It only writes the pages that differ, one JSON line per page: `missing` (no text: it transcribes the whole page), `garbled` (unreadable: likewise), `partial` (only the part that is missing), `hidden` (the visible text and a short quote of what cannot be seen) or `ok` with a `visual` description of what the figures show; at the end, `{"end": true}`. The parsing is strict: a malformed line, a line out of order, or one cut off by the budget ends the reading, and only the pages before it count.

- The model is the turn's Claude model: quality before cost, as the owner chose. Without reasoning, and with an output budget that depends on the pages (more for the ones Claude will have to transcribe whole: those without text, the unreadable ones, and those that may hide text, of which it writes all the visible text).
- At most 3 calls per PDF, each one from the page where the previous one stopped: about 60 scanned pages. The rest are left unchecked, and say so.
- At most 2 PDFs at a time, and 5 minutes for the turn's whole check: after that, ChatGPT reads the text unchecked.
- It is stored when it is complete or when the calls have run out. Never after an error, a refusal, a call that makes no progress, a timeout or a cancellation: the next turn tries again. That is why a turn where ChatGPT read a PDF like that does not enter the turn cache: the same question would repeat the unchecked reading instead of trying again (point 6).

**4. What ChatGPT reads, page by page.** The exact extracted text where it is correct; Claude's reading, marked, where text is missing or cannot be read, and its complement where part of it is missing; the descriptions of the figures, marked. **The text that Claude finds is not visible does not reach it:** from that page, it gets the visible text and the warning that the page has some. **A page nobody has checked reaches it as it was extracted, with any text that cannot be seen that it may have:** without Claude or with the demo one, after an error, a refusal or a timeout, the pages left out of the calls (which are not checked again, because the partial check is stored) and the unanalysed PDFs. Such a page says that it has not been checked and, if the analysis finds it suspicious, that it may have text that cannot be seen, a warning that every model also gets (point 5). The analysis is a heuristic (point 1): a page that does not trigger it reaches ChatGPT without any warning. The view has a code of its own, different from the file's (which Claude sees in the revisions, when it gets the PDF as text) and from the check's: only ChatGPT sees it, so neither the PDF nor Claude can forge any page line or any note in it.

**5. Warnings to every model.** The label of a PDF with suspicious pages tells the models to treat those pages with suspicion, and the revisions and the synthesis know which pages ChatGPT read through Claude, by the text Claude read there or by its description of the figures (even if the extracted text was correct): where the two agree, it is a single reading, not two.

**6. When it cannot be checked** (without any Claude provider, with the demo one, an error, a refusal, a timeout or an unanalysed PDF): ChatGPT gets the extracted text, with the unchecked pages marked, and the turn says why. Such a turn never enters the turn cache when another turn would read the PDF differently: when the next turn would check it again (an error, a refusal, a call that makes no progress, a timeout) or when there was no Claude that could check it (none, or the demo one), because one configured later would. An unanalysed PDF is always read the same way, and the turn does enter the cache: the key tells it apart from another upload of the same file that has been analysed.

**7. Interface and protocol** ([PROTOCOL.md](../PROTOCOL.md)): the attachment's card warns about pages without text, unreadable, or with possible hidden text; the turn shows the state of the check (`pdf.check`: checking, checked, with its cost or "already checked", or unchecked and why); and ChatGPT's answers carry a badge with the pages it read through Claude, the hidden ones and the ones that were not checked (`meta.pdf_reading`).

**8. Accounting.** Each check call is a billed call, with the purpose `check`: it shows in the statistics and counts towards the total of the turn that makes it.

**9. Storage** (migration 5): the `attachments.pdf_pages` column, with each page's data, and the `pdf_checks` table, with the check by content and version. A check is deleted when no attachment uses the file: when the last attachment that has it is deleted (unsent, or with its conversation) or, if an orphan is left, by the hourly sweep.

**10. Limits of the check.** It is a model's judgement. It finds missing text, unreadable text, hidden text and scanned pages well, but a small difference (a figure in a long table) can slip past it. Where the text layer is correct, ChatGPT still gets the exact text.

## Alternatives considered

- **A multipart/form-data upload:** it would need `python-multipart`, a new dependency and more parsing surface. A raw body with the name in the query string is enough for one file per request.
- **The file name in a header (`X-AOS-File-Name`) instead of the query string:** it would not reach the access logs without having to filter them, but it would change the protocol. The choice was that no log stores the query string of any URL (uvicorn and Caddy), which also protects the text of searches (`?q=`).
- **Trusting the `Content-Type` or the extension:** an HTML or SVG file labelled as an image could be served on the app's origin (XSS). The type comes from the content, and SVG is rejected.
- **Storing the files in SQLite:** it would make the database and the WAL grow, and Codex needs a path for `localImage`. The content-addressed files, next to the database, are on the same volume and in the same backups.
- **Decoding images on the server (Pillow) to validate or shrink them:** a native dependency with a history of vulnerabilities in its decoders. The browser already shrinks them, and the server only needs their dimensions, which it reads from the headers.
- **Reading PDFs in the server's process:** a hostile PDF could stall the process that serves everything, or exhaust its memory. That is why they are read in a separate process with limits.
- **Another PDF library:** PyMuPDF is AGPL and native; pdfminer.six is slower and brings more dependencies. pypdf (6.19.0) is pure Python, BSD-3, without mandatory dependencies. In the browser, `pdfjs-dist` (Mozilla, Apache-2.0) makes the thumbnails and the preview.
- **Sending PDFs whole to every phase:** it is the simplest and the most faithful option, but it multiplies the cost. The settings let the owner choose.
- **Rendering the PDF's pages as images for Codex (PDFium or poppler):** more faithful and independent of the text layer, but with native dependencies and more tokens on each ChatGPT call. It is kept for later, as an alternative to P7b.
- **Checking the PDF when it is uploaded:** the check would be ready before the turn, but each uploaded PDF would cost a Claude call, also the ones the owner removes from the composer or only sends to Claude.
- **Checking with a cheap Claude model (the fast one):** it would cost less, but transcribing scanned pages and finding hidden text is where quality matters most. The owner chose the turn's model.
- **A switch to turn the check off:** not for now; it can be added later if the owner wants one.
- **Detecting hidden text by the text layer alone (without Claude):** the analysis finds the signs of it, but it does not see white text on white or text under an image, and it cannot read a scanned page. That is why it is a warning, and Claude, which sees the page, does the check.
- **Letting Codex read the PDF with its tools:** the app turns its tools off and runs it in read-only mode, and app-server 0.157.1 has no document input.
- **Making the thumbnails on the server:** images and PDFs would have to be rendered on the server (native dependencies). The browser makes them once and uploads them.

## Consequences

- Two new dependencies: `pypdf` (Python) and `pdfjs-dist` (web).
- The protocol changes ([PROTOCOL.md](../PROTOCOL.md) and `web/src/lib/protocol.ts`): the `/api/attachments` routes, the `attachments` field of `turn.start`, the question's `meta.attachments`, `pdf_in_revisions` in the `RuntimeSettings`, and the `415` and `507` statuses.
- The internal contracts change: `Attachment` and `GenerationRequest.attachments` (`providers/base.py`); the `Store`'s `get_attachments`, `link_attachments`, `discard_conversation` and `NewMessage.attachments` (the question and its links in one transaction); `TurnRequest.attachments` and `pdf_in_revisions`; and the version of the turn cache key (6).
- The database schema goes to version 4. The data volume, and its backups, grow with the attachments sent.
- With Codex, each PDF of a turn costs Claude calls the first time it is used (the check), and ChatGPT's answer waits for it; the following turns reuse it.
- The database schema goes to version 5 (P7b): `attachments.pdf_pages` and the `pdf_checks` table. The PDFs uploaded before it have no analysis, and they are read as in P7a.
- The protocol changes (`Attachment.pdf_notes`, the `pdf.check` event, `meta.pdf_reading` and the optional `pdf_reading` of `stream.completed`), and so do the internal contracts: `Attachment.pdf_pages` and `pdf_check`, the `check` purpose, and the `Store`'s `get_pdf_check` and `put_pdf_check`.
- The turn cache key goes to version 7 (P7b): it includes the version of the check when the turn carries some PDF and, for each PDF, whether it has text and the warnings of its pages (or that it was not analysed), because two uploads of the same file can differ in these (one from before the analysis, or a reading that timed out). A turn where ChatGPT read a PDF that another turn would read differently never enters the cache (point 6).
- Security: no uploaded file is served as HTML; `nosniff` and a `sandbox` policy on all of them; PDFs are only read by a limited process; only one route, with a session, accepts the large body; the text of files cannot pass for part of the prompt; the text of a PDF that Claude finds is not visible does not reach ChatGPT on the subscription (that of the pages nobody has checked does), and every model gets the warning about the pages where the analysis suspects it, which is a heuristic; and no log stores file names.
- Caddy (`deploy/Caddyfile`) has a limit of its own for the upload: it must be restarted when updating (`docker compose restart caddy`, already in the "Updating" steps of [DEPLOYMENT.md](../DEPLOYMENT.md)).
- This decision is a proposal until the owner accepts it.
