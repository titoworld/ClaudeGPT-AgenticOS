"""Turn a GenerationRequest into provider-ready input.

- ``render_transcript``: a single text block, for cli providers whose process
  receives one user message per call.
- ``to_chat_messages``: alternating user/assistant messages for api providers,
  append-only across turns so the vendor prompt cache keeps hitting.

Text embedded inside the XML-like sections (history, summaries, the other agent's
answer...) goes through ``neutralize_tags`` first, so it cannot close a section or
forge an owner's message such as ``<message from="User">``, not even with the tag name
split by invisible characters (a U+200B zero-width space inside ``claude_answer``) or
written in full-width letters.

Attachments (docs/adr/0009-adjunts.md) travel before the text, each in a block of its
own: the file itself (an image, a PDF document) or, for a text file and for a PDF sent
as its extracted text, a text block (:func:`attachment_text`). A file's text is untrusted
like the rest: it goes through ``neutralize_tags``, between an opening line that ends
with a code, ``[Fitxer: <name> · <code>]``, and the closing line ``[Fi del fitxer
<code>]``. The code (:func:`file_code`) hashes the content's SHA-256 and the name, so
neither the file nor its name can hold it (each would have to contain its own hash) and a
forged end of file is told apart; it is deterministic, so the prompt caches keep hitting.
ChatGPT's page-by-page view of a PDF (:func:`pdf_view`) and Claude's check of its text
have codes of their own, seeded apart (:func:`view_code`, :func:`check_code`): only
ChatGPT ever sees the view's. The prompt lists the attachments by :func:`attachment_label`.
"""

from __future__ import annotations

import asyncio
import hashlib
import re
import unicodedata
from collections.abc import Sequence
from typing import Final, Literal

from agentic_os.domain import AgentName
from agentic_os.pdf_facts import PageFinding, PdfPage
from agentic_os.providers.base import Attachment, ChatTurn, GenerationRequest

AGENT_LABELS: dict[AgentName, str] = {"claude": "Claude", "chatgpt": "ChatGPT"}

ATTACHMENT_KIND_LABELS: dict[str, str] = {"image": "imatge", "pdf": "PDF", "text": "fitxer de text"}
"""Catalan name of each attachment kind, in labels and history references."""
TEXT_ONLY_NOTE = "només el text extret"
"""Label note of a PDF that a call gets as its extracted text instead of the document."""
PDF_WITHOUT_TEXT = "[No se n'ha pogut extreure el text d'aquest PDF.]"
"""Body of a PDF sent as text when the server could not extract any."""

ChatRole = Literal["user", "assistant"]

RESERVED_TAGS: frozenset[str] = frozenset(
    {
        # render_transcript and to_chat_messages
        "conversation_summary",
        "conversation_history",
        "message",
        "current_message",
        # orchestrator/prompts.py templates (a transcript may embed them)
        "user_message",
        "question",
        "your_previous_answer",
        "claude_answer",
        "chatgpt_answer",
        "critique",
        "answer",
        "agreement",
        "attachments",
    }
)
"""Every tag name the app's prompts use as a delimiter."""

IGNORABLE_CHARS = (
    r"\u00ad\u034f\u061c\u115f\u1160\u17b4\u17b5\u180b-\u180f\u200b-\u200f"
    r"\u202a-\u202e\u2060-\u206f\u3164\ufe00-\ufe0f\ufeff\uffa0\ufff0-\ufff8"
    r"\U0001bca0-\U0001bca3\U0001d173-\U0001d17a\U000e0000-\U000e0fff"
)
"""Regex character-class body of Unicode's Default_Ignorable_Code_Point characters:
invisible format characters (zero-width spaces and joiners, soft hyphen, bidi controls,
word joiner, BOM, variation selectors, tag characters...) that nobody sees on screen."""

_IGNORABLE_RE = re.compile(f"[{IGNORABLE_CHARS}]")
_NAME_CHARS = r"\w\-\uff0d\uff3f"  # full-width "-" and "_" are not \w
_TAG_OPENING_RE = re.compile(
    # "<" (or its full-width and small forms), an optional "/", then the tag name;
    # spaces and invisible characters may come anywhere, even inside the name.
    rf"[<\uff1c\ufe64](?=[\s{IGNORABLE_CHARS}]*(?:[/\uff0f][\s{IGNORABLE_CHARS}]*)?"
    rf"([{_NAME_CHARS}][{_NAME_CHARS}{IGNORABLE_CHARS}]*))"
)


