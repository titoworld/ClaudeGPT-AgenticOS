"""Deterministic fake provider: tests and the no-cost demo (``AOS_*_MODE=fake``).

Replies are canned Catalan Markdown built from the question, streamed in small
chunks. Revisions follow the debate format (critique, answer or UNCHANGED,
agreement) with agreement values taken from a configurable sequence, which
restarts for every new question.
"""

from __future__ import annotations

import asyncio
import hashlib
import re
import time
from collections.abc import AsyncIterator, Sequence

from agentic_os.domain import AgentName, ProviderMode, Purpose, Usage, other_agent
from agentic_os.providers.base import (
    GenerationRequest,
    GenerationResult,
    ProviderError,
    ProviderEvent,
    ProviderStatus,
    TextDelta,
)
from agentic_os.providers.prompt_format import AGENT_LABELS

DEFAULT_AGREEMENTS: tuple[int, ...] = (72, 90)
UNCHANGED_FROM = 85
"""Agreement from which the fake keeps its previous answer (UNCHANGED)."""

_TAGGED = {
    "question": re.compile(r"<question>\n(.*?)\n</question>", re.DOTALL),
    "user_message": re.compile(r"<user_message>\n(.*?)\n</user_message>", re.DOTALL),
    "previous": re.compile(r"<your_previous_answer>\n(.*?)\n</your_previous_answer>", re.DOTALL),
}
_WORD_RE = re.compile(r"\S+\s*|\s+")


