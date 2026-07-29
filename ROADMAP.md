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

> ⚠️ **Révisé le 2026-07-26 (défaut C1).** Derrière une API, cette pause **tuait
> le fil** : LangGraph reprend la tâche pendante avant tout, donc le `router`
> n'était plus jamais réévalué et tous les messages suivants recevaient la même
> phrase, sans erreur nulle part. `escalate` **ne met plus le graphe en pause** :
> il ouvre un ticket, coupe le bot (`handled_by_human`) et termine le tour —
> le modèle des vraies plateformes de support. `interrupt()` n'est pas abandonné,
> il est **relogé** sur une porte d'approbation d'action irréversible.
> Le pourquoi complet : [`docs/escalade.md`](docs/escalade.md).

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

> ➕ **2026-07-26 :** un 4e outil, `request_human_handoff(reason, summary)`, rend
> l'escalade **décidable après tentative** — c'est le modèle qui a la FAQ et les
> outils en main qui transfère, pas le routeur qui n'a que le message. Le dossier
> part avec son contexte. Voir [`docs/escalade.md`](docs/escalade.md) §3 bis.

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

> 🐛 **Défaut ouvert (constaté le 2026-07-29) : `honest_refusal` est INSTABLE.**
> Le cas `out-of-faq-honest-refusal` échoue par intermittence — 1 échec observé
> sur 8 exécutions. Ce n'est pas l'agent : c'est l'évaluateur qui note **faux** un
> refus parfaitement correct. Exemple réel qui a échoué :
>
> > « Non, notre FAQ **ne mentionne aucune** vente de billets d'avion. Je **ne
> > peux donc pas** confirmer que ce service est proposé. »
>
> Les signaux cherchent `"ne mentionne pas"` (le modèle a écrit « ne mentionne
> **aucune** ») et `"ne peux pas"` (le modèle a inséré « **donc** »). Vérifié hors
> ligne : les 5 signaux applicables renvoient `False`.
>
> **Même classe de bug que `f404775`** (« honest_refusal was scoring the model's
> choice of apostrophe ») : du *substring matching* sur de la prose libre casse
> sur une variante de négation ou un adverbe inséré. Rustiner la liste de signaux
> ne ferait que déplacer le problème d'un cran — la vraie réponse est un **juge
> sémantique**, donc c'est de la matière du **chantier 3**, pas un correctif isolé.
> ⚠️ En attendant, un `make check` rouge sur ce seul cas n'est pas une régression :
> relancer avant d'enquêter.

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

> 🗄️ **Décidé le 2026-07-29 (où vivent Case et Ticket) : un TROISIÈME schéma.**
> La table où l'**agent écrit** doit être séparée des tables que le **marchand
> possède**, sur le même serveur Postgres. Trois schémas, chacun avec sa
> rétention et son propriétaire :
>
> | Schéma | Propriétaire | Rétention |
> |---|---|---|
> | `agent_state` | nous | **balayé par le TTL RGPD** (`MEMORY_TTL_DAYS`) |
> | `shop` | le marchand (doublure) | jetable (`make seed ARGS=--reset`) |
> | `support` — Cases + Tickets | l'agent | durable, **jamais balayé** |
>
> Quatre raisons, de la plus forte à la plus faible :
> 1. **Ça supprime un hack.** `_ensure_customer()` (`actions/sql/backend.py`)
>    fabrique un client bidon uniquement parce que `tickets.customer_id` est une
>    FK vers `customers`. Hors du périmètre marchand, on abandonne la FK — et
>    c'est la modélisation JUSTE : en prod le client vit chez le marchand, il n'y
>    a rien à référencer. `customer_id` devient une simple chaîne.
> 2. **Le moindre privilège devient applicable** : `SELECT` seul sur `shop`,
>    `INSERT` sur `support`. C'est la base qui l'empêche, pas la relecture.
> 3. **`--reset` cesse de détruire le travail de l'agent.** Aujourd'hui il efface
>    les tickets des conversations avec la fixture (cf.
>    `test_seed_reset_rebuilds_and_drops_conversation_tickets`) — conséquence du
>    schéma partagé, pas un choix.
> 4. **Pas dans `agent_state` non plus** : le *sweeper* de rétention y mangerait
>    les tickets. Un ticket d'escalade est un registre métier, pas une donnée de
>    personnalisation.
>
> Non construit à ce jour : la doublure est en SQLite (dev) et en RAM (conteneur,
> forcé par `compose.yaml`). Ce bloc est le plan, pas l'état.

