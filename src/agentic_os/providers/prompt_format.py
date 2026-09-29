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
The prompt lists the attachments by :func:`attachment_label`.
"""

from __future__ import annotations

import asyncio
import hashlib
import re
import unicodedata
from collections.abc import Sequence
from typing import Final, Literal

from agentic_os.domain import AgentName
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


def enclosed(opening: str, body: str, attachment: Attachment) -> str:
    """An attachment's text as the models get it: the line ``[<opening> · <code>]``, the
    body with every tag of the app's prompts neutralized (``neutralize_tags``) and the
    line ``[Fi del fitxer <code>]``, with the attachment's :func:`file_code` and a line
    break (Codex joins its text items: what follows starts on a line of its own). The
    body cannot close the block or open a section of the prompt: whatever it says, it
    stays the file's content."""
    code = file_code(attachment)
    text = neutralize_tags(body)
    end = "" if text.endswith("\n") else "\n"
    return f"[{opening} · {code}]\n{text}{end}[Fi del fitxer {code}]\n"


def attachment_text(attachment: Attachment) -> str:
    """The text block of a text file, or of a PDF sent as its extracted text (a notice
    when there is none): :func:`enclosed` under ``Fitxer: <name>``."""
    body = attachment.text or ""
    if attachment.kind == "pdf" and not has_text(attachment):
        body = PDF_WITHOUT_TEXT
    return enclosed(f"Fitxer: {neutralize_tags(attachment.name)}", body, attachment)


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
