"""Tests d'intégration agent ↔ mémoire (palier de câblage).

Contrairement à `test_memory.py` qui exerce `MemoryManager` en isolation, ici on
passe par `Agent.respond` : on vérifie que la mémoire est *réellement* branchée
dans le pipeline conversationnel (contexte injecté, oubli déclenché, faits
extraits), et non seulement correcte en tant que brique.
"""

from __future__ import annotations

from velmo.agent import Agent
from velmo.db import fresh_sqlite_session
from velmo.guardrails import Decision
from velmo.kb_store import LocalKB
from velmo.llm import EchoLLM
from velmo.memory import MemoryManager
from velmo.memory.store import MemoryStore
from velmo.sampledata import seed


class _AllowAll:
    """Garde-fous neutres : on isole le comportement mémoire."""

    def check_input(self, message: str) -> Decision:
        return Decision(allowed=True, action="allow")

    def check_output(self, text: str) -> Decision:
        return Decision(allowed=True, action="allow")


class _SpyLLM:
    """LLM-espion : capture le contexte reçu au fallback conversationnel.

    Nécessaire car `EchoLLM` ignore le contexte — l'injection ne se verrait pas
    dans la réponse hors-ligne, seulement dans ce que le LLM reçoit."""

    def __init__(self) -> None:
        self.seen_context: str | None = None

    def invoke(self, system: str, context: str, message: str) -> str:
        self.seen_context = context
        return f"[spy] {message}"


def _agent(memory: MemoryManager, llm=None) -> Agent:
    session = fresh_sqlite_session()
    seed(session)
    return Agent(
        llm=llm or EchoLLM(),
        memory=memory,
        guardrails=_AllowAll(),
        session=session,
        kb=LocalKB(),
    )


def _store(tmp_path) -> MemoryStore:
    return MemoryStore(f"sqlite:///{tmp_path / 'mem.db'}")


def test_read_context_reaches_llm(tmp_path):
    # Correctif 1 : le contexte reconstitué par read() doit atteindre le LLM,
    # pas être jeté. On l'observe via un LLM-espion.
    spy = _SpyLLM()
    agent = _agent(MemoryManager(store=_store(tmp_path)), llm=spy)

    agent.respond("int-marc", "Je porte toujours la taille L.")
    agent.respond("int-marc", "Tu te souviens de moi ?")  # -> fallback LLM

    assert spy.seen_context is not None
    assert "taille=L" in spy.seen_context


def test_forget_via_respond(tmp_path):
    # Correctif 2 : « oublie mon adresse » supprime réellement l'info via respond.
    mm = MemoryManager(store=_store(tmp_path))
    agent = _agent(mm)

    agent.respond("int-sophie", "Mon adresse est 12 rue des Lilas.")
    assert "rue des Lilas" in mm.read("int-sophie", "Mon adresse ?").render()

    reply = agent.respond("int-sophie", "Oublie mon adresse s'il te plait.")
    assert "oublié" in reply.lower()
    assert "rue des Lilas" not in mm.read("int-sophie", "Mon adresse ?").render()


def test_facts_persist_across_sessions_via_respond(tmp_path):
    # Correctif 3 : un fait dit en conversation est promu en fait durable et
    # retrouvé dans une nouvelle session (même store).
    url = f"sqlite:///{tmp_path / 'mem.db'}"

    session1 = _agent(MemoryManager(store=MemoryStore(url)))
    session1.respond("int-karim", "Je suis revendeur et je porte du L.")

    session2 = MemoryManager(store=MemoryStore(url))
    rendered = session2.read("int-karim", "Tu te souviens de moi ?").render()
    assert "segment=revendeur" in rendered
    assert "taille=L" in rendered
