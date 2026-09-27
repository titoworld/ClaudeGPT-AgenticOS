from agentic_os.providers.base import ChatTurn, GenerationRequest
from agentic_os.providers.prompt_format import render_transcript, to_chat_messages


def test_render_transcript_without_context_is_the_prompt() -> None:
    assert render_transcript(GenerationRequest(system="s", prompt="Hola")) == "Hola"


def test_render_transcript_labels_speakers_and_summary() -> None:
    request = GenerationRequest(
        system="s",
        prompt="I ara?",
        context_summary="Parlàvem de Python.",
        history=(
            ChatTurn("user", "Què és uv?"),
            ChatTurn("assistant", "Un gestor de paquets.", agent="chatgpt"),
        ),
    )
    text = render_transcript(request)
    assert text.index("<conversation_summary>") < text.index("<conversation_history>")
    assert '<message from="ChatGPT">' in text
    assert text.endswith("<current_message>\nI ara?\n</current_message>")


def test_chat_messages_alternate_and_label_the_other_agent() -> None:
    request = GenerationRequest(
        system="s",
        prompt="Continua",
        context_summary="Resum",
        history=(
            ChatTurn("user", "Pregunta"),
            ChatTurn("assistant", "Resposta Claude", agent="claude"),
            ChatTurn("assistant", "Resposta GPT", agent="chatgpt"),
        ),
    )
    messages = to_chat_messages(request, "claude")
    assert [role for role, _ in messages] == ["user", "assistant", "user"]
    assert messages[0][1].startswith("<conversation_summary>")
    assert "Pregunta" in messages[0][1]
    assert messages[1][1] == "Resposta Claude\n\n[ChatGPT]\nResposta GPT"
    assert messages[2] == ("user", "Continua")


def test_chat_messages_never_start_with_assistant() -> None:
    request = GenerationRequest(
        system="s", prompt="Hola", history=(ChatTurn("assistant", "Hi", agent="claude"),)
    )
    assert to_chat_messages(request, "claude")[0][0] == "user"
