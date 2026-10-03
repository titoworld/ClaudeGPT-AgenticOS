# Client ↔ server protocol

The contract between the frontend (`web/`) and the backend (`src/agentic_os/server/`). Everything is JSON in UTF-8. Field names are in English and in `snake_case`. The equivalent TypeScript types are in `web/src/lib/protocol.ts`.

## Authentication and common security

- Session with a `__Host-aos_session` cookie (HttpOnly, Secure, SameSite=Strict, Path=/). In development without HTTPS (`AOS_SECURE_COOKIES=false`) the cookie is called `aos_session` and is not `Secure`. The session expires after `AOS_SESSION_IDLE_HOURS` without activity (72 h) and, in any case, after `AOS_SESSION_MAX_DAYS` (30 days).
- Activity: only the owner's actions extend the idle expiry (logging in, opening a conversation, saving, deleting, renaming, `turn.start`, `turn.stop`, `turn.cancel`...). The REST requests the client makes on its own, without any action of the owner (the refreshes after a `hello` or a reconnection, the periodic refreshes and the retries), carry the header `X-AOS-Background: 1`. For these, the server checks the session read-only, like a `ping`: it answers the same way (`401` if the session is no longer valid), but does not extend the expiry. That way, a tab left open and unused does not keep the session alive. The client also sets the header on every `POST /api/auth/logout`, so that a logout that fails does not extend the session (if it works, it ends the session anyway). Without the header, or with another value, the request counts as activity. The WebSocket handshake is read-only too (see [WebSocket](#websocket-apiws)).
- Known device: every successful login also sets a `__Host-aos_device` cookie (HttpOnly, Secure, SameSite=Strict, Path=/; without HTTPS it is called `aos_device` and is not `Secure`) holding a random token that lasts 1 year and is replaced by a new one at every login. Logging out keeps it; `agentic-os init` and `agentic-os reset-sessions` forget every device. A login attempt that carries it is limited only by that device's failure counter (not by the address's nor by the global one), so nobody can lock the owner out of a browser where they have already logged in.
- Every route under `/api/` requires a session, except `GET /api/health`, `GET /api/auth/state` and `POST /api/auth/login`.
- Requests that change state (`POST`, `PUT`, `PATCH`, `DELETE`) and the WebSocket handshake must carry an `Origin` header listed in `Settings.allowed_origins`: that of `AOS_PUBLIC_ORIGIN` or one of `AOS_EXTRA_ORIGINS`. Otherwise the request gets `403`. The WebSocket, instead, is accepted and closed at once with code `4403`, so that the browser sees why: it sees no reason at all for a rejected handshake.
- HTTP errors: body `{"detail": "message in the client's language"}` (see [Languages](#languages)). `400` if the client drops the connection before sending the whole body, `401` without a session, `403` origin not allowed, `404`, `408` if the body does not arrive whole within 15 s (from when the server starts reading it; 120 s on `PUT /api/attachments`), `409` if the settings have changed since the client read them (`PUT /api/settings`, with `settings` in the body) or if the attachment being deleted has already been sent (`DELETE /api/attachments/{id}`), `413` body too large: at most 1 MiB, or 4 KiB on `POST /api/auth/login` (the only route read without a session), or 20 MB on `PUT /api/attachments` (each file type has a lower one: see [Attachments](#attachments)), by the `Content-Length` or counted as it arrives; the `detail` names the limit applied (`The request is too large (maximum 1 MiB).`, `The request is too large (maximum 4 KiB).` or `The request is too large (maximum 20 MB).`), `415` file type not supported (attachments only), `422` validation (numbers out of range too, however large; see *Input validation* below), `429` too many attempts (with a `Retry-After` header and a `retry_after` field in seconds), `507` the server has no disk space left to store an attachment.
- Input validation: conversation and attachment ids (in the path, in `before`, and in the WebSocket's `conversation_id` and `attachments`) must be integers from 1 to 2^63 − 1, the SQLite maximum; otherwise `422` (`Invalid data: "conversation_id".`, `"attachment_id"` or `"before"`). The text of JSON bodies (keys and values) must be encodable in UTF-8: a lone surrogate, which JSON writes as `"\ud800"` and which is valid JSON, gives `422` with `The request contains text that is not valid UTF-8.` and nothing is stored. Surrogate pairs, like `"😀"` (😀), are valid text. The only exception is `POST /api/auth/login`: a password or a code with such text are wrong credentials (`401`), and the attempt counts towards the lockout after failed attempts. Error messages never include Python's internal messages.
- A response sent before the server has received the whole request body (`403`, `401`, `413` by the `Content-Length`, `429`, `408`, or a body sent to a route that does not read it) carries `Connection: close`, and the server closes the connection: the client cannot reuse it. Requests without a body, or whose body was read whole, keep the connection.

## Languages

The server writes every text meant for people in the client's language: English, Spanish or Catalan ([ADR 0011](adr/0011-internationalization.md)). These are the `detail` of HTTP errors, the agents' `detail` (`ProviderStatus`), the models' descriptions (`ModelInfo`), and the messages, errors and reasons of the WebSocket and of the turns. Field names, identifiers and codes never change.

- **REST:** the language of the request's `Accept-Language` header: the one it prefers most among `en`, `es` and `ca` (by its `q` weights, the first listed on a tie; a tag with a region, like `ca-ES`, counts as its language), and English otherwise. The web app sends its own language on every request.
- **WebSocket:** `/api/ws?lang=en|es|ca`, since a browser cannot set headers on a WebSocket; English otherwise. The connection's language is that of its messages (the `hello`, the `error` messages) and of the turns it starts: their reasons, their failures and what they store. A turn's events keep that language, also for another connection that subscribes to them (`turn.subscribe`). When the owner changes language, the app reconnects and fetches the agents' status and the models again.
- **Stored texts** keep the language they were written in: a reloaded turn shows its reasons and failures as they were stored, whatever the client's language. Where the client must know what a text says, not only show it, the wire also carries a code: `reason_code` for the rounds and versions of a Refine turn (see `refine.round`), and `attachment_id` for the error about an attachment that no longer exists (see `turn.failed`). Turns stored before ADR 0011 have no code: the client recognizes their Catalan texts.
- The models' answers are not texts of the server: the prompts ask the models to answer in the language of the user's message, whatever the language of the interface. The demo answers (the `fake` providers) are written in the turn's language.

## REST

Every request can carry an `Accept-Language` header: the server writes the texts of its answer in that language (see [Languages](#languages)).

| Method and route | Body / parameters | Response |
| --- | --- | --- |
| `GET /api/health` | – | `{"status": "ok"}` |
| `GET /api/auth/state` | – | `{"authenticated": bool, "setup_required": bool}` (`setup_required`: `agentic-os init` has not been run yet) |
| `POST /api/auth/login` | `{"password": str, "totp": str}` | `204` + session and device cookies; `401`; `429`. The login completes in a single transaction, conditional on the owner whose credentials were checked: if `agentic-os init` has changed it in the meantime, `401` and nothing is stored. The same transaction ends the session the cookie presented, if there was one; its WebSockets are closed afterwards |
| `POST /api/auth/logout` | – | `204` (deletes the session cookie and closes this session's WebSockets; the device cookie is kept); `401` without a session. For the client, only `204` and `401` mean that the session has ended. With any other response, or if none arrives, it locks the page locally (on the server, the session stays open) and, until the server confirms the logout or the owner logs in again, every page load retries the logout before querying `GET /api/auth/state` |
| `GET /api/providers` | – | `[ProviderStatus]` |
| `GET /api/models` | optional `?refresh=1` (skips the cache) | `ModelCatalog` |
| `GET /api/pricing` | – | `Pricing` |
| `GET /api/spend` | – | `MonthSpend` (the current month, for the budget bars) |
| `GET /api/settings` | – | `RuntimeSettings`, with the current `revision` |
| `PUT /api/settings` | `RuntimeSettings` with the `revision` the change is based on (required; the other missing keys take their default value) | The saved `RuntimeSettings` (`revision` + 1); `409` or `422` (see [Saving the settings](#saving-the-settings)) |
| `GET /api/conversations` | `?limit=50&before=<id>&q=<text>` (see [Listing and searching conversations](#listing-and-searching-conversations)) | `[ConversationSummary]`, most recent first |
| `GET /api/conversations/{id}` | – | `ConversationDetail` |
| `PATCH /api/conversations/{id}` | `{"title": str}` | `ConversationSummary` |
| `DELETE /api/conversations/{id}` | – | `204`. The conversation's running turns are cancelled, and the server forgets all its turns: a later `turn.subscribe` gets `turn.unknown` |
| `GET /api/stats` | `?days=30` (1–365) | `Stats` |
| `PUT /api/attachments` | `?name=<file name>`; the body is the file as it is, not multipart (see [Attachments](#attachments)) | `201` + `Attachment`; `413`, `415`, `422`, `507` |
| `GET /api/attachments/{id}` | – | `Attachment` |
| `GET /api/attachments/{id}/content` | – | The file, with the type detected when it was uploaded (see [Attachments](#attachments)) |
| `PUT /api/attachments/{id}/thumbnail` | The body is the thumbnail: PNG or WebP, at most 100 kB and 512 px per side | `204`; `404`, `413`, `415`, `422` |
| `GET /api/attachments/{id}/thumbnail` | – | The thumbnail (`image/png` or `image/webp`), or `404` if it has none |
| `DELETE /api/attachments/{id}` | – | `204` if it has never been sent; `409` if it is already in a question (it is deleted with the conversation) |
| `GET /api/ws` | WebSocket | see below |

### Types

```ts
type Agent = "claude" | "chatgpt";
type TurnMode = "solo" | "duel" | "debate" | "refine";   // "debate": Council; "refine": Refine (ADR 0010)
type MessageKind = "question" | "answer" | "revision" | "synthesis";

interface Usage {
  input_tokens: number;        // input not served from the cache
  output_tokens: number;       // includes the reasoning
  cache_read_tokens: number; cache_write_tokens: number;
  reasoning_tokens: number;    // part of output_tokens: not added to it again
  cost_usd: number | null;     // estimated cost (API prices); null if the model has no price
}
// Processed tokens (ADR 0008): input_tokens + cache_read_tokens + cache_write_tokens +
// output_tokens. It is the definition of every token count: a turn's total, the
// savings (cache and early_stop) and the dashboard's ratio, with the same definition in
// the numerator and the denominator. The client computes it (processedTokens in web/src/lib/costs.ts).
// A Usage is always of a single model, except the totals (of a turn, an agent, a day).

interface ProviderStatus {
  agent: Agent; mode: "cli" | "api" | "fake";
  available: boolean; model: string; detail: string;   // detail: in the client's language
  limits: { window: string;            // "5h", "7d"...
            used_percent: number | null;   // 0–100 (can go over 100)
            resets_at: string | null;  // ISO 8601
            status: string }[];        // "allowed" | "warning" | "rejected"
}

interface ModelInfo {
  id: string;                  // the value sent to the provider (API id, CLI alias...)
  label: string; description: string;   // description: in the client's language
  is_default: boolean; context_window: number | null;
}

interface ModelCatalog {
  claude: AgentModels; chatgpt: AgentModels;
}
interface AgentModels {
  mode: "cli" | "api" | "fake";
  default_model: string;       // the one used if none is chosen
  fast_model: string;          // the one for internal calls (summaries)
  models: ModelInfo[];         // the provider's live list, or a fallback one
  live: boolean;               // false if the list is the fallback one
}
// Any id matching ^[A-Za-z0-9][A-Za-z0-9._:/@\[\]-]{0,99}$ is valid:
// that way new models can be used even if they are not on the list.

interface FxRate {
  eur_per_usd: number;
  as_of: string | null;        // date (YYYY-MM-DD) of the ECB rate
  source: "ecb" | "manual";
}

interface ModelPrice {         // USD per million tokens, as the providers publish them
  input: number; output: number; cache_read: number; cache_write: number;
}

interface Pricing {
  fx: FxRate;
  prices: (ModelPrice & { model: string; key: string; source: "default" | "custom";
                          default: ModelPrice | null })[];
}
// prices: exactly the table the costs are computed with, sorted by model: the default
// prices with the owner's on top (a custom price replaces the default one of the same
// normalized model, without provider prefix, date or context).
// key: the normalized id the server compares models by (normalize_model: trimmed and
// lowercased, without everything up to the last "/" nor the "anthropic." prefix, without
// a "[…]" context at the end, and then without a "-YYYYMMDD", "@YYYYMMDD" or "-latest"
// date at the end). Two rows never have the same key. A model whose key has no row pays
// the price of the row with the longest key that is a prefix of its own.
// The vectors in tests/fixtures/model_ids.json pin down the normalization for the server
// and for the web.
// default: on a "custom" row that replaces a default price (the same key), that default
// price; null on the others.

interface RefineOptions {                 // the options of a Refine turn (ADR 0010)
  max_rounds: number;                     // 2–50, default 12: the rounds that write a
                                          // version, the merge of round 1 included
  budget_eur: number;                     // 0.1–100, default 3: what the turn may spend,
                                          // in euros (in subscription mode, the value at API prices)
  max_words: number | null;               // word limit of each version: 100–20000, or
                                          // null (the default) for the automatic limit:
                                          // 1.2 times the word count of version 1, and at least 300
                                          // (each version must fit in a single reply:
                                          // see "Message metadata")
  stop_on_convergence: boolean;           // default true: the turn stops by itself when it
                                          // converges (stop_reason "converged")
  convergence_threshold: number;          // 50–100, default 90
  editor: Agent;                          // default "claude": merges the answers and
                                          // writes each version
}

interface RuntimeSettings {
  revision: number;                       // saves: 0 until the first one, +1 at each one
                                          // (see "Saving the settings")
  default_mode: TurnMode;                 // default "debate"; never "refine"
  default_target: Agent;                  // the agent of the solo mode
  debate: { rounds: number;               // 0–4, default 2
            consensus_threshold: number;  // 50–100, default 85
            synthesizer: Agent };         // default "claude"
  refine: RefineOptions;                  // the options of the Refine turns that do not
                                          // carry their own
  use_cache: boolean;                     // default true
  compaction_threshold_tokens: number;    // 1000–100000, default 6000
  models: Record<Agent, string | null>;       // default model; null = the provider's
  fast_models: Record<Agent, string | null>;  // model for the summaries; null = the provider's
  prices: Record<string, ModelPrice>;         // custom prices (they replace or add models);
                                              // 422 if a key identifies no model once
                                              // normalized ("openai/") or if two are the same
  fx: { mode: "auto" | "manual";              // auto: the ECB's daily rate, with the manual one as a fallback
        eur_per_usd: number };                // 0.2–5, default 0.86
  budgets_eur: Record<Agent, number | null>;  // monthly budget for API usage
  plans_eur: Record<Agent, number | null>;    // monthly price of the subscription
  pdf_in_revisions: "full" | "text";          // PDFs in a debate's revisions: "text" (the
                                              // default) the extracted text; "full" the document.
                                              // A PDF without text always goes whole
}

interface AgentSpend {
  api_usd: number;             // real cost of the calls in api mode
  equivalent_usd: number;      // value of the calls in cli mode at API prices
  unpriced_calls: number;      // calls of models without a known price
  budget_eur: number | null;  budget_used: number | null;   // 0–1+ (api_usd in € / budget); null without a budget
  plan_eur: number | null;    plan_value: number | null;    // 0–1+ (equivalent in € / plan price); null without a price
}
// equivalent_usd includes every call that is not an API one and has a price (the demo ones too, if you give them a price).

interface MonthSpend {
  month: string;               // "YYYY-MM" (UTC)
  fx: FxRate;
  by_agent: Record<Agent, AgentSpend>;
}

interface ConversationSummary {
  id: number; title: string;
  created_at: string; updated_at: string;   // ISO 8601 UTC
  last_mode: TurnMode | null; message_count: number;
}

interface Message {
  id: number; turn_id: number; kind: MessageKind; content: string;
  agent: Agent | null; round: number; final: boolean;
  meta: Record<string, unknown>;   // see "Message metadata"
  created_at: string;
}

interface ConversationDetail extends ConversationSummary {
  summary: string | null;          // the compaction summary, if there is one
  messages: Message[];             // all of them, oldest first
}

interface Attachment {             // an attached file (see "Attachments")
  id: number;
  name: string;                    // display name, cleaned (no path, no control characters)
  kind: "image" | "pdf" | "text";
  mime: string;                    // "image/png" | "image/jpeg" | "image/gif" | "image/webp" |
                                   // "application/pdf" | "text/plain"
  size: number;                    // bytes
  pages: number | null;            // PDF
  width: number | null; height: number | null;   // images, in pixels
  sha256: string;                  // of the content
  created_at: string;              // ISO 8601 UTC, when it was uploaded
  has_thumbnail: boolean;          // the browser has uploaded its thumbnail
  text_available: boolean;         // text: always; PDF: its text could be extracted
  estimated_tokens: number;        // approximate input tokens per call
  pdf_notes: {                     // warnings about the pages of an analysed PDF (see
    no_text: number[];             // "Attachments"); null for the others. Page numbers, from 1:
    garbled: number[];             // without text (scanned), with unreadable text
    hidden: number[];              // and with text that may not be visible
  } | null;
}

interface Stats {
  days: number;
  totals: { calls: number; errors: number; cost_usd: number;
            by_agent: Record<Agent, Usage & { calls: number }> };
            // errors: calls with ok = false (failed ones, and every attempt a model
            // declined before a fallback)
  savings: { cache: number; compaction: number; early_stop: number;
             unchanged: number; total: number; cost_usd: number | null };
             // cost_usd: the value of the window's savings; like the tokens, it is kept
             // even if the conversation is deleted (null if no saving has a price).
             // Processed tokens; the rows stored before ADR 0008 keep the old
             // definition (input + output in cache and early_stop).
  daily: { date: string; agent: Agent; input_tokens: number; output_tokens: number;
           cache_read_tokens: number; cache_write_tokens: number;
           cost_usd: number }[];   // date: UTC calendar day; the four kinds of processed
                                   // tokens, to add them up as in Usage
  savings_daily: { date: string; kind: "cache" | "compaction" | "early_stop" | "unchanged";
                   tokens: number }[];
  latency: Record<Agent, { p50_ms: number | null; p95_ms: number | null;
                           ttft_p50_ms: number | null }>;
                           // only the calls that write a message (answers,
                           // revisions and syntheses): not the history summaries nor the
                           // PDF checks, which count in the tokens and the costs
  turns: { solo: number; duel: number; debate: number; refine: number };
                           // the window's questions, by the turn's mode
  consensus: { debates: number; reached: number; avg_rounds: number | null };
                           // debates only: the final version of a Refine turn is
                           // a synthesis too, but it does not count here
  costs: { fx: FxRate;
           by_agent: Record<Agent, { api_usd: number; equivalent_usd: number;
                                     unpriced_calls: number }> };
  month: MonthSpend;
}
```

### Saving the settings

`revision` counts the saves of the settings: it is 0 where they have never been saved and grows by 1 at each successful save. Settings saved by an earlier version, without a revision, count as revision 1. That way, only the built-in settings, which a client has while it has not yet read the server's, are at revision 0, and a save based on them can never replace saved settings. It is stored with the settings, so it survives a restart, and it never goes back. The client edits from the settings it has read and sends all of them to `PUT /api/settings`, with the `revision` of the ones it read ([ADR 0006](adr/0006-settings-revisions.md)):

- `200`: the revision is the current one. The response is the saved settings, with `revision` + 1.
- `409`: the revision is not the current one, usually because the settings were saved from another tab or device. Nothing is saved. The body is `{"detail": "The settings have changed in another tab or on another device. Review them and save them again.", "settings": RuntimeSettings}`, with the current settings as `GET /api/settings` gives them: the client shows them, and the owner reviews them and saves them again.
- `422`: `revision` is missing or is not an integer ≥ 0, or some other field is not valid. `default_mode` cannot be `refine` (`The default mode cannot be "refine".`): a Refine turn lasts until you stop it, so it only starts when you choose it. The order is: the body must be a JSON object, then `revision`, then the other fields. The revision is compared last, so an invalid edit gives `422` even if it is based on an old revision.
- The comparison and the write are atomic (a single SQLite write transaction): of two requests based on the same revision, one gets `200` and the other `409`, even if they come from different processes.

### Listing and searching conversations

`GET /api/conversations` gives the conversations by activity, most recent first (by `updated_at` and, on a tie, by `id`), in pages:

- `limit`: from 1 to 200 conversations per page (50 by default).
- `before`: the `id` of the last conversation of the previous page. It gives the ones that follow it in this order, or none if that conversation no longer exists.
- `q` (optional): only the conversations whose title contains this text, ignoring case and accents. The server transforms the title and the text in the same way: compatibility decomposition (NFKD), `casefold`, and no combining marks. That way, `cafe` finds "Cafè", `strasse` finds "Straße" and `fi` finds "ﬁnances". The match is literal: `%`, `_` and `\` are characters like any other. The text is trimmed, and a run of spaces counts as one, as in the titles. An empty `q`, or one with only spaces, is the same as none.
- The search text can be at most 200 characters long, once trimmed. A longer one gives `422` with `The search cannot be longer than 200 characters.`
- A search is paged like the list: `before` is the `id` of the last conversation of the previous page of the same search. The response has the same shape, `[ConversationSummary]`.

### Attachments

The files the owner attaches to a question ([ADR 0009](adr/0009-attachments.md)). The limits are the constants of `src/agentic_os/attachments.py`.

- **Type.** It always comes from the content, never from the name nor from the `Content-Type`:
  - PNG, JPEG, GIF and WebP images, by their first bytes; their dimensions, from their headers (the server never decodes an image);
  - PDF, by the `%PDF-` at the start;
  - text: valid UTF-8 without any NUL character and with one of these extensions: `txt`, `md`, `markdown`, `csv`, `tsv`, `json`, `yaml`, `yml`, `xml`, `html`, `htm`, `log`, `ini`, `toml`, `cfg`, `py`, `js`, `ts`, `jsx`, `tsx`, `svelte`, `css`, `scss`, `sql`, `sh`, `bash`, `rs`, `go`, `java`, `kt`, `c`, `h`, `cpp`, `hpp`, `cs`, `rb`, `php`, `swift`, `lua`, `r`, `pl`. It is always plain text (`text/plain`), an `.html` file too;
  - anything else, SVG and HEIC included, gives `415`.
- **Limits:**
  - at most 5 attachments per message, and 20 MB in total;
  - image: 7 MB and 8,000 pixels per side. Before uploading an image, the browser shrinks any image over 2,576 pixels on the long side, and re-encodes at the same size one over 7 MB; it also re-encodes upright a photo that is shown rotated by its EXIF orientation (like phone photos), because the models get its pixels as they are stored, without the metadata. GIFs are uploaded as they are;
  - PDF: 20 MB and 100 pages, not encrypted;
  - text: 200 kB.
  - Too large: `413`, with the type's limit (`The file is too large: an image can be at most 7 MB.`). Not valid (empty, without a name, an unreadable image, too many pixels or pages, an encrypted or damaged PDF): `422`.
- **Upload:** `PUT /api/attachments?name=<name>`, with the file as the body. It needs the session and the `Origin`, like every write. The server writes the body to a temporary file as it arrives and cuts it off at the limit of its type, which the first 16 bytes decide: if the `Content-Length` already exceeds it, the server answers `413` at once, without reading any more. `name` is the display name (and the one that gives a text file its extension): the server keeps the last part of a path, in NFC, without control characters or invisible format characters (like those that reverse the direction of the text), with each run of spaces as one, and at most 200 characters (a longer one is cut and keeps its extension). An attachment that is not sent in any turn is deleted after 24 h.
- **Text of a PDF:** the server reads it with pypdf in a separate process, for at most 60 s. It has one block per page, introduced by the line `--- Page N ---`, and the models get it when they do not get the document (see `pdf_in_revisions` and the ADR). `text_available` is `false` if no text could be extracted: a scanned PDF, or an error or a timeout while extracting it (if not even its pages can be counted, the upload gives `422`). If it exceeds 1,000,000 characters, it is cut off and ends with the notice `[Text truncated: the text extracted from the PDF was over 1,000,000 characters.]`.
- **Pages of a PDF** (`pdf_notes`): in the same pass, the reader analyses every page, also the ones the text limit leaves out: where its text is within the stored text, how many letters and how many broken characters it has (U+FFFD, private-use or control characters: a font without a character map), whether it draws any image, and how much text it shows that cannot be seen: in an invisible rendering mode (3 or 7), smaller than 1 point in some direction (counting the font size, the horizontal scaling `Tz`, the text matrix, the transformation matrix and that of the form that draws it), or with its origin more than 1 point outside the visible part of the page (the `CropBox` within the `MediaBox`), counting the text's vertical shift (`Ts`). `pdf_notes` gives its warnings, per page:
  - `no_text`: fewer than 25 letters (a scanned page, or text drawn as an image);
  - `garbled`: 5 broken characters or more, and at least 5% of the text (a page without text is not listed);
  - `hidden`: 10 characters or more that cannot be seen. Invisible text only counts on a page without images: over a scan, it is the recognized text, which is legitimate.

  They are warnings, not verdicts: the analysis does not see text hidden by a clipping path or text in the colour of what lies beneath it, and an image the page has in its `Resources` counts even if the page does not draw it. A PDF that could not be analysed has `pdf_notes: null`, like images, text files and the PDFs uploaded before the analysis existed. The `meta.attachments` stored before it do not have the key.
- **Claude's check** ([ADR 0009](adr/0009-attachments.md)): ChatGPT on the subscription (Codex, `cli` mode) cannot open a PDF and reads its extracted text, page by page. In a turn where ChatGPT takes part and the question carries analysed PDFs, Claude checks that text against the document (in a duel or a debate, while it answers): on the pages where the extracted text is missing or unreadable, ChatGPT reads what Claude reads there, marked as such, and from a page where Claude finds text that cannot be seen, ChatGPT only gets the visible text and the warning. ChatGPT waits for the check for at most 5 minutes; then it reads the text unchecked. Claude makes at most 3 calls per PDF: the pages that do not fit are left unchecked, and say so. The demo Claude (`AOS_CLAUDE_MODE=fake`) checks nothing, because it answers that every page is correct without reading any: with it, the PDF is read unchecked, as without Claude. The pages nobody has checked (`unchecked_pages`) reach ChatGPT as they were extracted, any text that cannot be seen included: they say that they have not been checked and, if the analysis finds them suspicious, that they may have text that cannot be seen, a warning every model also gets in the PDF's label. The analysis is a heuristic, not a guarantee. The check is stored by the file's content, and later turns and other conversations reuse it; it is deleted when no attachment uses the file any more. A turn in which ChatGPT read a PDF that another turn would read differently never enters the turn cache: when the next turn would check it again (the check failed, Claude declined to do it, a reply made no progress or took too long) or when nobody could check it (without Claude, or with the demo one). It shows in the `pdf.check` event and in the `meta.pdf_reading` of ChatGPT's messages. The calls are billed with the purpose `check` and count towards the turn's total.
- **`estimated_tokens`** (approximate, for each call that gets the attachment): image `ceil(w'/28) · ceil(h'/28)`, at most 4,784, with `(w', h')` the image shrunk to 2,576 pixels on the long side (never enlarged); PDF, 3,600 per page; text, `ceil(characters / 4)`.
- **Content** (`GET /api/attachments/{id}/content`): the file as it was uploaded, with the detected type (`text/plain; charset=utf-8` for text), `X-Content-Type-Options: nosniff` and `Content-Security-Policy: default-src 'none'; sandbox`. Images carry `Content-Disposition: inline`; PDFs and text, `attachment` (they are downloaded). Both with the name: `filename` in ASCII and `filename*` in UTF-8. Nothing that is uploaded is ever served as HTML. `Range` is supported.
- **Thumbnails:** the browser makes one when the file is attached and uploads it to `PUT /api/attachments/{id}/thumbnail`: a PNG or WebP image (by its content) of at most 100 kB and 512 pixels per side; otherwise `415`, `413` or `422`. A new thumbnail replaces the previous one. `GET` returns it, with the same headers as an image, or `404` if there is none.
- **Deleting:** `DELETE /api/attachments/{id}` deletes an attachment that has never been sent (the owner removed it from the composer): `204`. If it is already in a question, `409`, and it is deleted with its conversation: deleting a conversation deletes the attachments that only it used. Two uploads of the same file share its copy, which is deleted, and Claude's check with it, when no attachment uses it.

### Turn outcome (`meta.outcome` of the question)

How a turn ended is decided only once, and it is stored in the question before the final event ([ADR 0007](adr/0007-turn-outcome.md)):

```ts
interface TurnOutcome {
  status: "completed" | "failed" | "cancelled";
  error?: { kind: string; message: string;     // only if status is "failed" (that of
            attachment_id?: number };          // turn.failed); attachment_id: the attachment
                                               // that no longer exists, when that is the error
  failures: { agent: Agent; kind: string; message: string; round: number }[];
                                   // the turn's call failures (stream.failed), in order
  usage: Usage;                    // the turn's total: every billed call (compaction
                                   // summaries, PDF checks, failed calls, declined
                                   // attempts and calls without a message included); the
                                   // same usage as the final event's
  savings: object;                 // like the savings of turn.completed (see below);
                                   // zeros in a failed or cancelled turn (it records no
                                   // savings), except a turn cancelled when it was already
                                   // storing its savings: the rows are written whole, and
                                   // it carries them
  consensus: object | null;        // like the consensus of turn.completed: that of a completed debate
  final_message_ids: number[];     // final messages stored (in a cancelled turn too)
  cached: boolean;                 // served from the turn cache
  stop_reason?: "owner" | "converged" | "unchanged" | "max_rounds" | "budget" | "failed";
                                   // only in a Refine turn (see below)
}
```

- The question is created with `outcome: null`. If the turn never ends (a crash or a restart of the server), it stays `null`: the client shows it as not completed. Turns stored before ADR 0007 do not have the key.
- The `message` of `error` and of `failures` is in the language of the client that started the turn, and keeps it (see [Languages](#languages)).
- A cancelled turn stores it too, before `turn.cancelled`. A turn is cancelled only once: a repeated `turn.cancel`, or the server shutting down, while the turn is stopping does not interrupt it, and `turn.cancelled` arrives when the turn has stopped and stored its outcome, with the same `usage`. If the write fails, the turn does not fail, and the question stays `null`.
- `stop_reason`, only in a Refine turn ([ADR 0010](adr/0010-refine-mode.md)), says why it ended with the last version, which is the final answer:
  - `owner`: the owner stopped it, after the round (`turn.stop`) or at once (`turn.cancel`);
  - `unchanged`: neither model found anything to change for 2 rounds in a row;
  - `converged`: both gave it the threshold (`convergence_threshold`) or more, without proposing any defect, for 2 rounds in a row (only with `stop_on_convergence`);
  - `max_rounds`: it has done the rounds of `max_rounds`;
  - `budget`: it has spent the budget (`budget_eur`);
  - `failed`: both models failed in a round when there was already a version. The turn completes anyway, with the current version as the final answer and the failures in `failures`.
- A cancelled Refine turn (`turn.cancel`) that already has a version first stores it as the final message, without any call, so that nothing already paid for is lost: `status` is still `cancelled`, `final_message_ids` includes it, and `stop_reason` is `owner`.

### Message metadata (`meta`)

- Question (`question`): `mode`, `target`, `options`, `models` (the models chosen for this turn, if any), `attachments` (the question's attachments, in order: `Attachment[]` as they were when the turn started; only present if it has any), `compaction_usage` (the `Usage` of all the turn's summary calls, if there were any), `refine` (in a Refine turn, its `RefineOptions`: a reloaded turn shows its limits) and `outcome` (see [Turn outcome](#turn-outcome-metaoutcome-of-the-question)).
- Answers (`answer`, `revision`, `synthesis`): `model`, `usage` (with `cost_usd`; only that of the attempt that answered), `cost_basis` (`"api"`: real cost; `"equivalent"`: subscription mode, the value at API prices), `latency_ms`, `ttft_ms`, `cached` (whether it comes from the cache).
- An answer served after a fallback (Claude's API): `declined`, `[{model, usage}]`, the billed attempts that other models declined before, each with its model and its cost. They are calls billed separately (one usage row per attempt, with `ok = false`) and they count towards the turn's total, but not towards the message's `usage`: the tokens of different models are never added up ([ADR 0008](adr/0008-token-accounting.md)).
- A truncated answer: `truncated: true` and, if known, `finish_reason` (`"max_tokens"`: output limit; `"content_filter"`: content filter; `"incomplete"` or `"interrupted"`; or a value of the provider's own). They are only present when the model's answer was cut off before the end: the content is a useful partial answer, never a complete one. In a revision that keeps the previous answer, what was cut off is the critique or the new answer. A turn with any truncated message never enters the turn cache. A degraded synthesis that reuses a truncated answer carries the mark too ([ADR 0005](adr/0005-answer-integrity.md)).
- Revision (`revision`): also `critique` (text), `agreement` (0–100 or `null`) and `unchanged` (bool). If `unchanged` is true, `content` holds the previous answer, which is kept, and `unchanged_note` (optional) is the short note the model wrote after `UNCHANGED`, on the same line (at most 200 characters). `content` also holds the previous answer (with `unchanged: false`) when the revision was cut off before the answer.
- Synthesis (`synthesis`): also `consensus` (`{reached, round, scores}`), and `degraded: true` if it was stored without calling any model.
- Messages of a Refine turn ([ADR 0010](adr/0010-refine-mode.md)): they use the usual kinds. The answers of round 0 are `answer`. The reviews, the versions and the final answer carry `refine`, the same object as the `refine` of their `stream.completed`:

  ```ts
  interface RefineChange { kind: string; text: string }
  // kind: "defect" (an error), "clarity", "simplification" (remove or simplify)
  // or "requirement" (a requirement of the brief); "merge" in the lines of version 1,
  // which say what it took from each answer
  type RefineMeta =
    | { role: "review";              // a review (kind "revision", from round 2 on)
        score: number | null;        // 0–100: how well the version meets the brief (null:
                                     // the review did not score it)
        unchanged: boolean;          // it proposes no change (UNCHANGED)
        changes: RefineChange[] }    // the changes it proposes (at most 5)
    | { role: "version";             // a version by the editor (kind "revision", from round 1
                                     // on)
        version: number;             // the current one + 1: one that is not accepted shares
                                     // its number with the next one written
        words: number; budget_words: number;   // its words and the turn's limit
        accepted: boolean;           // it has become the current version
        reason: string | null;       // why not, as a text (the reason of refine.round)
        reason_code?: string | null; // why not, as a code (the reason_code of refine.round);
                                     // missing in the versions stored before ADR 0011
        changelog: RefineChange[];   // the changes it has applied (at most 5)
        copied_from?: number }       // a version 1 stored without any call (neither model
                                     // could write it): the id of the copied answer
    | { role: "final";               // the final answer (kind "synthesis", final)
        version: number; words: number; budget_words: number;
        stop_reason: string };       // that of the turn's outcome
  ```

  Every version the editor writes is stored, also the ones that are not accepted (with `accepted: false` and the reason) and the attempt to shorten one that goes over the word limit, so that the owner can see them. Version 1 has no previous version to keep: if the merge goes over the owner's word limit (`max_words`), it is stored with `accepted: false`, and the attempt to shorten it is version 1, with `accepted: true` even if it still goes over (`words` greater than `budget_words`), just like a version 1 copied from an answer. If the turn is cancelled while shortening, the final answer is the merge. The editor writes each version whole in a single reply, of at most 16,000 output tokens, reasoning included: a version that does not fit is cut off and not accepted (`The editor wrote no complete version.`), so a `max_words` of more than about 10,000 words (fewer in languages such as Catalan, or in code) cannot be met. The final answer is the current version, stored without any call (zero `usage`): the agent that wrote it, the `round` of the last round, and `meta.copied_from`, the `id` of the version's message.
- ChatGPT's messages (`answer`, `revision`, `synthesis`) for a question with PDFs, when it cannot open them (the subscription; see *Claude's check* under [Attachments](#attachments)): `pdf_reading`, how it read each PDF, in the order of the attachments:

  ```ts
  interface PdfReading {
    attachment_id: number; name: string;
    checked: boolean;              // Claude has checked at least one page of it
    claude_pages: number[];        // pages ChatGPT read, wholly or in part, as Claude
                                   // read them (without text, unreadable, incomplete
                                   // or with text that cannot be seen), or with Claude's
                                   // description of what the figures show
    hidden_pages: number[];        // pages with text that cannot be seen: ChatGPT did not get it
    unchecked_pages: number[];     // pages nobody has checked: the extracted text as it is
    reason: string | null;         // why pages are left unchecked (null if none is), in the
                                   // language of the client that started the turn
  }
  ```

  An answer served from the turn cache keeps the stored `meta`.
- The turn's final messages (those in `final_message_ids`): `savings` (the same object as in `turn.completed`). Every final message also carries `unstored_usage` (`Usage`) if, until then, there were billed calls that left no message (errors, empty answers, refusals, the output limit used up without any text, declined attempts).
- Total of a reloaded turn: the question's `outcome.usage`, as it is (`compaction_usage` and `unstored_usage` are not added to it again). It equals the `usage` of the final event, and the sum of the turn's usage rows plus those of its compaction summaries, which are stored without a `turn_id` because they are made before the question exists. For the turns without `outcome` (stored before ADR 0007), the sum of their messages' `usage`, plus `compaction_usage` and the `unstored_usage` of the last final message, which does not include a failed call that ended after the other agent of a duel had stored its answer.

## WebSocket `/api/ws`

A single persistent connection per tab, at `/api/ws?lang=en|es|ca`: the language of its messages and of the turns it starts (see [Languages](#languages)). The server closes it with code:

- `4401` if there is no session when connecting, and also when the session ends with the socket open: at once if it is a logout on this server, and within 30 s if `agentic-os reset-sessions` has revoked it or it has expired (the server checks it again every 30 s even if the client sends nothing).
- `4403` if the origin is not valid (see [Authentication and common security](#authentication-and-common-security)).
- `1013` if the client does not receive fast enough: it has 4,096 messages waiting to be sent, or 200,000 events or more (each event of a `turn.subscribe` replay counts; a single replay can be longer, so any turn can be recovered), when another one arrives. It must reconnect and send `turn.subscribe` from the last `seq` it has.
- `1011` if there is an internal error.

The handshake and every client message (with `type`) check the session. Only `turn.start`, `turn.stop` and `turn.cancel`, which are the owner's actions, count as activity. The handshake, the `ping` and the `turn.subscribe` (which the client sends on its own after reconnecting) check it read-only: they do not extend the idle expiry. That way, a tab left open and unused does not keep the session alive, even if it reconnects.

### Client → server

```jsonc
{"type": "turn.start", "request_id": "uuid", "text": "…", "mode": "debate",
 "target": "claude", "conversation_id": null,
 "options": {"debate": {"rounds": 2, "consensus_threshold": 85, "synthesizer": "claude"},
             "use_cache": true},
 "models": {"claude": "opus", "chatgpt": "gpt-6-sol"},
 "attachments": [12, 13]}
{"type": "turn.start", "request_id": "uuid", "text": "…", "mode": "refine",
 "options": {"refine": {"max_rounds": 12, "budget_eur": 3, "max_words": null,
                        "stop_on_convergence": true, "convergence_threshold": 90,
                        "editor": "claude"}}}
{"type": "turn.stop", "request_id": "uuid"}     // Refine: stop it after the current round
{"type": "turn.cancel", "request_id": "uuid"}
{"type": "turn.subscribe", "request_id": "uuid", "after_seq": 12}   // after a reconnection
{"type": "ping", "t": 1727450000000}
```

`mode`, `target`, `options` (partial ones too) and `models` are optional: the `RuntimeSettings` apply. In `models` (and in the `RuntimeSettings`), `null` or `""` means the default model; identifiers are trimmed. Limit: 3 simultaneous turns, and a single one per conversation.

A Refine turn (`mode: "refine"`, [ADR 0010](adr/0010-refine-mode.md)) takes its options from `options.refine`, partial ones too: the missing keys take the value of the `RuntimeSettings`, and `max_words: null` means the automatic limit. They are validated with the ranges of `RefineOptions`; otherwise, `error` with `code: "invalid"`, the `request_id` and the settings' message (like `"refine.max_rounds" must be an integer between 2 and 50.`), and the turn does not start. The engine counts in dollars, so the server passes it the budget (`budget_eur`) converted with the rate the app shows euros with: the `fx` of the `hello` and of `GET /api/pricing` (the ECB's in `auto` mode if it is recent; otherwise, the manual one). That way, the turn stops when what the interface shows it has spent reaches the budget.

`turn.stop` ("Stop after this round") asks a Refine turn to stop at the end of the current round: no call is cut off, the round finishes (the reviews and the edit), and the turn completes with the last version and `stop_reason: "owner"`. The turn announces it with `turn.stopping`, only once, even if it is asked more than once. To stop it at once there is `turn.cancel` ("Stop now"), which also stores its last version (see [Turn outcome](#turn-outcome-metaoutcome-of-the-question)). A `turn.stop` for a turn the server does not have gets `turn.unknown`, like a `turn.cancel`; one for a turn that has already ended, or is already being cancelled, gets no answer. One for a running turn of another mode, which has no rounds to finish, gets `error` with `code: "invalid"`, the `request_id` and `Only a Refine turn can stop after the current round; to stop it now, cancel it.`, and the turn continues.

`attachments` (optional) are the `id`s of the uploaded attachments, in the order they go in the question: at most 5, each one once, integers from 1 to 2^63 − 1. Otherwise, `error` with `code: "invalid"` and the `request_id`, and the turn does not start. An attachment that does not exist, or attachments adding up to more than 20 MB, give `turn.failed` with `kind: "invalid"` (`Attachment 12 does not exist.`, with `attachment_id: 12` in the `error`), without `turn.started`, and nothing is stored. The same happens if an attachment is deleted while the turn is being prepared (from another tab, or by the sweep of an unsent attachment older than 24 h): the question is stored with its attachments in a single transaction, before `turn.started`, and a new conversation the turn had just created is deleted. The answers and the synthesis get the attachments whole; a debate's revisions get the PDFs as the `pdf_in_revisions` of the `RuntimeSettings` says (its value when the turn starts), except a PDF from which no text could be extracted (a scanned one), which goes whole.

The question (`text`) can have at most 100,000 characters. An empty or longer one gives `turn.failed` with `kind: "invalid"`, without `turn.started`, and nothing is stored.

No client message can exceed 524,288 characters (512 × 1024). The server does not read a longer message: it answers `{"type": "error", "code": "too_large", "message": "The message is too large."}` without a `request_id`, because it does not know which turn the message is for, and the turn does not start. A client must not send one: the answer would not tell it which turn was left unstarted. A question within its limit can only exceed it if most of it is control characters, which JSON writes with 6 characters each.

### Server → client

On connecting: `{"type": "hello", "version": string, "providers": [ProviderStatus], "fx": FxRate, "active_turns": [{"request_id", "conversation_id" (null until the turn.started of a new conversation), "last_seq"}]}`. `version` is the version of the `agentic-os` package, the same one `agentic-os --version` shows. The `detail` of the `providers` is in the connection's language. `active_turns` only includes the running turns, and not those of a deleted conversation.

Every event of a turn carries `request_id` and `seq` (an integer that grows within the turn, starting at 1). The server keeps the events of the running turns and of those that ended less than 5 minutes ago: `turn.subscribe` replays the ones with `seq > after_seq` and then continues live; if the turn does not exist, or its conversation has been deleted, it answers `{"type": "turn.unknown", "request_id"}`. A `turn.cancel` for a turn the server does not have (it never existed, it ended more than 5 minutes ago, or it belonged to a deleted conversation and has already ended) also gets `turn.unknown`; one for a turn that has already ended, but is still kept, gets no answer. A connection gets each turn only once: a `turn.subscribe` for a turn the connection already gets (because it started it or has already subscribed to it) is ignored, without an answer, since the connection already has every event from the first `after_seq`. **A turn continues even if the connection drops**; only `turn.cancel` stops it (and `turn.stop`, after the round, a Refine turn). A turn's texts are in the language of the client that started it, also for a connection that subscribes to it.

| `type` | Fields | Meaning |
| --- | --- | --- |
| `turn.started` | `conversation_id`, `turn_id`, `mode`, `new_conversation` | Question stored |
| `phase` | `phase` (`answer`, `revision`, `synthesis`, `compaction`, `review`, `edit`), `round` | Phase change (`compaction` can arrive before `turn.started`). `review` and `edit` are the two parts of a round of a Refine turn: the reviews of the current version and the editor's new version |
| `stream.started` | `stream_id`, `agent`, `kind`, `round`, `model` | A model starts answering |
| `stream.delta` | `stream_id`, `section` (`text`, `critique`, `answer`), `text` | A fragment of text. In a Refine turn, `critique` is the changes a review proposes or the changelog of a version, and `answer`, the text of the version |
| `pdf.check` | `attachment_id`, `name`, `state` (`checking`, `checked`, `unchecked`), `claude_pages`, `hidden_pages`, `unchecked_pages` (page numbers, as in `PdfReading`), `reused`, `usage` (`Usage` or `null`), `reason` (text or `null`) | Claude checks a PDF of the question for ChatGPT on the subscription (see *Claude's check* under [Attachments](#attachments)). `checking` when Claude starts checking it, before ChatGPT's first call, which waits for it (there is none if the check was already stored, if it cannot be done, or if time runs out before it starts: then only the final one arrives); then `checked` (at least one page checked) or `unchecked` (none: the check failed, Claude declined to do it or took too long, the PDF could not be analysed, there is no Claude, or it is the demo one). `reused`: the check of an earlier turn, without any call in this one. `usage`: what this turn's calls have billed for this PDF, with the cost (`null` while checking, and if reused). `reason`: why pages are left unchecked, in the language of the client that started the turn (`null` if none is). If the turn is cancelled or fails while Claude is checking a PDF, no final `pdf.check` arrives for that PDF: the client takes it as interrupted. It is one of the turn's events (with `seq`, and replayed by `turn.subscribe`) |
| `stream.completed` | `stream_id`, `message_id`, `usage`, `latency_ms`, `ttft_ms`, `agreement`, `unchanged`, `cost_basis`; optional: `truncated` (only when it is `true`), `finish_reason`, `unchanged_note`, `pdf_reading` and `refine` (only when present) | Answer finished and stored. With `truncated: true` it is a truncated answer, and `finish_reason` says why. `unchanged_note` is the short note of an `UNCHANGED` revision. `pdf_reading` (`PdfReading[]`) says how ChatGPT read the PDFs when it cannot open them. `refine` is the message's `meta.refine` in a Refine turn: a review, a version (and whether it was accepted, with its `reason` and `reason_code`) or the final answer. They all have the same values as the fields of the stored message's `meta`, so the live view and the reloaded one match |
| `stream.failed` | `stream_id`, `error: {kind, message}`; optional: `usage` | That model has failed (the turn can continue with the other one). A refusal of the model arrives as `kind: "invalid"` with its own message; any text emitted before it is not stored. `usage` is what the failed call billed, with the cost (a refusal, an empty answer, the output limit used up without any text); it is only present when some billing is known. In a Refine turn, a review without the list of changes it was asked for (it does not have the changes section, or it proposes no change in the requested format and does not say `UNCHANGED` either) fails with `kind: "invalid"` and `The review does not have the list of changes it was asked for.`: that agent does not count in the round |
| `refine.round` | `round`, `version`, `accepted`, `reason`, `reason_code`, `words`, `budget_words`, `changes`, `proposals`, `scores`, `converged`, `usage`, `total` | End of a round of a Refine turn, from round 1 on. `version`: the current version after the round. `accepted`: the round has written a new version, which is now the current one. `reason`: why not, as a text in the language of the client that started the turn: `The new version went over the word limit.`, `The editor wrote no complete version.`, `The new version is the same as the previous one.`, `Neither of them found anything to change.` or, when no review came back or no editor could answer, `The models failed and the round wrote no version.` (and the turn ends with `stop_reason: "failed"`); `null` when the round wrote a version. `reason_code`: the same reason as a code, for the client's logic: `over_budget`, `incomplete`, `identical`, `nothing_to_change` or `failed_round`, respectively; `null` when the round wrote a version. `words`: the words of the current version; `budget_words`: the turn's limit. `changes`: the changelog of the new version (`[{kind, text}]`; empty if there is none). `proposals` and `scores` (`{"claude", "chatgpt"}`): how many changes each agent's review proposed, and what score from 0 to 100 it gave (`null` without a review, and the score also if the review gave no valid one; in round 1, the merge, always `null`). `converged`: the round meets the convergence rule, and the turn stops. `usage`: what the round's calls billed; `total`: what the turn has billed so far |
| `turn.stopping` | `round` | The Refine turn will stop at the end of `round`: the current round, or round 1 if it has not finished any round yet (version 1 is always written). It is the answer to `turn.stop`, only once |
| `turn.completed` | `conversation_id`, `turn_id`, `final_message_ids`, `usage`, `savings`, `consensus`, `cached`; optional: `stop_reason` (only in a Refine turn) | Turn finished. `stop_reason` says why a Refine turn ended with the last version (see [Turn outcome](#turn-outcome-metaoutcome-of-the-question)) |
| `turn.failed` | `error: {kind, message}`, with `attachment_id` when the error is an attachment that no longer exists; `usage` | Turn aborted. `message` is in the language of the client that started the turn; `attachment_id` lets the client act on the error without reading it. `usage` is the turn's total until then, the same as `outcome.usage` (zero if it failed before any call) |
| `turn.cancelled` | `usage` | Cancelled by the user (or because the server is shutting down). `usage` is what the turn had spent, the same as `outcome.usage`. It arrives when the turn has stopped and stored its outcome; a repeated `turn.cancel` in the meantime does nothing |

Two rounds of a Refine turn: round 2 writes version 2, and in round 3 the editor's new version goes over the word limit, so version 2 stays the current one:

```jsonc
{"type": "refine.round", "request_id": "uuid", "round": 2, "version": 2, "accepted": true,
 "reason": null, "reason_code": null, "words": 512, "budget_words": 600,
 "changes": [{"kind": "defect", "text": "…"}, {"kind": "clarity", "text": "…"}],
 "proposals": {"claude": 2, "chatgpt": 1}, "scores": {"claude": 72, "chatgpt": 70},
 "converged": false,
 "usage": {"input_tokens": 2400, "output_tokens": 3100, "cache_read_tokens": 18000,
           "cache_write_tokens": 6000, "reasoning_tokens": 900, "cost_usd": 0.19},
 "total": {"input_tokens": 6900, "output_tokens": 9800, "cache_read_tokens": 41000,
           "cache_write_tokens": 15000, "reasoning_tokens": 2600, "cost_usd": 0.55},
 "seq": 31}
{"type": "refine.round", "request_id": "uuid", "round": 3, "version": 2, "accepted": false,
 "reason": "The new version went over the word limit.", "reason_code": "over_budget",
 "words": 512, "budget_words": 600, "changes": [],
 "proposals": {"claude": 1, "chatgpt": 2}, "scores": {"claude": 85, "chatgpt": 80},
 "converged": false,
 "usage": {"input_tokens": 2700, "output_tokens": 4300, "cache_read_tokens": 21000,
           "cache_write_tokens": 5500, "reasoning_tokens": 1200, "cost_usd": 0.24},
 "total": {"input_tokens": 9600, "output_tokens": 14100, "cache_read_tokens": 62000,
           "cache_write_tokens": 20500, "reasoning_tokens": 3800, "cost_usd": 0.79},
 "seq": 44}
```

Others: `{"type": "pong", "t"}` (returns the same `t`) and `{"type": "error", "code", "message", "request_id"?}` for invalid messages or limits (`code`: `invalid`, `busy`, `duplicate`, `unavailable`, `too_large`, `internal`; `request_id` when a `turn.start` or a `turn.stop` is rejected), with `message` in the connection's language. A `conversation_id` outside the range 1 – 2^63 − 1 gives `invalid`. A message with text that cannot be encoded in UTF-8 (a lone surrogate, `\ud800`, in any key or value) gives `invalid` with `The message contains text that is not valid UTF-8.` and the `request_id` if that one is valid; nothing in the message is used.

`savings` = `{"cache", "compaction", "early_stop", "unchanged", "total", "cost_usd"}` (the estimated processed tokens saved and their approximate value: the kept answers at the output price of their model, the compaction at the input price of the calls that carried the context, the skipped rounds at the average cost of the turn's revisions, and a cache hit at the cost of the whole original turn; each call, and each attempt declined before a fallback, at the current rates of its model; `null` if none of them can be priced). `consensus` = `{"reached": bool, "round": int, "scores": {"claude": int, "chatgpt": int}}`, or `null` outside the debate mode.

### Typical order of a debate

1. `turn.started` → `phase(answer, 0)` → two `stream.started` (Claude and ChatGPT in parallel) with their `stream.delta` (`section: "text"`) and `stream.completed`. If ChatGPT runs on the subscription and the question carries PDFs, the `pdf.check` events of each PDF (`checking` and, when the check ends, `checked` or `unchecked`) arrive before ChatGPT's `stream.started`, while Claude is already answering.
2. For each round `r`: `phase(revision, r)` → two streams with `section` `critique` and then `answer`; `stream.completed` carries `agreement`.
3. If both reach the consensus threshold, the rounds stop (`early_stop` saving).
4. `phase(synthesis, r)` → a stream from the synthesizer agent → `turn.completed`.

### Typical order of a Refine turn

The two AIs improve a single document, round after round ([ADR 0010](adr/0010-refine-mode.md); the loop is in [ARCHITECTURE.md](ARCHITECTURE.md#turn-modes)):

1. `turn.started` → `phase(answer, 0)` → the two answers to the brief, in parallel, as in a debate.
2. `phase(edit, 1)` → the editor (`editor`, or the other agent if the editor has failed) merges the answers into version 1: a stream with `section` `answer` (the version) and then `critique` (the changelog), and the `stream.completed` with `refine`. If the merge goes over the owner's word limit, another stream of the same round is the attempt to shorten it, which is version 1 even if it still goes over → `refine.round` of round 1.
3. For each round `k` from 2 on: `phase(review, k)` → the reviews of the current version (`stream.completed` with `refine`, `role: "review"`). If any review proposes changes, `phase(edit, k)` → the editor's new version (and, if it goes over the word limit, another stream: the attempt to shorten it) → `refine.round` of round `k`. If one of the two models fails, the other one continues alone.
4. Before each round from 2 on, the turn ends if the owner has asked for it (`turn.stop`), if it has already spent the budget or if it has already done the rounds of `max_rounds`; and at the end of a round, if neither model has found anything to change for 2 rounds in a row, or if it meets the convergence rule (see `stop_reason` in [Turn outcome](#turn-outcome-metaoutcome-of-the-question)). The budget does not count the calls of models without a price.
5. The final answer, the current version stored without any call → `turn.completed` with `stop_reason`.

If the owner asks to stop it, `turn.stopping` arrives in between, only once. A Refine turn never uses the turn cache.
