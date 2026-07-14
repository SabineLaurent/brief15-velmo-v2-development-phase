# 🗺️ ROADMAP — Le tuto pas à pas

On construit l'agent **par couches**, de la plus simple à la plus complète.
Chaque phase a un **objectif d'apprentissage** (le concept lang* qu'on découvre)
et un **livrable** (ce qui marche à la fin). On ne passe à la phase suivante
qu'une fois la précédente comprise et fonctionnelle.

Légende : ✅ fait · 🚧 en cours · ⬜ à venir

---

## Phase 0 — Structure & fondations 🚧
**Objectif d'apprentissage :** séparer le QUOI (spec) du COMMENT (archi), poser
une base agnostique dès le départ.
**Livrable :** arborescence, docs, `.env.example`, projet `uv` prêt à installer.

---

## Phase 1 — La couche LLM agnostique + premier « hello agent » ✅
**Concept :** `BaseChatModel`, la factory provider, `init_chat_model`.
**Livrable :** `llm/factory.py` — on parle à Mistral *ou* Groq *ou* Foundry en
changeant juste `.env`. Un mini-script qui envoie un message et reçoit une réponse.
**Fait :** run live validé contre Mistral (`mistral-large-latest`).

## Phase 2 — Observabilité avec LangSmith ✅
**Concept :** tracing, pourquoi c'est indispensable pour déboguer un agent.
**Livrable :** chaque appel LLM est tracé et visible dans le dashboard LangSmith.
**Fait :** tracing EU activé (endpoint region), runs enrichis (run_name/tags/metadata).

## Phase 3 — Mémoire court terme (conversation) 🚧
**Concept :** LangGraph `checkpointer`, notion de `thread_id`, état persistant
d'une conversation.
**Livrable :** l'agent se souvient des messages précédents dans une même session.

## Phase 4 — Base de connaissance FAQ (RAG) ⬜
**Concept :** embeddings, vector store, retriever, chunking, retrieval tool.
**Livrable :** l'agent répond en s'appuyant sur une FAQ ingérée, avec citations.

## Phase 5 — Mémoire long terme (cross-session) ⬜
**Concept :** LangGraph `Store`, mémoire sémantique/épisodique, namespaces par
utilisateur.
**Livrable :** l'agent se souvient d'infos d'un utilisateur d'une session à l'autre.

## Phase 6 — Orchestration LangGraph (le vrai graphe) ⬜
**Concept :** `StateGraph`, nœuds, arêtes conditionnelles, routage.
**Livrable :** un graphe explicite qui décide : répondre / chercher FAQ / escalader.

## Phase 7 — Escalade humaine (human-in-the-loop) ⬜
**Concept :** `interrupt`, points de pause, reprise d'exécution.
**Livrable :** l'agent transfère à un humain les cas qu'il ne sait pas traiter.

## Phase 8 — Outils & actions ⬜
**Concept :** tool calling, définition d'outils métier agnostiques.
**Livrable :** l'agent peut agir (ex : statut de commande, création de ticket).

## Phase 9 — Évaluation & qualité ⬜
**Concept :** datasets LangSmith, evaluators, tests de non-régression.
**Livrable :** un jeu d'évaluation qui note l'agent automatiquement.

## Phase 10 — Exposition & déploiement ⬜
**Concept :** servir l'agent (API/LangGraph Server), configuration par environnement.
**Livrable :** l'agent est appelable depuis l'extérieur, prêt à être branché à un projet.

---

### Où on en est
- [x] Phase 0 — structure & fondations
- [x] Phase 1 — couche LLM agnostique (run live Mistral OK)
- [x] Phase 2 — observabilité LangSmith (tracing EU OK)
- [ ] Phase 3 — mémoire court terme (prochaine étape)
