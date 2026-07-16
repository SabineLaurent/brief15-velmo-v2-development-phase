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

## Phase 9 — Évaluation & qualité ✅
**Concept :** datasets LangSmith, evaluators, tests de non-régression.
**Livrable :** un jeu d'évaluation qui note l'agent automatiquement.
**Fait :** nouveau package `eval/` — les cas de test vivent **en code**
(`dataset.py`, source de vérité versionnée) et alimentent deux runners : le run
**LangSmith** (`run.py` : `target(inputs)` invoque le vrai graphe → `route` /
`answer` / `tool_output`, puis `client.evaluate(...)` sur EU, expérience nommée
par provider/model pour comparer les LLM) et un **gate pytest** local
(`tests/test_eval.py`, intégration, `skipif` si aucune clé provider). 5
evaluators **déterministes** (`evaluators.py`) : `route_matches` (match exact de
branche, le signal fort), `cites_source`, `honest_refusal`, `mentions_order`,
`no_cross_user_leak` (inspecte la sortie outil, robuste à la langue). Un
evaluator renvoie `None` = non applicable (skip pytest) ; adapté pour LangSmith
qui refuse les valeurs falsy (`_for_langsmith` → `{"results": []}`). Juge
LLM-as-judge volontairement remis à plus tard. Cible `make eval`. Vérifié en
live : 6/6 cas passent en pytest, expérience LangSmith EU sans erreur.

## Phase 10 — Persistance & robustesse (durcissement pré-prod) ✅
**Concept :** backends durables (checkpointer/store), retries/backoff/timeout,
fallback provider — un agent servi redémarre et encaisse les erreurs transitoires.
**Livrable :** l'agent survit à un redémarrage et ne casse pas sur un `429`/timeout.
**Fait :** persistance durable via un switch unique `PERSISTENCE_BACKEND`
(`memory` | `sqlite`) sur le checkpointer ET le store (index sémantique durable),
survie inter-process vérifiée en live ; retries + timeout forwardés à tous les
providers par la factory LLM. **Fallback provider** (`LLM_FALLBACK_PROVIDER` /
`LLM_FALLBACK_MODEL`) : la factory expose `get_chat_model_fallbacks()` ; comme
`RunnableWithFallbacks` ne propage pas `bind_tools`/`with_structured_output`, les
fallbacks sont composés **au niveau feuille** dans chaque node-factory (après le
binding), donc les nœuds restent agnostiques (ils ne nomment aucun provider).
Bascule sur `Exception` large (`FALLBACK_EXCEPTIONS`) car les SDK lèvent leurs
propres types. **Gestion d'erreurs explicite** dans les 3 nœuds qui appellent le
LLM (`router` / `answer` / `support`) : try/except + log + dégradation gracieuse
(`GRACEFUL_ERROR_MESSAGE` au lieu d'un crash de tour ; le router échoue en `answer`).
Les tools sont déjà couverts par `ToolNode` (`handle_tool_errors`), `escalate`
n'appelle pas le LLM. Tests unitaires purs (sans clé/réseau) : fallback + dégradation
gracieuse, 5/5 (`tests/test_robustness.py`) ; suite complète 11/11.

## Phase 11 — Cycle de vie du support (Case + Ticket) ⬜
**Concept :** modéliser un dossier de support comme en prod — un **Case** (dossier
par conversation, logué automatiquement) distinct des **Tickets** actionnables
qui lui sont rattachés ; statut + assignation (IA vs humain) qui évoluent.
**Livrable :** chaque conversation ouvre un Case ; le bot « signe » sa résolution
après validation du client ; l'historique complet permet une vraie détection de
récurrence.
**Étapes :**
- **A — Domaine & transitions :** objets `Case` + `Ticket` (deux objets), nœud
  `open_case` automatique et idempotent (ré-ouverture si le client revient),
  transitions `create_ticket → pending_human` / `escalate → escalated`, outil
  `list_customer_cases`.
- **B — Protocole de résolution :** en fin de traitement le bot fait **valider**
  au client (« Ai-je répondu à votre demande ? » / « Avez-vous besoin d'autre
  chose ? ») → oui ⇒ clôture + signature (`resolved_by_ai → closed`) ; sinon
  continuation / ré-ouverture.
  Découpage en **trois moments** (dont un seul est du routage) :
  1. **Poser** la question de clôture — en *sortie* d'un tour réussi (nœud
     `ask_closure`, ou consigne de prompt) ; pose aussi un drapeau d'état
     `awaiting_closure = True`.
  2. **Interpréter** la réponse oui/non — c'est une **sous-étape de routage** :
     nouvelle route `close` (à côté de `answer` / `support` / `escalate`), fiabilisée
     par le drapeau `awaiting_closure` pour ne pas confondre un « oui » de
     confirmation avec un « oui » quelconque.
  3. **Agir** — nœud `close_case` : signe et clôt (`resolved_by_ai → closed`) si
     résolu, sinon efface le drapeau et repart en `support` / `answer` (ré-ouverture).
  Choix assumé : cross-turn via le routeur (le client répond par un message
  normal), **pas** d'`interrupt()` — ce dernier reste réservé au handoff humain
  externe (Phase 7).

## Phase 12 — Sécurité & guardrails 🚧
**Concept :** prompt-injection, filtrage/masquage des données personnelles (PII),
limites sur ce que les outils ont le droit de faire, validation des entrées/sorties.
**Livrable :** l'agent résiste aux entrées malveillantes et ne fuit ni n'exécute
rien d'interdit — critique dès qu'un vrai client lui parle.

