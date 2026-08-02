"""Which optional-dependency extra ships which provider integration.

`init_chat_model` / `init_embeddings` resolve a provider by IMPORTING its integration
package at call time. Those packages are extras, so a perfectly valid `.env` value can
point at a provider whose package was never installed — and the raw failure is an
`ImportError` deep inside LangChain, which reads like a broken install rather than what
it is: a deployment that did not opt into that provider.

This module exists so that failure names its own fix. It is the ONE place that knows the
mapping, shared by the chat factory and the embeddings factory so the two cannot drift
apart.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

PROVIDER_EXTRAS: dict[str, str] = {
    "mistral": "mistral",
    "groq": "groq",
    "google_genai": "google",
    "azure_ai": "azure",
}


@contextmanager
def provider_package_required(provider: str) -> Iterator[None]:
    """Turn a missing provider package into an actionable config error.

    Wraps the exact call that imports the integration package. An `ImportError` for a
    provider we know as an extra is re-raised naming the extra to install; anything else
    propagates untouched, so a genuinely broken install still looks broken.

    Args:
        provider: The friendly provider name from the settings (e.g. "groq").

    Yields:
        Nothing — this is a pure error-translation guard.

    Raises:
        ImportError: Re-raised with the `uv sync --extra ...` fix when the provider
            is a known extra; unchanged otherwise.
    """
    try:
        yield
    except ImportError as exc:
        extra = PROVIDER_EXTRAS.get(provider.lower())
        if extra is None:
            raise
        msg = (
            f"Provider {provider!r} is configured, but its integration package is "
            f"not installed. It ships in this package's optional {extra!r} extra:\n"
            f"    uv sync --extra {extra}\n"
            f"For the container image, add `--extra {extra}` to the `uv sync` line "
            f"in packages/support-agent/Dockerfile.\n"
            f"Original import error: {exc}"
        )
        raise ImportError(msg) from exc
