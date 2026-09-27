"""System prompts and prompt templates of the council.

Everything here is byte-for-byte stable across turns (no timestamps, ids or
counters) so the vendors' prompt caches keep hitting: the system prompt is the
same for every purpose of an agent, and the variable parts of a template always
come last. Every prompt asks the model to answer in the language of the user.

The embedded texts (the question, the answers, the critiques) go through
``neutralize_tags``: another model's answer, or a page the owner pasted, cannot close
a section and pass itself off as the owner's instructions. Every tag used here must be
in ``RESERVED_TAGS`` (a test checks it).
"""

from __future__ import annotations

from collections.abc import Mapping

from agentic_os.domain import AGENTS, AgentName, other_agent
from agentic_os.providers.prompt_format import AGENT_LABELS, neutralize_tags

_IDENTITIES: dict[AgentName, str] = {
    "claude": "You are Claude, an AI assistant made by Anthropic.",
    "chatgpt": "You are ChatGPT, an AI assistant made by OpenAI.",
}

_BASE_SYSTEM = """{identity} You work together with {other} ({other_vendor}) as a \
two-assistant council serving a single owner: you answer the owner's questions, review \
each other's answers and combine them into the best possible answer.

Guidelines:
- Be accurate, concrete and concise. Prefer facts, numbers and examples over generalities.
- If you are not sure about something, say so briefly instead of guessing.
- Use Markdown when it helps readability (headings, lists, tables, code blocks).
- Start with the answer itself, without preamble or restating the question.
- Always answer in the language of the user's message."""

_VENDORS: dict[AgentName, str] = {"claude": "Anthropic", "chatgpt": "OpenAI"}

SYSTEM_PROMPTS: dict[AgentName, str] = {
    agent: _BASE_SYSTEM.format(
        identity=_IDENTITIES[agent],
        other=AGENT_LABELS[other_agent(agent)],
        other_vendor=_VENDORS[other_agent(agent)],
    )
    for agent in AGENTS
}

DEBATE_ANSWER_TEMPLATE = """Answer the user's message below. {other} is answering it \
independently and will then review your answer, so make it accurate and complete while \
staying concise. Answer in the language of the user's message.

<user_message>
{question}
</user_message>"""

REVISION_TEMPLATE = """You and {other} both answered the user's question below. Review \
{other}'s latest answer, then improve your own answer using anything valid from it.

Reply with exactly these three parts, in this order, and nothing else:

<critique>
At most 5 short bullets about real errors or important omissions in {other}'s answer \
(not style or wording). Write only None if there are none.
</critique>
<answer>
Your complete improved answer, ready to be shown to the user on its own. If your \
previous answer needs no change, write exactly UNCHANGED instead.
</answer>
<agreement>N</agreement>

N is a number from 0 to 100: how much you agree with {other}'s answer on substance.
Write the critique and the answer in the language of the user's question.

<question>
{question}
</question>

<your_previous_answer>
{own_answer}
</your_previous_answer>

<{other_tag}_answer>
{other_answer}
</{other_tag}_answer>"""

SYNTHESIS_TEMPLATE = """Claude and ChatGPT answered the user's question below and \
reviewed each other's answers. Write the single best final answer for the owner:
- Combine the strongest, correct points of both answers.
- Resolve disagreements: decide what is right; if something remains uncertain, say so \
briefly.
- Do not narrate the debate or mention the assistants unless that is useful to the owner.
- Reply with the final answer only, in the language of the user's question.

<question>
{question}
</question>

{answers}"""

SUMMARY_PROMPT = """Summarize the conversation above, including any earlier summary, so \
that the summary can replace it as context for the rest of the conversation.
- Be dense and factual: keep decisions, conclusions, names, numbers, code identifiers, \
preferences stated by the user and open questions.
- When the assistants disagreed, keep only the final position and any unresolved point.
- Drop greetings, repetition and formatting.
- At most about 300 words, in the same language as the conversation.
Reply with the summary only."""


def system_prompt(agent: AgentName) -> str:
    """The agent's system prompt, identical for every purpose (best cache reuse)."""
    return SYSTEM_PROMPTS[agent]


def debate_answer_prompt(agent: AgentName, question: str) -> str:
    """Round-0 prompt of a debate: the question, knowing the other agent will review it."""
    return DEBATE_ANSWER_TEMPLATE.format(
        other=AGENT_LABELS[other_agent(agent)], question=neutralize_tags(question)
    )


def revision_prompt(agent: AgentName, question: str, own_answer: str, other_answer: str) -> str:
    """Self-contained revision prompt: the question and the two latest answers, no history."""
    other = other_agent(agent)
    return REVISION_TEMPLATE.format(
        other=AGENT_LABELS[other],
        other_tag=other,
        question=neutralize_tags(question),
        own_answer=neutralize_tags(own_answer),
        other_answer=neutralize_tags(other_answer),
    )


def synthesis_prompt(
    question: str,
    answers: Mapping[AgentName, str],
    critiques: Mapping[AgentName, str | None],
) -> str:
    """Synthesis prompt: the question, both final answers and the last critiques
    (empty or "None" critiques are left out)."""
    parts = [
        f'<answer from="{AGENT_LABELS[agent]}">\n{neutralize_tags(answers[agent])}\n</answer>'
        for agent in AGENTS
        if agent in answers
    ]
    for agent in AGENTS:
        critique = (critiques.get(agent) or "").strip()
        if critique and critique.strip(" -*.").lower() != "none":
            target = AGENT_LABELS[other_agent(agent)]
            parts.append(
                f'<critique from="{AGENT_LABELS[agent]}" about="{target}">\n'
                f"{neutralize_tags(critique)}\n</critique>"
            )
    return SYNTHESIS_TEMPLATE.format(question=neutralize_tags(question), answers="\n\n".join(parts))
