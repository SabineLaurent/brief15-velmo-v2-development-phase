# 🗺️ ROADMAP — Le tuto pas à pas

On construit l'agent **par couches**, de la plus simple à la plus complète.
Chaque phase a un **objectif d'apprentissage** (le concept lang* qu'on découvre)
et un **livrable** (ce qui marche à la fin). On ne passe à la phase suivante
qu'une fois la précédente comprise et fonctionnelle.

> 🎯 La ROADMAP est le **chemin**. Pour la **destination** (à quoi ressemble le
> produit fini, prod-grade 2026, et les critères pas encore couverts par une
> phase), voir [`docs/perimetre-final.md`](docs/perimetre-final.md).

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

## Phase 3 — Mémoire court terme (conversation) ✅
**Concept :** LangGraph `checkpointer`, notion de `thread_id`, état persistant
d'une conversation.
**Livrable :** l'agent se souvient des messages précédents dans une même session.
**Fait :** `create_agent` + `InMemorySaver`, chat interactif, souvenir + isolation
par `thread_id` vérifiés en live.

## Phase 4 — Base de connaissance FAQ (RAG) ✅
**Concept :** embeddings, vector store, retriever, chunking, retrieval tool.
**Livrable :** l'agent répond en s'appuyant sur une FAQ ingérée, avec citations.
**Fait :** FAQ d'exemple (4 fichiers), embeddings agnostiques, `InMemoryVectorStore`,
outil `search_faq` (agentic RAG). Cas « dans la FAQ » (avec source) et « hors FAQ »
(refus honnête) vérifiés en live.

## Phase 5 — Mémoire long terme (cross-session) ✅
**Concept :** LangGraph `Store`, mémoire sémantique/épisodique, namespaces par
utilisateur.
**Livrable :** l'agent se souvient d'infos d'un utilisateur d'une session à l'autre.
**Fait :** `InMemoryStore` avec recherche sémantique (embeddings agnostiques réutilisés),
outils agentiques `save_memory`/`search_memories`, namespace `("memories", user_id)`,
`create_agent(store=..., context_schema=AgentContext)`. Recall cross-session
(thread A → thread B) et isolation par `user_id` vérifiés en live.

## Phase 6 — Orchestration LangGraph (le vrai graphe) ✅
**Concept :** `StateGraph`, nœuds, arêtes conditionnelles, routage.
**Livrable :** un graphe explicite qui décide : répondre / chercher FAQ / escalader.
**Fait :** `create_agent` remplacé par un `StateGraph` custom (package `graph/` :
`state.py` / `nodes.py` / `builder.py`). Nœud `router` (LLM à sortie structurée
`with_structured_output`) → arête conditionnelle vers 3 branches : `answer`
(petit talk), `support` (boucle ReAct manuelle `model` ⇄ `ToolNode` via
`tools_condition` : FAQ + mémoire), `escalate` (handoff — placeholder Phase 7).
Mémoire court/long terme conservée (`compile(checkpointer, store)` +
`context_schema`). Les 3 routages + le rappel mémoire cross-thread vérifiés en live.

## Phase 7 — Escalade humaine (human-in-the-loop) ✅
**Concept :** `interrupt`, points de pause, reprise d'exécution.
**Livrable :** l'agent transfère à un humain les cas qu'il ne sait pas traiter.
**Fait :** nœud `escalate` réécrit avec un vrai `interrupt()` (payload `reason` /
`user_id` / `customer_message` remonté à l'opérateur). Le graphe se met en pause
(checkpointer) et `invoke` renvoie `__interrupt__` ; `agent.py` joue le conseiller
et reprend via `Command(resume=<réponse>)`, qui redevient le message client. Nœud
gardé idempotent (lecture seule avant l'`interrupt`, car le nœud ré-exécute à la
reprise). Pause + reprise vérifiées en live (branche escalade → réponse humaine).

## Phase 8 — Outils & actions ✅
**Concept :** tool calling, définition d'outils métier agnostiques.
**Livrable :** l'agent peut agir (ex : statut de commande, création de ticket).
**Fait :** nouveau package `actions/` bâti comme la factory LLM — un **port**
`SupportBackend` (Protocol) + un adaptateur de démo `InMemorySupportBackend`,
pour rester agnostique au SI métier (brancher le vrai système = écrire un
adaptateur, sans toucher aux outils). Deux outils d'**action** (`actions/tools.py`)
branchés sur la branche `support` à côté de FAQ + mémoire : `get_order_status`
(lecture d'une commande) et `create_ticket` (effet de bord ; `user_id` pris dans
le contexte runtime, jamais du LLM — même isolation que la mémoire). Prompt du
routeur affûté pour séparer `create_ticket` (suivi asynchrone → branche support)
de l'escalade Phase 7 (handoff synchrone → `interrupt`). Les 3 cas vérifiés en
live : statut commande, ouverture de ticket (conversation continue), et demande
humaine explicite (escalade Phase 7 non régressée).

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
- [x] Phase 3 — mémoire court terme (souvenir + isolation vérifiés)
- [x] Phase 4 — base de connaissance FAQ / RAG (hit + miss vérifiés)
- [x] Phase 5 — mémoire long terme cross-session (recall + isolation vérifiés)
- [x] Phase 6 — orchestration LangGraph / StateGraph (routage + ReAct manuel vérifiés)
- [x] Phase 7 — escalade humaine / human-in-the-loop (pause + reprise vérifiées)
- [x] Phase 8 — outils & actions (order status + création ticket, vérifiés en live)
- [ ] Phase 9 — évaluation & qualité (prochaine étape)
