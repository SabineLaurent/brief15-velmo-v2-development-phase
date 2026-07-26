# 📋 TODO — priorités de travail

> Document **vivant** et **transverse**. Les ROADMAPs décrivent le *chemin
> pédagogique* ([`ROADMAP.md`](ROADMAP.md) scope A · [`docs/roadmap-frontend.md`](docs/roadmap-frontend.md)
> scope B). Ce fichier-ci décrit **ce qu'on fait ensuite et dans quel ordre**,
> y compris ce qui ne rentre dans aucune phase (dettes, correctifs d'audit).
>
> Convention : ⬜ à faire · 🚧 en cours · ✅ fait. On coche au fil de l'eau.

---

## Ordre de traitement

| # | Chantier | Pourquoi maintenant | État |
|---|---|---|---|
| 1 | Audit A2 — escalade cassée via la couture | bloquait **B1.6** (le cas escalade dans l'UI) | ✅ |
| 2 | Audit A1 — guard de sortie contourné en streaming | **sécurité** sur le seul chemin client réel | ✅ |
| 3 | **Déploiement conteneurisé** (Docker → Azure), 5 étapes | l'agent doit devenir un **service appelable** — objectif de livraison de la formation | 🚧 |
| 3bis | Revue de code — **C2** (adresses boutique caviardées) · **C3** (500 au lieu de 401) | dégradaient des réponses **aujourd'hui** ; petits, faits avant l'étape 5 | ✅ |
| 3ter | Revue de code — **C1** : escalade sans reprise via HTTP | 🔴 **le fil est condamné** après une escalade ; se décide **avec** l'étape 5 | ⬜ |
| 4 | B1.4 — session, `thread_id` & identité | **reporté** : l'identité se règle à l'étape 5 du déploiement, là où la frontière réseau existe (elle existe depuis l'étape 4) | ⬜ |
| 5 | Reliquat d'audit — I2 · Q1 · Q2 | seuls findings encore ouverts ; conditionne l'archivage de l'audit | ⬜ |
| — | Ingestion prod-grade de la base de connaissance | **Phase 13**, pas avant | 📌 |
| — | Index FAQ persistant (Chroma) | ❌ **abandonné** — voir ci-dessous | 🚫 |
| — | Cache de la dimension d'embeddings | ⏸️ suspendu — même logique | 🚫 |
| — | Mémoire longue & changement de modèle d'embeddings | piège documenté, pas urgent | ⬜ |

---

## 🚫 Abandonné — index FAQ persistant (Chroma)

**Codé, mesuré, puis annulé** (revert de `e4e8447`, 2026-07-21). À garder comme
exemple : la décision d'annuler valait mieux que le code.

**Ce qui a été construit :** index Chroma persistant sous `TEMP/database/chroma`,
protégé par une empreinte (fichiers FAQ + modèle d'embeddings + découpage), IDs de
chunks stables, `make reindex`, 15 tests. Tout fonctionnait, vérifié en live.

**Pourquoi c'était une erreur — deux raisons, la seconde étant décisive :**

1. **YAGNI.** Le gain mesuré : **1,63 s → 0,68 s au démarrage**, sur 4 fichiers /
   8 chunks, en dev. Payé **26 paquets et ~160 Mo** (dont `onnxruntime` et un
   client `kubernetes` totalement inutilisés ici).
2. **Ce n'est pas le pattern prod.** En production, **l'application n'indexe pas
   au démarrage** : l'ingestion est un **job découplé** (voir la section suivante).
   On optimisait donc une étape qui, en prod, n'existe pas à cet endroit.

**Ce qui reste vrai et mérite d'être retenu :**
- un index persistant est une **projection** de la source ; sans garde-fou, il
  sert des réponses périmées **sans lever d'erreur** — c'est le vrai risque, et
  c'est ce qu'une empreinte adresse ;
- `add_documents` **empile** sur une collection persistante : sans IDs stables, on
  duplique tout à chaque reconstruction ;
- une empreinte **globale** impose de tout reconstruire pour un fichier modifié —
  inacceptable à 200 fichiers, d'où l'incrémental **par fichier** en prod.

**État actuel du code :** `InMemoryVectorStore` reconstruit à chaque démarrage
(~1,6 s, zéro dépendance). Assumé comme pattern de démo.

---

## 📌 Phase 13 — Ingestion prod-grade de la base de connaissance

**Le vrai sujet**, à traiter avec l'exposition/déploiement (Phase 13), pas avant.

**Le principe :** séparer **ingérer** de **servir**.

```
DEV (aujourd'hui)              PROD (cible)
─────────────────              ────────────
l'app démarre                  job d'ingestion (CI / cron / commande admin)
  └─ indexe                      └─ met à jour l'index quand la FAQ change
  └─ répond                                   │
                               l'app démarre ─┘ se connecte, interroge
                                 └─ n'indexe JAMAIS
```

Sans ce découplage, N instances derrière un load-balancer ré-indexent chacune la
même FAQ au démarrage, et se marchent dessus.

**À traiter alors :**
- **Ingestion incrémentale** — hash **par fichier** : ne ré-embedder que ce qui a
  changé, supprimer les chunks des fichiers disparus. (`langchain_core.indexing.index()`
  existe avec `cleanup="incremental"`, mais exige un `RecordManager` **durable** —
  seul `InMemoryRecordManager` est fourni.)
- **Vector store partagé** — Chroma en mode serveur (`host`/`port`, même classe) ou
  autre base managée ; l'index cesse d'être un fichier local.
- **Qualité du retrieval** — le sujet qui compte vraiment pour un agent de support :
  fraîcheur/versionnage de la FAQ, reranking, évaluation de la **recherche**
  elle-même (pas seulement de la réponse finale), multi-tenant.

---

## ⏸️ Suspendu — cache de la dimension d'embeddings

`memory/long_term.py:60` sonde le modèle (`embed_query("probe")`) à chaque
démarrage pour découvrir la dimension des vecteurs — **un** appel réseau.

Mémoïser ce nombre est trivial (~10 lignes), mais le bénéfice l'est tout autant :
un appel par démarrage de process. Le chantier n'existait que pour compléter
l'index persistant (« zéro appel réseau au boot ») ; celui-ci abandonné, il perd
sa raison d'être. **À reprendre uniquement si la sonde devient réellement gênante.**

---

## Chantier 1 — Audit A2 : escalade cassée via la couture ✅

Réf. [`docs/audit-code-2026-07-19.md`](docs/audit-code-2026-07-19.md) §A2.
Quand le routeur choisit `escalate`, le graphe se met en pause (`interrupt()`) ;
`stream_reply` ne streamait rien et son repli ne trouvait pas d'`AIMessage` →
**bulle vide** dans Chainlit, payload `__interrupt__` ignoré.

**Résolu** (`519f254`) : `api.py` teste `result.get("__interrupt__")` et livre
`ESCALATION_PENDING_MESSAGE`. La règle qui en découle est désormais un invariant
documenté du package : *tout chemin livre exactement un chunk non vide*
(réponse normale, input bloqué, plantage, escalade en pause).

---

## Chantier 2 — Audit A1 : guard de sortie contourné en streaming ✅

Réf. [`docs/audit-code-2026-07-19.md`](docs/audit-code-2026-07-19.md) §A1.
`stream_reply` diffusait les tokens **avant** que `guard_output` ne s'exécute : sur
le seul chemin client réel (Chainlit), la rédaction PII/secrets et la protection
anti-fuite du system prompt étaient **cosmétiques**.

**Résolu** (`519f254`) : l'arbitrage streaming ⇄ sécurité a été tranché en faveur
de la **sécurité**. La couture ne streame plus les tokens des nœuds `answer`/`model` ;
elle livre l'**état terminal du graphe**, donc le message déjà passé par
`guard_output`, en un seul chunk. Garder et streamer sont exclusifs : c'est
maintenant écrit noir sur blanc dans `packages/support-agent/CLAUDE.md`, avec
l'interdiction explicite de revenir en arrière.

⚠️ Conséquence à assumer côté UI : plus d'effet machine à écrire tant que la
réponse n'est pas complète. Si la latence perçue devient gênante, le levier est
la **phase B1.5** (plusieurs chunks côté couture) ou le *smoothing* côté front —
jamais un accès direct au graphe.

---

## Chantier 3 — Déploiement conteneurisé (Docker → Azure) 🚧

**Le plan complet, avec le but de chaque étape et le glossaire des acronymes :**
[`docs/plan-deploiement-2026-07-25.md`](docs/plan-deploiement-2026-07-25.md).
À lire avant d'écrire du code d'exposition ou d'infra.

**Pourquoi ce chantier passe devant B1.4 :** l'agent n'est appelable par personne
(un CLI + un import Python). C'est le bloquant n°1 de
[`docs/perimetre-final.md`](docs/perimetre-final.md) §B.1, et c'est aussi ce qui
crée la **frontière réseau** sans laquelle le trou 🔴 du `user_id` non signé n'est
pas défendable — donc B1.4 y gagne en attendant.

| Étape | Livrable | État |
|---|---|---|
| 1 | Service HTTP : `POST /chat` (SSE), `/health`, `/ready`, clé de service + tests de contrat de la couture | ✅ |
| 2 | Image Docker de la tranche `support-agent` (état sur volume) | ✅ |
| 3 | Postgres + pgvector réellement exercé (`PERSISTENCE_BACKEND=postgres`) | ✅ |
| 4 | Conteneur `client` : Chainlit devient client **HTTP** | ✅ |
| 5 | Azure : ACR + Container Apps + Flexible Server, **identité prouvée** | ⬜ |

**Étape 4 close (2026-07-25) — ce qu'elle a prouvé, chiffré :** `app.py` a changé
d'**une ligne d'import**, le corps du handler d'**aucun caractère**. `support-agent`
est sorti des dépendances de `packages/client` ; l'image du client (405 Mo) ne
contient ni `support_agent`, ni `langgraph`, ni `langchain` — vérifié dans l'image
construite, pas déduit. Le client ne reçoit plus que **deux variables**
(`AGENT_API_URL`, `AGENT_API_KEY`) là où il voyait tout le `.env` de l'agent.

**Décidé et à ne pas rouvrir sans raison neuve :** le rail de déploiement LangGraph
(`langgraph.json` / LangSmith Deployment) est **écarté** — son contrat public est le
graphe, il double notre persistance, et il exige une clé LangSmith même en local
(détail et sources : le plan, §5). Retenu : **FastAPI mince autour de `stream_reply`**.

---

## Chantier 3bis / 3ter — Défauts de la revue de code du 2026-07-25

Réf. [`docs/revue-code-2026-07-25.md`](docs/revue-code-2026-07-25.md).

| # | Défaut | État |
|---|---|---|
| **C2** 🔴 | Le garde de sortie caviardait `pro@velmo.example` / `privacy@velmo.example`, qui **sont** la réponse de deux fiches FAQ | ✅ allowlist de domaines propriétaires (`GUARDRAILS_OWNED_EMAIL_DOMAINS`), asymétrique : sortie seulement |
| **C3** 🟠 | Une clé d'API non-ASCII rendait `500` au lieu de `401` | ✅ comparaison en **octets** — la classe entière disparaît |
| **C1** 🔴 | **Aucun chemin de reprise** après un `interrupt()` via HTTP : une escalade condamne le fil, tous les messages suivants reçoivent la même phrase, sans erreur nulle part | ⬜ **décision de conception à trancher** |

**C1 — la question à trancher avant d'écrire une ligne** (les deux options se
défendent, elles ne coûtent pas la même chose) :

1. **Rendre la reprise possible** — route `POST /chat/{thread_id}/resume` + exposition
   du payload d'`interrupt()` (déjà structuré : `reason` / `user_id` /
   `customer_message`). C'est ce que l'audit A2 demandait, et ça suppose **un
   opérateur branché** quelque part.
2. **Ne pas mettre le graphe en pause du tout** — `escalate` ouvre un ticket via le
   port `actions/` et **termine le tour**. Le fil reste vivant, l'escalade devient
   asynchrone : le comportement d'un vrai service de support, et ça ne suppose
   personne au bout du fil.

⚠️ Dans les deux cas, le test qui manque est le même : **deux tours sur le même
`thread_id` après une escalade**. Aucun test à un seul tour ne voit ce trou.

Se décide **avec l'étape 5** du déploiement, pas contre elle.

---

## Chantier 5 — Reliquat d'audit : I2, Q1, Q2 ⬜

Réf. [`docs/audit-code-2026-07-19.md`](docs/audit-code-2026-07-19.md). Les trois
seuls findings encore ouverts (A1, A2, I1 et Q3 sont réglés) :

- **I2 — providers annoncés sans paquet d'intégration.** `groq`, `google_genai`,
  `azure_ai` sont dans `_PROVIDER_ALIASES` et `.env.example`, mais les paquets
  `langchain-*` correspondants ne sont pas installés (`pyproject.toml:20` n'est
  qu'un commentaire) → `ImportError` brut au runtime. Correctif minimal : envelopper
  l'erreur avec un message actionnable (« installe `langchain-groq` »).
- **Q1 — `honest_refusal` trop laxiste.** `eval/evaluators.py:61-62` compte
  `"support"` et `"contact"` comme signaux de refus honnête : deux mots omniprésents
  chez un agent *de support*. L'évaluateur passe donc presque toujours.
- **Q2 — les évaluateurs supposent `content: str`.** Certains providers renvoient
  une liste de blocs → `AttributeError`. Fragile pour l'agnosticisme revendiqué.

**Une fois les trois traités :** archiver l'audit dans `docs/archive/` avec son
bandeau (règle de [`docs/archive/README.md`](docs/archive/README.md)) — il devient
un instantané daté, pas une liste de tâches.

---

## Chantier 4 — B1.4 : session, `thread_id` & identité ⬜

Reprise de [`docs/roadmap-frontend.md`](docs/roadmap-frontend.md).
**Reporté après le chantier 3** (2026-07-25) : le `user_id` prouvé n'a de sens qu'une
fois la frontière réseau posée — tant que Chainlit importe la couture en in-process,
il n'y a rien à usurper. L'étape 4 du déploiement (Chainlit → client HTTP) recouvre
d'ailleurs une partie du travail de session.

---

## Chantier 6 — Mémoire longue & changement de modèle d'embeddings ⬜

Le store long terme indexe ses souvenirs avec les mêmes embeddings, **persistés en
SQLite** depuis la Phase 10. Changer `EMBEDDINGS_MODEL` rendrait la recherche
mémoire silencieusement incohérente — et contrairement à la FAQ (reconstructible
depuis `data/kb-velmo/`), les souvenirs n'existent **que** dans la base : il faudrait
les **ré-embedder** un par un. C'est une **migration**, pas un rebuild.

Ne se déclenche que si on change de modèle d'embeddings — improbable en tuto.
**Action minimale retenue : documenter le piège**, coder la migration seulement
si le cas se présente.

---

## Divers (petit, à caser)

- [x] Indexer [`docs/audit-code-2026-07-19.md`](docs/audit-code-2026-07-19.md)
      dans la section « Where things live » du `CLAUDE.md` racine (fait : `6575c0e`,
      avec `TODO_priorities.md` et `vision.md`).
