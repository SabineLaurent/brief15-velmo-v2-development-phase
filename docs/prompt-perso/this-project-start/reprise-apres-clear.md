On reprend le projet agent de support. Lis CLAUDE.md, MEMORY.md et ROADMAP.md,
fais un point rapide sur où on en est, puis reprenons à la Phase 9.

État au 2026-07-15 : Phases 0 à 8 ✅ (dernier commit phase 8 : e29b418).
Phase 8 = outils & actions métier agnostiques (get_order_status / create_ticket),
durcie ownership + idempotence (commit 6c676a4). Prochaine étape = Phase 9 —
Évaluation & qualité (datasets LangSmith + evaluators + tests de non-régression).

Note infra (hors ROADMAP) : le LLM tourne désormais sur Azure OpenAI API v1 via
le provider `openai_compatible` (chat gpt-5.6-sol + embeddings
text-embedding-3-small), configuré par `.env` (LLM_INFERENCE_ENDPOINT /
LLM_INFERENCE_API_KEY). L'agnosticisme a été prouvé en live (bascule Mistral →
Azure sans toucher au code métier). Traces LangSmith en région EU
(eu.smith.langchain.com).

Rappel méthode : Context7 avant tout code lang*, pédagogie du POURQUOI, une phase
à la fois, commit par phase.
