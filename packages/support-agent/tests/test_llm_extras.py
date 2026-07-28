"""Unit tests for the provider/extra contract behind the LLM-agnosticism claim.

Providers ship as optional-dependency extras (`pyproject.toml`), and
`init_chat_model` / `init_embeddings` import a provider's package only when the
config asks for it. That buys a lean default install, but it opens a specific way
to be wrong: `LLM_PROVIDER` can name a provider the install never included, and
the raw symptom is an `ImportError` from inside LangChain — which reads like a
broken environment rather than a deployment that did not opt in.

Two things are asserted here:

  1. Translation — a missing provider package fails by naming the extra that
     ships it, while an unrelated ImportError stays untouched.
  2. The invariant, made executable — the alias maps and the declared extras
     cannot drift apart. This is the regression guard for the bug that motivated
     the split: `langchain-mistralai` was a hard dependency nothing imported,
     while `groq` / `google_genai` / `azure_ai` were advertised in the alias map
     and installed nowhere.

Pure unit tests: no network, no credentials, no provider package required.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

import pytest

from support_agent.llm import embeddings as embeddings_module
from support_agent.llm import factory as factory_module
from support_agent.llm._extras import PROVIDER_EXTRAS, provider_package_required

# Providers backed by a BASE dependency (`langchain-openai`), so they are
# legitimately absent from PROVIDER_EXTRAS. Any OTHER provider missing from that
# map is the drift this module exists to catch.
_BASE_DEPENDENCY_PROVIDERS = {"openai", "openai_compatible"}

# Providers handled by a dedicated branch rather than by an alias lookup.
_NON_ALIAS_PROVIDERS = {"openai_compatible", "custom"}


def _declared_extras() -> dict[str, list[str]]:
    """Read `[project.optional-dependencies]` from this package's real manifest."""
    manifest = Path(__file__).resolve().parents[1] / "pyproject.toml"
    return tomllib.loads(manifest.read_text())["project"]["optional-dependencies"]


# ─── 1. Translation: the failure names its own fix ────────────────────────────


def test_missing_provider_package_names_the_extra_to_install() -> None:
    """A known provider's ImportError is re-raised naming its extra and the command."""
    with pytest.raises(ImportError) as excinfo:
        with provider_package_required("groq"):
            raise ImportError("No module named 'langchain_groq'")

    message = str(excinfo.value)
    assert "groq" in message
    assert "uv sync --extra groq" in message
    # The original cause must survive for debugging, not be swallowed.
    assert "langchain_groq" in message


def test_provider_name_is_matched_case_insensitively() -> None:
    """Settings values are free-form text; `Groq` must translate like `groq`."""
    with pytest.raises(ImportError, match="uv sync --extra google"):
        with provider_package_required("GOOGLE_GENAI"):
            raise ImportError("No module named 'langchain_google_genai'")


def test_unrelated_import_error_is_left_untouched() -> None:
    """A provider with no extra must not have its error relabelled.

    `openai` ships in a base dependency, so an ImportError there is a genuinely
    broken install. Dressing it up as a missing extra would send the reader off
    to install something that is already declared.
    """
    with pytest.raises(ImportError) as excinfo:
        with provider_package_required("openai"):
            raise ImportError("cannot import name 'ChatOpenAI'")

    assert "uv sync --extra" not in str(excinfo.value)


def test_guard_is_transparent_when_nothing_fails() -> None:
    """The happy path returns normally — the guard only translates errors."""
    with provider_package_required("groq"):
        result = "built"

    assert result == "built"


# ─── 2. The architectural invariant, made executable ─────────────────────────


def test_every_extra_in_the_map_is_actually_declared() -> None:
    """Each extra named in an error message must exist in `pyproject.toml`.

    Otherwise the guard sends the reader to run `uv sync --extra <typo>`, which
    fails with an unrelated message.
    """
    declared = _declared_extras()

    for provider, extra in PROVIDER_EXTRAS.items():
        assert extra in declared, (
            f"provider {provider!r} points at extra {extra!r}, "
            f"absent from pyproject.toml (declared: {sorted(declared)})"
        )


@pytest.mark.parametrize(
    ("label", "aliases"),
    [
        ("chat", factory_module._PROVIDER_ALIASES),
        ("embeddings", embeddings_module._PROVIDER_ALIASES),
    ],
)
def test_every_advertised_provider_is_installable(
    label: str, aliases: dict[str, str]
) -> None:
    """A provider the config accepts must be installable — via a base dep or an extra.

    THE regression guard: adding a line to an alias map without declaring its
    extra makes a `.env` value that crashes on first use. This fails that commit
    instead of the deployment that flips the provider.
    """
    for provider in aliases:
        if provider in _BASE_DEPENDENCY_PROVIDERS:
            continue
        assert provider in PROVIDER_EXTRAS, (
            f"{label} provider {provider!r} is advertised but has no extra. "
            f"Declare it in pyproject.toml and add it to PROVIDER_EXTRAS."
        )


def test_provider_extras_map_has_no_dead_entries() -> None:
    """Every provider in the map is one some factory can actually build.

    Guards the opposite drift: a provider dropped from the alias maps but left
    behind here, so the extra outlives the code that used it.
    """
    known = (
        set(factory_module._PROVIDER_ALIASES)
        | set(embeddings_module._PROVIDER_ALIASES)
        | _NON_ALIAS_PROVIDERS
    )

    assert set(PROVIDER_EXTRAS) <= known, (
        f"PROVIDER_EXTRAS names providers no factory builds: "
        f"{sorted(set(PROVIDER_EXTRAS) - known)}"
    )
