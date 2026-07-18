# support-agent

**Scope A — le cœur de l'agent** (le « cerveau ») du mono-repo.

Agent IA de support client agnostique au provider LLM (LangChain + LangGraph +
LangSmith) : orchestration (`StateGraph`), mémoire court/long terme, RAG (FAQ),
outils métier, human-in-the-loop, guardrails.

- Vision d'ensemble & scopes : [`../../docs/vision.md`](../../docs/vision.md)
- Conception technique : [`../../docs/architecture.md`](../../docs/architecture.md)
- Étapes du tuto : [`../../ROADMAP.md`](../../ROADMAP.md)

> Ce package est un **membre du workspace uv** (voir le `pyproject.toml` racine).
> Commandes via le `Makefile` à la racine (`make run`, `make test`, `make check`).