**Décision d'architecture :** on garde le `StateGraph` custom (esprit « capot
ouvert » de la Phase 6) et on écrit les guardrails comme des **nœuds explicites**,
PAS via les middlewares LangChain 1.x (`PIIMiddleware`, `HumanInTheLoopMiddleware`)
qui sont couplés à `create_agent` — qu'on a justement retiré. On emprunte leur
*modèle mental* (un hook d'entrée, un hook de sortie, une garde d'outil) sans le
couplage. Les middlewares tout faits restent une illustration possible, pas la
fondation.

**Principes transverses (non négociables du projet) :**
- **Agnosticisme préservé :** chaque détecteur (PII, injection) est un **port**
  (`Protocol`) + un adaptateur baseline, jamais un SaaS en dur — même pattern que
  `SupportBackend` (`actions/`) et la factory LLM. Remplaçable plus tard par
  Presidio ou un classifieur LLM sans toucher au graphe. Aucun provider nommé
  dans le code métier.
- **Déterministe d'abord :** la baseline est en regex/règles (gratuit, fiable,
  aucun appel LLM en plus). Un détecteur LLM-based reste une option pluggable
  future derrière le même port.
- **Fail-safe & observable :** un guardrail qui déclenche → log + trace LangSmith
  (tag dédié), jamais un crash silencieux ; court-circuit vers une réponse sûre.

**Étapes :**
- **A — Guardrails d'entrée (la porte d'entrée, priorité 1) :** nœud explicite
  `guard_input` en tête de graphe, AVANT `router`, capable de court-circuiter vers
  une réponse sûre (`jump_to` équivalent : arête conditionnelle vers une sortie).
  Contenu : (1) validation déterministe — taille max (coût/DoS), rejet vide/binaire ;
  (2) détection prompt-injection via un port `InjectionDetector` (baseline : patterns
  connus « ignore tes instructions », « montre ton system prompt »…) ; (3) masquage
  PII AVANT que le message n'atteigne le LLM, via un port `PIIDetector` (baseline
  regex : email / carte / téléphone / IBAN) avec stratégies `redact` / `mask` / `block`.
- **B — Guardrails de sortie & anti-fuite :** nœud `guard_output` avant de rendre
  la réponse au client. Défense en profondeur : re-masquage PII en sortie, anti-fuite
  (le modèle ne recrache pas son system prompt / une clé / des données d'un autre
  client), refus hors-domaine. S'applique aussi sur le chemin de reprise après
  `escalate` (Phase 7).
- **C — Durcissement outils & mémoire (effet de bord + persistance) :** (1)
  validation des entrées d'outils (`subject` / `body` de `create_ticket` viennent
  en partie du client via le LLM) ; (2) **hygiène PII de la mémoire long terme** —
  filtrer/masquer AVANT `store.put` dans `save_memory` : ne jamais persister un
  numéro de carte en clair (aggravé depuis la persistance SQLite durable de la
  Phase 10) ; (3) anti-abus : rate-limit applicatif sur les actions à effet de
  bord (`create_ticket`).

**Fait (A) — guardrails d'entrée :** nouveau package `guardrails/` bâti comme
`actions/` — deux **ports** (`PIIDetector`, `InjectionDetector`) + adaptateurs
baseline regex (`RegexPIIDetector`, `RegexInjectionDetector`), zéro dépendance,
zéro appel LLM ; remplaçables (Presidio, classifieur LLM) sans toucher au graphe.
Un objet `InputGuard` compose les 3 contrôles (validation taille/vide →
injection → masquage PII) et renvoie une `GuardDecision` pure (testable hors
LangGraph). Nœud `guard_input` inséré **entre `START` et `router`** (kill switch
`GUARDRAILS_ENABLED`) : masque la PII **en place** (overwrite par `id` via
`add_messages`, donc la PII brute n'atteint jamais LLM/outils/store), et sur
refus **retire** le message fautif de l'historique (`RemoveMessage`, pas de
pollution des tours suivants) + réponse sûre + court-circuit vers `END` via
l'arête conditionnelle `guard_route`. Refus générique (ne révèle pas la détection
à l'attaquant). Tests unitaires purs 12/12 (`tests/test_guardrails.py`), suite
complète 23/23, et vérif live hors-ligne : injection bloquée (0 LLM) + PII
réécrite en place. **Restent : B (sortie/anti-fuite) et C (outils & mémoire).**

## Phase 13 — Exposition & déploiement ⬜
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
- [x] Phase 9 — évaluation & qualité (dataset + evaluators, 6/6 pytest + run LangSmith EU)
- [x] Phase 10 — persistance & robustesse (SQLite + retries/timeout + fallback provider + erreurs nœuds)
- [~] Phase 12 — sécurité & guardrails (12-A entrée : injection + PII + validation, **fait**) ← **en cours**
- [ ] Phase 12 — reste : B (sortie/anti-fuite) + C (outils & mémoire)
- [ ] Phase 11 — cycle de vie du support (Case + Ticket) *(réordonnée après la 12)*
- [ ] Phase 13 — exposition & déploiement

> Note : Phases 11 et 12 **réordonnées** — la sécurité (guardrails) passe avant le
> cycle de vie du support, car elle est critique dès qu'un vrai client parle.