def _estimate(text: str) -> int:
    return max(1, -(-len(text) // 4)) if text else 0


def _topic(question: str) -> str:
    line = next((ln.strip() for ln in question.splitlines() if ln.strip()), "la teva pregunta")
    return line if len(line) <= 80 else line[:79].rstrip() + "…"


def _variant(*parts: str) -> int:
    return hashlib.sha256("\x00".join(parts).encode("utf-8")).digest()[0]


def _chunks(text: str) -> list[str]:
    """Split into small pieces of one or two words (whitespace preserved)."""
    words = _WORD_RE.findall(text)
    return ["".join(words[i : i + 2]) for i in range(0, len(words), 2)]


class FakeProvider:
    """Provider that never leaves the process. ``requests`` and ``prewarmed`` record
    every call, which tests use to check what the engine sent."""

    def __init__(
        self,
        agent: AgentName,
        *,
        chunk_delay: float = 0.015,
        agreements: Sequence[int] | None = None,
        fail: set[Purpose] | None = None,
    ) -> None:
        self._agent: AgentName = agent
        self._chunk_delay = chunk_delay
        self._agreements = tuple(agreements) if agreements else DEFAULT_AGREEMENTS
        self._fail = frozenset(fail or ())
        self._revisions = 0
        self._revision_question: str | None = None
        self.requests: list[GenerationRequest] = []
        self.prewarmed: list[GenerationRequest] = []
        self.closed = False

    @property
    def agent(self) -> AgentName:
        return self._agent

    @property
    def mode(self) -> ProviderMode:
        return "fake"

    @property
    def model(self) -> str:
        return f"fake-{self._agent}"

    async def stream(self, request: GenerationRequest) -> AsyncIterator[ProviderEvent]:
        self.requests.append(request)
        started = time.monotonic()
        if request.purpose in self._fail:
            await asyncio.sleep(self._chunk_delay)
            raise ProviderError(
                f"{AGENT_LABELS[self._agent]} (demostració) ha fallat a propòsit.",
                kind="unavailable",
            )
        text = self._compose(request)
        ttft_ms: int | None = None
        for chunk in _chunks(text):
            await asyncio.sleep(self._chunk_delay)
            if ttft_ms is None:
                ttft_ms = int((time.monotonic() - started) * 1000)
            yield TextDelta(chunk)
        prompt_tokens = _estimate(request.system) + _estimate(request.prompt)
        prompt_tokens += sum(_estimate(turn.content) for turn in request.history)
        prompt_tokens += _estimate(request.context_summary or "")
        yield GenerationResult(
            text=text,
            usage=Usage(input_tokens=prompt_tokens, output_tokens=_estimate(text)),
            model=self.model,
            latency_ms=int((time.monotonic() - started) * 1000),
            ttft_ms=ttft_ms,
        )

    async def prewarm(self, request: GenerationRequest) -> None:
        self.prewarmed.append(request)

    async def status(self) -> ProviderStatus:
        return ProviderStatus(
            agent=self._agent,
            mode="fake",
            available=True,
            model=self.model,
            detail="Mode demostració",
        )

    async def aclose(self) -> None:
        self.closed = True

    # -- canned replies ------------------------------------------------------------

    def _compose(self, request: GenerationRequest) -> str:
        if request.purpose == "summary":
            return self._summary(request)
        question = self._question(request.prompt)
        if request.purpose == "revision":
            return self._revision(request.prompt, question)
        if request.purpose == "synthesis":
            return self._synthesis(question)
        return self._answer(question)

    @staticmethod
    def _question(prompt: str) -> str:
        for name in ("question", "user_message"):
            match = _TAGGED[name].search(prompt)
            if match:
                return match.group(1)
        return prompt

    def _answer(self, question: str) -> str:
        topic = _topic(question)
        mode = self._agent.upper()
        if self._agent == "claude":
            intro = (
                "Vet aquí com ho enfocaria",
                "Ho resumiria en tres passos",
                "La clau és anar per parts",
            )[_variant(question, "claude") % 3]
            return (
                f"### {topic}\n\n"
                f"{intro}:\n\n"
                "1. **Defineix l'objectiu.** Concreta què vols aconseguir i quines "
                "restriccions tens (temps, pressupost, eines).\n"
                "2. **Comença pel camí més senzill.** Prova la solució mínima que funcioni "
                "i mesura'n el resultat abans d'afegir complexitat.\n"
                "3. **Revisa els casos límit.** Pensa què pot fallar i com ho detectaràs.\n\n"
                f"> Resposta de demostració: configura `AOS_{mode}_MODE=cli` o `api` "
                "per parlar amb el model real."
            )
        tip = (
            "Documenta cada decisió en una línia",
            "Posa una data límit a cada prova",
            "Compara sempre amb una alternativa",
        )[_variant(question, "chatgpt") % 3]
        return (
            f"**Resposta ràpida:** sobre «{topic}», el més important és tenir clar el "
            "resultat esperat i avançar amb iteracions curtes.\n\n"
            "| Aspecte | Proposta |\n"
            "| --- | --- |\n"
            "| Primer pas | Escriure l'objectiu en una frase |\n"
            "| Eina | La més simple que resolgui el problema |\n"
            "| Validació | Una prova petita abans d'escalar |\n\n"
            f"- **Consell:** {tip}.\n"
            "- **Error habitual:** optimitzar abans de saber on és el coll d'ampolla.\n\n"
            f"*Resposta de demostració (`AOS_{mode}_MODE=fake`).*"
        )

    def _revision(self, prompt: str, question: str) -> str:
        if question != self._revision_question:
            # A new debate: the agreement sequence starts again.
            self._revision_question = question
            self._revisions = 0
        agreement = self._agreements[min(self._revisions, len(self._agreements) - 1)]
        self._revisions += 1
        other = AGENT_LABELS[other_agent(self._agent)]
        if agreement >= UNCHANGED_FROM:
            critique = f"- Cap error rellevant: la resposta de {other} és correcta i completa."
            answer = "UNCHANGED"
        else:
            critique = (
                f"- A la resposta de {other} li falta un exemple concret.\n"
                "- No diu com es comprova que la solució funciona."
            )
            previous_match = _TAGGED["previous"].search(prompt)
            previous = previous_match.group(1) if previous_match else self._answer(question)
            answer = (
                f"{previous}\n\n"
                f"**Després de revisar la resposta de {other}:** hi afegeixo un exemple. "
                "Si l'objectiu és reduir un procés de 10 minuts a 2, primer mesura'l, "
                "després aplica un sol canvi i torna a mesurar."
            )
        return (
            f"<critique>\n{critique}\n</critique>\n"
            f"<answer>\n{answer}\n</answer>\n"
            f"<agreement>{agreement}</agreement>"
        )

    @staticmethod
    def _synthesis(question: str) -> str:
        topic = _topic(question)
        return (
            f"## {topic}\n\n"
            "Claude i ChatGPT coincideixen en l'essencial:\n\n"
            "1. **Objectiu clar:** escriu en una frase què vols aconseguir.\n"
            "2. **Iteracions curtes:** comença per la solució més senzilla i mesura-la.\n"
            "3. **Validació:** prova els casos límit abans de donar-ho per bo.\n\n"
            "| Pas | Com saber que està fet |\n"
            "| --- | --- |\n"
            "| Objectiu | Està escrit i és mesurable |\n"
            "| Prototip | Funciona amb un cas real |\n"
            "| Revisió | Els errors habituals estan descartats |\n\n"
            "*Síntesi de demostració generada sense cap model real.*"
        )

    @staticmethod
    def _summary(request: GenerationRequest) -> str:
        questions = [_topic(turn.content) for turn in request.history if turn.role == "user"]
        listed = "; ".join(f"«{q}»" for q in questions) or "temes diversos"
        previous = f" {request.context_summary}" if request.context_summary else ""
        return f"Resum (demostració):{previous} L'usuari ha preguntat per {listed}."