def _escape_reserved(match: re.Match[str]) -> str:
    name = unicodedata.normalize("NFKC", _IGNORABLE_RE.sub("", match.group(1))).casefold()
    return "&lt;" if name in RESERVED_TAGS else match.group(0)


def neutralize_tags(text: str) -> str:
    """Escape the ``<`` of reserved tags in untrusted text (``</answer>`` becomes
    ``&lt;/answer>``): still readable, but it can no longer close or open a section.

    Tag names are compared ignoring case, invisible characters (``IGNORABLE_CHARS``)
    and compatibility forms (NFKC): a zero-width space inside ``claude_answer``, a
    full-width ``<`` (U+FF1C) or full-width letters do not get past it. The text itself
    is kept as is, other markup (``<div>``, ``a < b``) is left alone, and the result is
    deterministic, so re-rendered history stays byte-identical for the prompt caches."""
    return _TAG_OPENING_RE.sub(_escape_reserved, text)


def pages_label(pages: int) -> str:
    return "1 pàgina" if pages == 1 else f"{pages} pàgines"


def attachment_label(
    name: str, kind: str, pages: int | None = None, *, text_only: bool = False
) -> str:
    """How an attachment is named to the models: «informe.pdf (PDF, 12 pàgines)»,
    «foto.jpg (imatge)», «notes.md (fitxer de text)»; a PDF that a call gets as its
    extracted text says so («informe.pdf (PDF, 12 pàgines; només el text extret)»)."""
    details = ATTACHMENT_KIND_LABELS.get(kind, "fitxer")
    if kind == "pdf" and pages:
        details += f", {pages_label(pages)}"
    if text_only:
        details += f"; {TEXT_ONLY_NOTE}"
    return f"{name} ({details})"


def label_of(attachment: Attachment) -> str:
    """:func:`attachment_label` of an attachment as this call delivers it."""
    return attachment_label(
        attachment.name,
        attachment.kind,
        attachment.pages,
        text_only=attachment.kind == "pdf" and attachment.mode == "text",
    )


def sends_file(attachment: Attachment) -> bool:
    """Whether a call sends the stored file itself: an image, or a PDF in mode "full"
    (a text file, or a PDF in mode "text", goes as :func:`attachment_text`)."""
    return attachment.kind == "image" or (attachment.kind == "pdf" and attachment.mode == "full")


def has_text(attachment: Attachment) -> bool:
    """Whether an attachment has any stored text: a text file's content, or the text the
    server extracted from a PDF (a scanned PDF has none)."""
    return attachment.text is not None and bool(attachment.text.strip())


FILE_CODE_LENGTH: Final = 16
"""Hexadecimal digits of :func:`file_code` (64 bits: a file or name that held its own
code would take about 2^64 tries to make)."""


def file_code(attachment: Attachment) -> str:
    """The code that opens and closes an attachment's text (:func:`enclosed`): the first
    :data:`FILE_CODE_LENGTH` hexadecimal digits of the SHA-256 of the content's SHA-256
    and the name. Neither the file (or the text extracted from it) nor its name can hold
    it, since each would have to contain its own hash; the same file and name always get
    the same code, so the prompts stay byte-identical for the vendors' caches."""
    seed = f"{attachment.sha256}\n{attachment.name}".encode()
    return hashlib.sha256(seed).hexdigest()[:FILE_CODE_LENGTH]


def _enclose(opening: str, body: str, code: str) -> str:
    """:func:`enclosed` with this ``code``."""
    text = neutralize_tags(body)
    end = "" if text.endswith("\n") else "\n"
    return f"[{opening} · {code}]\n{text}{end}[Fi del fitxer {code}]\n"


def enclosed(opening: str, body: str, attachment: Attachment) -> str:
    """An attachment's text as the models get it: the line ``[<opening> · <code>]``, the
    body with every tag of the app's prompts neutralized (``neutralize_tags``) and the
    line ``[Fi del fitxer <code>]``, with the attachment's :func:`file_code` and a line
    break (Codex joins its text items: what follows starts on a line of its own). The
    body cannot close the block or open a section of the prompt: whatever it says, it
    stays the file's content."""
    return _enclose(opening, body, file_code(attachment))