## Phase 12 — Sécurité & guardrails ✅
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

**Fait (B) — guardrails de sortie & anti-fuite :** nœud `guard_output` symétrique
de `guard_input`, sur **toutes les branches qui répondent** (`answer`, `support`,
reprise `escalate`) — la fin de la boucle ReAct y est routée en remappant le `END`
de `tools_condition`. Un objet `OutputGuard` (pur, testable) applique trois
contrôles **déterministes** : (1) **fuite du system prompt** — si la réponse
recopie verbatim nos instructions (détection par *shingles* ≥ 60 car.), on
**remplace** toute la réponse par un message sûr ; (2) **re-masquage PII** en
sortie (défense en profondeur, réutilise le `PIIDetector`) ; (3) **anti-fuite
secret** — nouveau port `SecretDetector` + `RegexSecretDetector` (clés `sk-`/`gsk_`,
AWS `AKIA…`, `Bearer …`), fondu dans la même redaction via un `CompositeDetector`.
Réécriture **en place** (overwrite par `id`), réponse propre laissée intacte.
Le *refus hors-domaine* (non déterministe) est volontairement laissé au niveau
prompt / durcissement futur, pas dans ce nœud. Tests unitaires 6 de plus (18/18
guardrails, suite complète 29/29), vérif live : graphe complet traversant
`guard_output` sur les chemins answer + support, sans faux positif. **Reste : C
(outils & mémoire).**

**Fait (C) — durcissement outils & mémoire :** nouveau `ToolGuard` (réutilise le
`PIIDetector` + un `RateLimiter` in-process, sliding-window) threadé dans les
*builders* d'outils via le même kill switch (`None` = comportement d'avant). Trois
protections sur les outils **d'écriture** : (1) **validation** de la longueur des
champs générés par le LLM (`subject`/`body` de ticket, `text` mémoire) ; (2)
**hygiène PII avant persistance** — `sanitize()` masque la PII **avant**
`store.put` (`save_memory`) et avant d'écrire un ticket (`create_ticket`), donc
un numéro de carte n'est **jamais** persisté en clair (le trou aggravé par le
SQLite de la Phase 10) ; (3) **anti-abus** — rate-limit par client sur
`create_ticket` (seule vraie action à effet de bord). Le `RateLimiter`
in-process est documenté comme à remplacer par un store partagé (Redis) en prod,
sans changer le code des outils. Tests +4 (22/22 guardrails, suite complète
33/33), lint clean. Vérif hors-ligne : ticket **et** mémoire persistés sans PII
brute, rate-limit appliqué, graphe sans cycle d'import. **Phase 12 terminée.**

