"""The language of every text the server writes for people to read
(docs/adr/0011-internationalization.md): English, Spanish or Catalan.

Each text has a key and its three translations in a catalog of :mod:`agentic_os.locales`;
:func:`t` gives it in the language in force:

- an HTTP request's: its ``Accept-Language`` header, which the web app sets to its own
  language (:class:`agentic_os.server.middleware.LanguageMiddleware`);
- a WebSocket connection's: its ``?lang=`` (the app's language when it connected), and so
  the turns it starts, whose tasks inherit it, and what they store;
- the command line's: the system locale (``LC_ALL``, ``LC_MESSAGES``, ``LANG``).

Without one, :data:`DEFAULT_LANG`. Texts are made in the language of whoever caused them
and stored as they are: never keep a translated text where another language may read it
later (a cache shared by every client), keep a :func:`lazy` text instead.
"""

from __future__ import annotations

import contextlib
import re
from collections.abc import Iterator, Mapping
from contextvars import ContextVar, Token
from dataclasses import dataclass
from string import Formatter
from typing import Final, Literal, TypedDict, get_args

Lang = Literal["en", "es", "ca"]
LANGS: Final[tuple[Lang, ...]] = get_args(Lang)

DEFAULT_LANG: Lang = "en"
"""The language of a text nothing chose one for. (The tests set Catalan, the language
they were written in: ``tests/conftest.py``.)"""


class Text(TypedDict):
    """A text in every language: ``{placeholders}`` in :meth:`str.format` syntax, the same
    ones in the three."""

    en: str
    es: str
    ca: str


_current: ContextVar[Lang | None] = ContextVar("agentic_os_lang", default=None)


def current() -> Lang:
    """The language in force (:data:`DEFAULT_LANG` when nothing chose one)."""
    return _current.get() or DEFAULT_LANG


def chosen() -> Lang | None:
    """The language something chose, or None (then texts use :data:`DEFAULT_LANG`)."""
    return _current.get()


def as_lang(value: object) -> Lang | None:
    """``value`` as a supported language, by its primary subtag: ``"es"``, ``"ca-ES"``,
    ``"en_GB.UTF-8"``... None for anything else."""
    if not isinstance(value, str):
        return None
    primary = re.split(r"[-_.@]", value.strip().lower(), maxsplit=1)[0]
    for lang in LANGS:
        if primary == lang:
            return lang
    return None


def from_accept_language(header: str | None) -> Lang | None:
    """The supported language an ``Accept-Language`` header prefers most (its ``q``
    weights honored, the first one listed on a tie), or None when it names none."""
    if not header:
        return None
    best: tuple[float, Lang] | None = None
    for item in header.split(",")[:32]:
        tag, *params = item.split(";")
        lang = as_lang(tag)
        if lang is None:
            continue
        weight = 1.0
        for param in params:
            name, _, value = param.partition("=")
            if name.strip().lower() == "q":
                try:
                    weight = min(1.0, max(0.0, float(value.strip())))
                except ValueError:
                    weight = 0.0
        if weight > 0 and (best is None or weight > best[0]):
            best = (weight, lang)
    return best[1] if best else None


def from_environ(environ: Mapping[str, str]) -> Lang | None:
    """The system locale's language, as POSIX resolves it (``LC_ALL``, then
    ``LC_MESSAGES``, then ``LANG``), or None (``C``, ``POSIX`` or another language)."""
    for name in ("LC_ALL", "LC_MESSAGES", "LANG"):
        value = environ.get(name)
        if value:
            return as_lang(value)
    return None


def set_lang(lang: Lang | None) -> Token[Lang | None]:
    """Use ``lang`` from now on in this context (None: :data:`DEFAULT_LANG`)."""
    return _current.set(lang)


def reset_lang(token: Token[Lang | None]) -> None:
    _current.reset(token)


@contextlib.contextmanager
def use(lang: Lang | None) -> Iterator[None]:
    """Use ``lang`` within the block."""
    token = _current.set(lang)
    try:
        yield
    finally:
        _current.reset(token)


def t(key: str, /, **params: object) -> str:
    """The text ``key`` in the language in force, with ``params`` in its placeholders."""
    from agentic_os.locales import MESSAGES

    text = MESSAGES[key][current()]
    return text.format(**params) if params else text


@dataclass(frozen=True, slots=True)
class Lazy:
    """A text made when it is shown, in the language in force then: what a cache shared by
    every client keeps (an agent's status, a model's description) instead of a text made
    in the language of whoever filled it. ``str()`` makes it."""

    key: str
    params: tuple[tuple[str, object], ...] = ()

    def __str__(self) -> str:
        return t(self.key, **dict(self.params))


def lazy(key: str, /, **params: object) -> Lazy:
    """The text ``key`` with ``params``, made only when it is shown (:class:`Lazy`)."""
    return Lazy(key, tuple(params.items()))


def placeholders(text: str) -> frozenset[str]:
    """The names of a text's ``{placeholders}``."""
    return frozenset(name for _, name, _, _ in Formatter().parse(text) if name)


_SEPARATORS: Final[Mapping[Lang, tuple[str, str]]] = {
    "en": (",", "."),
    "es": (".", ","),
    "ca": (".", ","),
}


def number(value: float, decimals: int = 0) -> str:
    """``value`` as the web app writes numbers in the language in force (its
    ``Intl.NumberFormat``): ``1,234.5`` in English, ``1.234,5`` in Catalan, and in Spanish
    without a separator below ten thousand (``1234,5``, ``12.345``)."""
    lang = current()
    thousands, point = _SEPARATORS[lang]
    text = f"{value:,.{decimals}f}"
    whole, _, fraction = text.partition(".")
    digits = whole.lstrip("-").replace(",", "")
    if lang == "es" and len(digits) <= 4:
        whole = whole.replace(",", "")
    whole = whole.replace(",", thousands)
    return f"{whole}{point}{fraction}" if fraction else whole