def attachment_text(attachment: Attachment) -> str:
    """The text block of a text file, or of a PDF sent as its extracted text (a notice
    when there is none): :func:`enclosed` under ``Fitxer: <name>``."""
    body = attachment.text or ""
    if attachment.kind == "pdf" and not has_text(attachment):
        body = PDF_WITHOUT_TEXT
    return enclosed(f"Fitxer: {neutralize_tags(attachment.name)}", body, attachment)


def check_code(attachment: Attachment) -> str:
    """The code that encloses a PDF's text in Claude's check prompt: another one than
    :func:`file_code` and :func:`view_code`, seeded apart."""
    seed = f"check\n{attachment.sha256}\n{attachment.name}".encode()
    return hashlib.sha256(seed).hexdigest()[:FILE_CODE_LENGTH]


def view_code(attachment: Attachment) -> str:
    """The code of ChatGPT's view of a PDF (:func:`pdf_view`), seeded apart from
    :func:`file_code`, which Claude sees whenever a call gets the PDF as its text (the
    debate's revisions), and from :func:`check_code`: only ChatGPT ever sees it, so
    nothing Claude writes, whatever the PDF asks of it, can carry a real page line or
    note of the view. Deterministic, like the others, for the prompt caches."""
    seed = f"view\n{attachment.sha256}\n{attachment.name}".encode()
    return hashlib.sha256(seed).hexdigest()[:FILE_CODE_LENGTH]


PDF_VIEW_LEGEND: Final = (
    "Cada pàgina comença amb una línia «[Pàgina N · {code}...]». Quan aquesta línia diu que "
    "el text és de Claude, l'ha llegit Claude al PDF perquè el text extret hi falta o no és "
    "fiable; la resta és el text extret del fitxer."
)
"""First line of ChatGPT's view of a PDF: how to read its page lines."""
PAGE_WITHOUT_TEXT: Final = "(sense text extraïble)"


def _page_view(
    attachment: Attachment, page: PdfPage, finding: PageFinding | None, checked: bool, code: str
) -> str:
    """One page of :func:`pdf_view`: its line and its text as ChatGPT gets it."""
    number = page.number
    stored = ""
    if attachment.text is not None and page.start is not None and page.end is not None:
        stored = attachment.text[page.start : page.end].strip()
    if finding is not None and finding.status in ("missing", "garbled"):
        why = (
            "la pàgina no té text extraïble"
            if finding.status == "missing"
            else "el text extret de la pàgina no és llegible"
        )
        head = f"[Pàgina {number} · {code}: text de Claude, que l'ha llegit al PDF perquè {why}]"
        body = finding.text
    elif finding is not None and finding.status == "hidden":
        head = (
            f"[Pàgina {number} · {code}: text visible segons Claude; la pàgina té text que no es "
            "veu i no s'ha passat]"
        )
        body = finding.text or "(la pàgina no mostra cap text)"
    else:
        notes: list[str] = []
        if not checked:
            notes.append("sense contrastar")
            if page.hidden:
                notes.append("pot tenir text que no es veu")
        if page.cut:
            notes.append("text retallat pel límit del servidor")
        head = f"[Pàgina {number} · {code}{': ' + '; '.join(notes) if notes else ''}]"
        body = stored or PAGE_WITHOUT_TEXT
        if finding is not None and finding.status == "partial":
            body += (
                f"\n[Complement de Claude · {code}: text de la pàgina que l'extracció no recull]\n"
                f"{finding.text}"
            )
    if finding is not None and finding.visual:
        body += (
            f"\n[Descripció de Claude · {code}: què mostren les figures, taules o imatges]\n"
            f"{finding.visual}"
        )
    return f"{head}\n{body}"