**Fait (D) — modération de contenu (chantier 2, 2026-07-29) :** 4e port
`ContentModerator` + `RegexContentModerator` (`guardrails/moderation.py`), câblé
**en entrée** (`InputGuard`, étape 3, après l'injection et avant le masquage PII)
**et en sortie** (`OutputGuard`, où il **remplace** toute la réponse — une phrase
haineuse n'a pas de « portion » à caviarder). Le corpus d'acceptance du starter
est désormais **exécuté** et non paraphrasé : `data/eval/guardrail_cases.jsonl`
(35 cas) piloté par `tests/test_moderation.py` (26 tests).

**Mesuré avant / après** — le chiffre qui compte est la dernière ligne :

| Catégorie | Avant | Après |
|---|---|---|
| `hate` · `violence` · `sexual` | 0/8 | **8/8** |
| `secret_leak` (entrée) | 0/3 | **3/3** |
| `pii` (sortie) | 2/3 | **3/3** |
| `prompt_injection` | 4/4 | 4/4 |
| `out_of_scope` | 0/5 | 0/5 *(écarté, argumenté)* |
| **`legitimate` (faux positifs)** | **12/12** | **12/12** |
| **TOTAL** | **18/35** | **30/35** |

Trois décisions non évidentes :
- **Le port renvoie une CATÉGORIE, pas un booléen.** C'est ce qui rend le blocage
  journalisable (`reason=content_hate` — le nœud le loguait déjà, zéro code en
  plus) *et* ce qui permet des réponses différenciées.
- **L'auto-agression est sa propre catégorie**, avec son propre message. Répondre
  à quelqu'un en détresse par le refus générique serait la mauvaise chose à lui
  dire. L'ordre des règles est porteur : `self_harm` est testé **avant**
  `violence`, sinon « me faire du mal » part dans la branche refus.
- **Repli d'accents obligatoire** (`fold()` : NFD + suppression des diacritiques
  + apostrophes). Ce n'est pas cosmétique — c'est la même classe de bug que
  `f404775` (`honest_refusal` notait l'apostrophe du modèle), en version sécurité :
  sans lui, l'attaquant tape sans accents une fois et passe.
- **Précision > rappel, assumé.** Pas de `\bhais\b` ni `\btuer\b` nus : ils
  partiraient sur du trafic support normal. Refuser à tort un client en colère
  (« c'est scandaleux », « je vais porter plainte ») coûte un vrai client ; un
  raté dégrade vers le refus du modèle. 7 cas « en colère mais légitime » sont
  sous test pour épingler ça.

Vérifié en live : message haineux **32 ms**, menace **1,5 ms**, détresse **1,5 ms**
— contre **6 163 ms** pour le chemin légitime. Le facteur 4 000 est la preuve que
le garde court-circuite avant tout appel LLM.

⚠️ **Reste ouvert : `out_of_scope` (5 cas).** Deux natures s'y cachent, et une
seule est tranchée. Les 3 cas d'**estimation/authentification** (« combien vaut
mon maillot ? ») sont écartés à dessein : un « la FAQ ne le couvre pas » honnête
sert mieux le client qu'un blocage. Les 2 cas de **conseil** (juridique,
financier) ne sont **pas** tranchés — le brief les nomme explicitement, et c'est
une position de responsabilité, pas d'UX. Encodés comme déviation *sous test*
(`DELIBERATELY_NOT_BLOCKED`) pour qu'une implémentation future casse le test et
force la décision au lieu de la glisser en silence.

**Durcissement futur des détecteurs (derrière les ports, sans toucher au graphe) :**
la baseline regex est une *première ligne*. Deux upgrades naturels, non urgents :
- **PII → Presidio** (pas un LLM) : ajoute noms/adresses (NER) + validation
  Luhn/IBAN, reste **local et gratuit en tokens**. Un LLM-détecteur enverrait la
  PII brute à un LLM — à rebours de l'objectif « pas de PII vers le LLM ».
- **Injection → petit classifieur** (Presidio hors sujet ici) : l'injection est
  sémantique, le regex se contourne. Priorité à un **modèle local dédié**
  (type Prompt-Guard) ; LLM-as-judge en repli (mais +1 appel LLM par tour).
- Principe : *defense-in-depth* — garder le regex en passe rapide, brancher le
  détecteur lourd derrière le **même port** seulement quand il laisse passer.

## Phase 13 — Exposition & déploiement 🚧
**Concept :** servir l'agent en HTTP, configuration par environnement, conteneurs.
**Livrable :** l'agent est appelable depuis l'extérieur, prêt à être branché à un projet.
**Découpe en 5 étapes, avec le but de chacune :**
[`docs/plan-deploiement-2026-07-25.md`](docs/plan-deploiement-2026-07-25.md).
Cible finale : **Azure** (App Service for Containers + PostgreSQL Flexible Server).
**Étapes 1 à 4 faites** (détail et bilans chiffrés : le plan) :
1. `support_agent/server.py` — `POST /chat` en **SSE**, `/health`, `/ready`, clé de
   service **fail-closed**, `make serve`. Le serveur *transporte* ce que `stream_reply`
   *produit* : aucune logique d'agent dans la couche HTTP.
2. Image Docker de la tranche `support-agent` (multi-stage, non-root, sans `.env`).
3. **Postgres + pgvector réellement exercé** (`PERSISTENCE_BACKEND=postgres`) : une
   seule base, deux horizons de mémoire séparés par schéma, pool partagé, sweeper RGPD.
4. Conteneur `client` : Chainlit devient client **HTTP**. `packages/client` ne dépend
   **plus** de `support-agent` — vérifié dans l'image construite, pas déduit.

**Reste l'étape 5 (Azure)** : ACR + **App Service** (deux Web Apps sur un plan) +
Flexible Server/pgvector, et l'**identité prouvée** (`user_id` dérivé d'un token
vérifié, jamais lu du corps de la requête). La cible d'exécution est passée de
Container Apps à App Service le **2026-07-28** — bilan de l'inversion : le plan, §6.
⚠️ Le rail de déploiement LangGraph (`langgraph.json` / LangSmith Deployment) est
**écarté** — son contrat public est le graphe, ce qui rouvrirait le contournement du
garde de sortie corrigé en `519f254` (raisons détaillées : le plan, §5).

---

