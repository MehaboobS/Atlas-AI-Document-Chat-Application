import logging
from typing import Generator, Sequence
from google import genai
from app.config import settings

logger = logging.getLogger(__name__)


def get_gemini_client() -> genai.Client:
    """
    Create and return an official Google GenAI Client.
    Uses free GEMINI_API_KEY from https://aistudio.google.com/.
    """
    cfg = settings()
    if not cfg.gemini_api_key or not cfg.gemini_api_key.strip():
        raise ValueError(
            "GEMINI_API_KEY is not set in your .env file.\n"
            "Get your free key at: https://aistudio.google.com/"
        )
    return genai.Client(api_key=cfg.gemini_api_key.strip())


def generate_chat_response(
    messages: Sequence[dict[str, str]] | str,
    stream: bool = False,
) -> str | Generator[str, None, None]:
    """
    Generate a response using Google Gemini (100% Free via Google AI Studio).
    Supports prompt string or list of OpenAI-style message dictionaries.
    """
    cfg = settings()
    client = get_gemini_client()

    if isinstance(messages, str):
        prompt = messages
    elif isinstance(messages, (list, tuple)):
        prompt_parts = []
        for msg in messages:
            role = msg.get("role", "user")
            content = msg.get("content", "")
            if role == "system":
                prompt_parts.append(f"System: {content}\n")
            elif role == "user":
                prompt_parts.append(f"User: {content}\n")
            elif role == "assistant":
                prompt_parts.append(f"Assistant: {content}\n")
        prompt = "\n".join(prompt_parts)
    else:
        prompt = str(messages)

    if stream:
        def stream_generator():
            response = client.models.generate_content_stream(
                model=cfg.gemini_model,
                contents=prompt,
            )
            for chunk in response:
                if chunk.text:
                    yield chunk.text
        return stream_generator()

    response = client.models.generate_content(
        model=cfg.gemini_model,
        contents=prompt,
    )
    return response.text or ""