def pdf_view(attachment: Attachment) -> str:
    """What ChatGPT with the subscription reads of a PDF (Codex cannot open one), enclosed
    as a file's text: page by page, the text the server extracted, and where Claude's
    check (``attachment.pdf_check``) found it missing, unreadable, incomplete or with text
    that is not visible, Claude's reading instead, marked. Its opening, its page lines
    and its end carry the view's own code (:func:`view_code`), which only ChatGPT sees,
    so neither the PDF nor Claude can forge one. Pages that were not checked say so. A
    PDF that was not analysed (no ``pdf_pages``) is its extracted text, unchecked."""
    name = neutralize_tags(attachment.name)
    pages = attachment.pdf_pages
    code = view_code(attachment)
    if pages is None:
        if not has_text(attachment):
            return f"[PDF «{name}»: no se n'ha pogut extreure el text]\n"
        count = f", {pages_label(attachment.pages)}" if attachment.pages else ""
        return _enclose(
            f"PDF «{name}»{count}: text extret pel servidor, sense contrastar",
            attachment.text or "",
            code,
        )
    check = attachment.pdf_check
    covered = check.covered if check is not None else 0
    if covered <= 0:
        state = "text extret pel servidor, sense contrastar"
    elif covered >= len(pages):
        state = "text extret pel servidor i contrastat per Claude"
    else:
        state = (
            f"text extret pel servidor i contrastat per Claude fins a la pàgina {covered} "
            "(la resta, sense contrastar)"
        )
    blocks = [PDF_VIEW_LEGEND.format(code=code)]
    for page in pages:
        finding = check.finding(page.number) if check is not None else None
        blocks.append(_page_view(attachment, page, finding, page.number <= covered, code))
    header = f"PDF «{name}», {pages_label(len(pages))}: {state}"
    return _enclose(header, "\n\n".join(blocks), code)


async def read_files(attachments: Sequence[Attachment]) -> list[bytes | None]:
    """The stored bytes of the attachments a call sends as files (:func:`sends_file`),
    None for the others; read and checked in a worker thread (a ``ProviderError`` if a
    file is missing or changed)."""

    def read_all() -> list[bytes | None]:
        return [attachment.read() if sends_file(attachment) else None for attachment in attachments]

    if not any(sends_file(attachment) for attachment in attachments):
        return [None] * len(attachments)
    return await asyncio.to_thread(read_all)


def _speaker(turn: ChatTurn) -> str:
    if turn.role == "user":
        return "User"
    return AGENT_LABELS[turn.agent] if turn.agent else "Assistant"


def _summary_section(summary: str) -> str:
    return f"<conversation_summary>\n{neutralize_tags(summary)}\n</conversation_summary>"


def render_transcript(request: GenerationRequest) -> str:
    """Summary, history and the current message as one text. ``request.prompt`` is
    embedded as is: the orchestrator composes it (its templates neutralize what they
    embed), and in a solo turn it is the owner's own message."""
    parts: list[str] = []
    if request.context_summary:
        parts.append(_summary_section(request.context_summary))
    if request.history:
        messages = "\n".join(
            f'<message from="{_speaker(turn)}">\n{neutralize_tags(turn.content)}\n</message>'
            for turn in request.history
        )
        parts.append(f"<conversation_history>\n{messages}\n</conversation_history>")
    if not parts:
        return request.prompt
    parts.append(f"<current_message>\n{request.prompt}\n</current_message>")
    return "\n\n".join(parts)


def to_chat_messages(request: GenerationRequest, agent: AgentName) -> list[tuple[ChatRole, str]]:
    """Messages as seen by ``agent``: the other agent's answers are labelled."""
    raw: list[tuple[ChatRole, str]] = []
    if request.context_summary:
        raw.append(("user", _summary_section(request.context_summary)))
    for turn in request.history:
        if turn.role == "user":
            raw.append(("user", turn.content))
        elif turn.agent is None or turn.agent == agent:
            raw.append(("assistant", turn.content))
        else:
            raw.append(("assistant", f"[{AGENT_LABELS[turn.agent]}]\n{turn.content}"))
    raw.append(("user", request.prompt))

    merged: list[tuple[ChatRole, str]] = []
    for role, content in raw:
        if merged and merged[-1][0] == role:
            merged[-1] = (role, f"{merged[-1][1]}\n\n{content}")
        else:
            merged.append((role, content))
    if merged[0][0] == "assistant":
        merged.insert(0, ("user", "(continuació de la conversa)"))
    return merged