## Phase 14 — Mémoire épisodique (apprendre des cas qui ont marché) ✅
**Concept :** l'agent réutilise **la méthode** d'un cas passé résolu, rejoué en
few-shot. À ne pas confondre avec le sémantique (des *faits* sur UN client) : un
épisode est **partagé entre clients** — c'est le but, et c'est le risque.
**Livrable :** la boucle complète, un seul appel LLM et il est **hors ligne**.

- `memory/episodic.py` — schéma `Episode` en 4 champs (`observation` / `thoughts`
  / `action` / `result`, repris de LangMem ; `thoughts` est le champ qui distingue
  un épisode d'un doublon de la FAQ), écriture, rappel, rendu du bloc de prompt.
- Nœud **`close_turn`** — écrit un *candidat* (1 upsert, **0 modèle**), une clé par
  `thread_id` : le debounce sort des données, pas d'un timer en RAM.
- **`make consolidate`** — la distillation, hors ligne, à blanc par défaut. Lit le
  fil chez le checkpointer (le candidat ne stocke qu'un pointeur).
- Injection few-shot dans le nœud `model`, **après** le prompt stable (cache).
- 28 tests offline. **Trois garde-fous** : on n'apprend que des cas **résolus**
  (`not handled_by_human`) ; **plancher de pertinence** `EPISODIC_MIN_SCORE` (sans
  lui, le 1er épisode écrit atterrit dans toutes les conversations — défaut réel,
  attrapé par un test) ; **anonymisation à l'écriture** (prompt + `ToolGuard`).
- Kill switch `EPISODIC_MEMORY_ENABLED` : off = prompt identique octet pour octet.

**Mesuré le 2026-07-28** (7 cas × 2 répétitions × 2 bras, vivier de 6 épisodes sur
des sujets hors dataset) : **22/22 assertions dans les deux bras** → aucune
régression, et **~300 ms** de coût par tour support (aller-retour embeddings,
isolé au microbenchmark ; le delta bout-en-bout est noyé dans le bruit). Le
**gain n'est pas prouvé** et ce dataset ne peut pas le prouver : l'agent y était
déjà à 100 % et 1 cas sur 7 seulement a franchi le plancher de pertinence.
Détail et protocole : [`docs/memoire.md`](docs/memoire.md).

**Reste ouvert :** programmer la consolidation (cron App Service),
plafonner/dédupliquer le vivier, court-circuiter le rappel sur vivier vide, et —
si on veut vraiment chiffrer le gain — un dataset de cas *ratés* + un juge
sémantique, ce qui est un chantier d'évaluation à part entière.

⛔ **La mémoire procédurale ne sera PAS construite** (décidé le 2026-07-28). Elle
reste documentée comme piste « pour aller plus loin », pas comme tâche en attente :
un system prompt qui s'auto-modifie se trompe sur *toutes* les conversations à la
fois, là où un épisode se trompe cas par cas. La taxonomie de la mémoire est donc
**close** : court terme + sémantique + épisodique.

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
- [x] Phase 12 — sécurité & guardrails (A entrée + B sortie/anti-fuite + C outils & mémoire)
- [ ] Phase 13 — exposition & déploiement 🚧 (étapes 1 à 4/5 faites ; **l'étape 5
      Azure est EN PAUSE** depuis le 2026-07-28 — droits à obtenir côté centre de
      formation, blocage externe, pas technique)
- [x] Phase 14 — mémoire épisodique (candidat 0-LLM + consolidation hors ligne + few-shot plancheré)
- [ ] Phase 11 — cycle de vie du support (Case + Ticket) *(réordonnée après la 12, puis après la 13)*

> Note : Phases 11 et 12 **réordonnées** — la sécurité (guardrails) passe avant le
> cycle de vie du support, car elle est critique dès qu'un vrai client parle.
>
> Note (2026-07-25) : la **Phase 13 passe aussi devant la 11**. L'agent n'est
> appelable par personne, ce qui bloque à la fois la livraison de la formation, le
> vrai front (B2) et l'identité prouvée (le `user_id` signé n'a pas de frontière
> réseau à défendre tant qu'il n'y a pas d'API). Le cycle de vie du support
> (Phase 11) est une **complétude métier** : il attend sans rien bloquer.
>
> Note (2026-07-28) : l'étape 5 (Azure) est **bloquée par un droit d'accès** côté
> centre de formation — rien de technique, donc rien à débloquer par le code. La
> **Phase 14** (mémoire épisodique) a été faite pendant cette attente : elle ne
> dépend d'Azure que sur un point, la planification de `make consolidate`, qui
> restera manuelle jusqu'au déblocage.
