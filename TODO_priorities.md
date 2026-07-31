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
| 3ter | Revue de code — **C1** : escalade sans reprise via HTTP | 🔴 **le fil est condamné** après une escalade | ✅ |
| 3quater | **CI** (GitHub Actions) — la garde, puis l'image | **prérequis technique de l'étape 5** : l'image poussée sur ACR ne peut pas être construite sur un Mac **arm64** | 🚧 **CI-1 la garde ✅** · **CI-2 les images amd64 ✅** (`make docker-smoke`) · **CI-3 la publication ⬜** (étape 5, avec l'ACR) |
| 4 | B1.4 — session, `thread_id` & identité | **reporté** : l'identité se règle à l'étape 5 du déploiement, là où la frontière réseau existe (elle existe depuis l'étape 4) | ⬜ |
| 3ter bis | **Corpus d'acceptance du starter** — les 3 `eval/*.jsonl` portés et exécutés | la matière à noter du chantier 3 (MLOps) ; découpe mémoire/garde-fous/qualité prête | ✅ |
| 7 | **Étage MLOps** — note globale, seuil bloquant, rapport, baseline | le **dernier** des trois chantiers du brief encore ouvert ; l'étape 5 Azure est bloquée par un droit d'accès, celui-ci ne dépend de personne | ✅ |
| 5 | Reliquat d'audit — **Q2** (I2 et Q1 faits) | dernier finding ouvert ; conditionnait l'archivage de l'audit — **fait, audit archivé** | ✅ |
| 8 | **La mémoire n'est notée que sur SQLite** — or la prod est Postgres | **R3 est une propriété de sécurité** vérifiée sur le mauvais moteur ; à fermer **avant** l'étape 5 Azure | ✅ |
| — | **Quota LangSmith épuisé** — plus aucune trace enregistrée | bloque `make eval` en pratique ; **rien à corriger dans le code** | 🔒 externe |
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

Réf. [`docs/archive/audit-code-2026-07-19.md`](docs/archive/audit-code-2026-07-19.md) §A2.
Quand le routeur choisit `escalate`, le graphe se met en pause (`interrupt()`) ;
`stream_reply` ne streamait rien et son repli ne trouvait pas d'`AIMessage` →
**bulle vide** dans Chainlit, payload `__interrupt__` ignoré.

**Résolu** (`519f254`) : `api.py` teste `result.get("__interrupt__")` et livre
`ESCALATION_PENDING_MESSAGE`. La règle qui en découle est désormais un invariant
documenté du package : *tout chemin livre exactement un chunk non vide*
(réponse normale, input bloqué, plantage, escalade en pause).

---

## Chantier 2 — Audit A1 : guard de sortie contourné en streaming ✅

Réf. [`docs/archive/audit-code-2026-07-19.md`](docs/archive/audit-code-2026-07-19.md) §A1.
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
| 4bis | **CI** : la garde (lint + tests) puis l'image **amd64** — voir chantier 3quater | ✅ sauf la **publication** (CI-3), qui appartient à l'étape 5 ci-dessous |
| 5 | Azure : ACR + **App Service** (2 Web Apps, 1 plan) + Flexible Server/pgvector, **identité prouvée** | ⬜ |

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
| **C1** 🔴 | **Aucun chemin de reprise** après un `interrupt()` via HTTP : une escalade condamne le fil, tous les messages suivants reçoivent la même phrase, sans erreur nulle part | ✅ escalade **asynchrone** (ticket + `handled_by_human` + nœud `human_takeover`), sur le modèle des vraies plateformes — [`docs/escalade.md`](docs/escalade.md) |

**C1 — tranché le 2026-07-26.** Les deux options proposées par la revue (reprise
synchrone via `/resume`, ou escalade asynchrone) ont été départagées par un critère
extérieur : **ce que fait une plateforme de support en production**. Rien n'y est en
pause — une conversation est une ligne en base avec un statut et un assigné, et
escalader c'est *réassigner*, pas *suspendre*. Écartées au passage : la reprise
synchrone (elle suppose un opérateur qui n'existe pas) et la scission de thread
(inexistante en prod, et elle rend le nouveau fil amnésique).

**Reste ouvert, désormais nommé au lieu d'être silencieux :** le **canal de retour**
de la réponse humaine (le SSE est portée-requête → c'est un problème de transport,
scope B + Phase 11), la **console d'opérateur**, et la **sortie du takeover** (rien
ne remet `handled_by_human` à `False` — prudent et voulu tant que personne ne peut
clore un dossier). Détail : [`docs/escalade.md`](docs/escalade.md).

---

## Chantier 3quater — CI (intégration continue), avant la CD Azure 🚧 (CI-1 ✅ · CI-2 ✅ · CI-3 ⬜)

**Pourquoi maintenant, et pas « parce que ça se fait ».** La CI est ici un
**prérequis technique** de l'étape 5, pas une bonne pratique optionnelle :

| Le fait | La conséquence |
|---|---|
| La machine de dev est **arm64**, App Service Linux exécute du **linux/amd64** | L'image construite par `make docker-build` **ne démarrera pas** sur Azure. Il faut un constructeur amd64 — c'est-à-dire un runner de CI |
| La CD pousse une image sur ACR | Sans CI, on pousse une image bâtie sur un poste, depuis un arbre de travail dont **rien ne prouve** qu'il correspond à un commit |
| Le pipeline CI **est** le brouillon de la CD | Écrire la CD sans CI, c'est écrire deux fois le même script de build |

**Ce que ce projet a déjà, et qui rend la CI presque gratuite :**
- `make check` (ruff + pytest) tourne en **~40 s**, **sans réseau et sans secret** :
  `test_eval.py` se `skip` tout seul quand aucune clé provider n'est présente
  (`pytestmark = skipif(not _has_llm_credentials())`), et il n'y a **pas** de `.env`
  dans un checkout propre. La CI n'a donc **rien à configurer** pour être verte.
- **Un seul `uv.lock`** partagé : `uv sync --frozen` réinstalle exactement ce qui a
  été testé en local. C'est le gain workspace, encaissé une seconde fois.
- Les deux `Dockerfile` se construisent déjà depuis la **racine** — le contexte de
  build est le même en CI qu'en local.

**Découpe proposée, dans l'ordre (chaque étape a un but distinct) :**

- **CI-1 — la garde (aucun secret).** ✅ `push` + `pull_request` : `uv sync --frozen`,
  `ruff check`, `pytest`. Plus une vérification que le **lock est à jour**
  (`uv lock --check`) : un `pyproject.toml` modifié sans relock casse la
  reproductibilité de l'image, en silence. Livrable : un rouge/vert qui veut dire
  quelque chose.
- **CI-2 — l'image, en amd64.** ✅ **fait le 2026-07-31** — voir le bilan ci-dessous.
  Construire les **deux** images (agent + client) sur le runner,
  `--platform linux/amd64`, **sans pousser**, puis exécuter les deux assertions qui
  n'étaient vérifiées qu'à la main.
- **CI-3 — la chaîne complète (= la CD).** ⬜ Pousser sur ACR puis mettre à jour l'image
  des deux Web Apps. **Ne se fait qu'à l'étape 5**, avec les secrets Azure.

**Deux pièges à traiter dès CI-1 :**
- **Python 3.12.** Les paquets déclarent `>=3.11,<3.14` mais le projet cible 3.12 ;
  épingler la version du runner, sinon la CI teste un interpréteur que personne
  n'utilise.
- **Ne pas faire fuiter les tests « live » dans la garde.** L'éval LangSmith
  (`make eval`), le Postgres réel et les appels LLM ont besoin de secrets et de
  réseau : ils appartiennent à un **second étage**, déclenché à la main ou sur
  `main`, jamais au chemin qui doit rester vert et rapide sur chaque commit.

### CI-2 — fait le 2026-07-31 : `make docker-smoke` + le job `images`

**Une confusion levée d'abord, et c'est le vrai gain.** `ci.yml` et `docs/ci.md`
affirmaient tous deux que la moitié « image » attendait l'étape 5, « parce qu'elle
a besoin d'un ACR qui n'existe pas encore ». C'était confondre **construire** et
**publier** : seul le *push* demande un registre et des secrets. La moitié
vérifiable était donc faisable depuis le premier jour, et son absence nous
promettait de découvrir un build cassé **au déploiement** — au moment où on a le
moins envie de déboguer un `exec format error`.

**Le point dur, et la seule chose qui n'était pas évidente.** Le *lifespan* de
l'agent construit l'index FAQ **avant** d'ouvrir son port. Un conteneur sans
modèle d'embeddings joignable ne démarre donc pas du tout : « l'image répond sur
`/health` » est **impossible à vérifier sans clé**, ce que la découpe d'origine
n'avait pas vu. Réponse : servir l'**unique dépendance réseau du démarrage**
depuis un **stub local** parlant l'API d'embeddings OpenAI, branché par le rail
`openai_compatible` *qui existe déjà*. Aucun secret ⇒ la garde reste sur toutes
les branches. Bonus non prévu : c'est la **première fois que le rail
`openai_compatible` — celui d'Azure OpenAI — est exercé bout en bout**.

**Trois choses que l'exécution a apprises, et qu'aucune relecture n'aurait données :**

1. 🔴 **`GET /chat` répond `405`, avant toute dépendance.** La première version
   sondait l'authentification avec un GET : elle recevait 405 et n'avait donc rien
   testé de l'auth — la même sonde aurait été **verte sur un serveur grand
   ouvert**. Corrigé en `POST` + corps **valide** (un corps invalide déplace le
   problème d'un cran : 422 avant 401).
2. 🔎 **`tiktoken` télécharge `cl100k_base` depuis un CDN OpenAI au démarrage** —
   7,3 s, et **échec complet** sous `docker run --network none` (vérifié). Sans
   conséquence aujourd'hui (le rail par défaut est `mistral`), mais un **Azure
   OpenAI derrière un VNet fermé ne démarrerait pas**. À reverser dans l'étape 5.
3. **L'index FAQ n'est pas le coût du démarrage** : 2 appels d'embeddings pour 17
   vecteurs. Le stub compte et affiche ces deux nombres exprès — une durée
   d'échauffement sans eux ne dit pas si on a payé un batch ou trente allers-retours.

**Vérifié, pas déduit :**

| Chemin | Résultat |
|---|---|
| `make docker-smoke` (arm64 natif, builds en cache) | les 2 images OK, **44 s** bout en bout |
| Sonde « pas de cerveau » retournée contre l'image de l'**agent** | **sortie 1**, les 4 modules nommés — l'assertion peut donc échouer |
| `make -n docker-build PLATFORM=linux/amd64` | `docker build --platform linux/amd64 …` sur les deux images |
| `docker run --network none … tiktoken` | échec sur `openaipublic.blob.core.windows.net` |
| `ruff check .` | propre (le script est dans le périmètre du lint) |

⚠️ **Ce qui n'est PAS prouvé sur cette machine :** que le build **amd64** aboutit.
Sur un Mac arm64 il passerait par l'émulation ; c'est le runner amd64 de la CI qui
le prouvera, et c'est exactement la raison d'être du job. Le premier run de
`images` sur GitHub est donc le vrai verdict.

---

## Chantier 3ter bis — Corpus d'acceptance du starter : les trois portés ✅

**Fait le 2026-07-29.** Les trois `eval/*.jsonl` du starter vivent en `data/eval/`,
byte-identiques, lus par `eval/corpus.py` et **exécutés** : 35 cas garde-fous
(hors ligne), 12 cas mémoire (hors ligne, 19 tests), 7/8 cas qualité (intégration,
**7/7 en live, stable sur deux runs**). Détail et décisions : ROADMAP §Phase 9.

**Ce que ça débloque pour le chantier 3 (MLOps) :** la matière à noter existe
maintenant en trois familles, ce qui est exactement la découpe que
`test_mlops.py` réclame (`scores.memory` / `scores.guardrails` / `scores.quality`).
Ce qui manque n'est plus des cas, c'est l'**agrégation** en note globale, le
`enforce_threshold` bloquant et le `write_report`.

**Ce que ça a mesuré au passage, et qui pointe le même endroit :** 2 des 7 attentes
qualité sont des *notations* (`prepared`, `J+2`) et non des faits, et la surface de
la réponse varie d'un run à l'autre à fait constant. Avec le flake de
`honest_refusal`, ça fait **deux dettes qui convergent sur le juge sémantique** —
elles se règlent ensemble, pas séparément.

---

## Chantier 7 — L'étage MLOps : noter, bloquer, rapporter ✅

> ⚠️ **Collision de numérotation à ne pas confondre.** Le « chantier 3 » du *brief*
> ([`docs/brief/chantier3-evaluation-et-mlops.md`](docs/brief/chantier3-evaluation-et-mlops.md))
> est **Évaluation & MLOps** — c'est celui-ci. Le « Chantier 3 » de *ce fichier* est
> le **déploiement**. Trois chantiers au brief : mémoire ✅, garde-fous ✅, et
> celui-ci — le dernier ouvert.

**Le contrat, écrit noir sur blanc** dans
[`docs/brief/tests-reference/test_mlops.py`](docs/brief/tests-reference/test_mlops.py) —
cinq symboles qui n'existaient nulle part ici :

| Symbole | Ce qu'il doit faire |
|---|---|
| `run_eval(...) → Scores` | **quatre** notes : `global_`, `memory`, `guardrails`, `quality` |
| `current_version()` | les notes doivent être **versionnées** |
| `enforce_threshold(scores, 0.8)` | lève `DeliveryBlocked` sous le seuil |
| `write_report(scores, path)` | **cinq** signaux visibles : note mémoire, taux de blocage, taux de faux positifs, latence, coût |
| `DeliveryBlocked` | l'exception qui arrête la livraison |

La **matière** existe déjà (chantier 3ter bis) : 35 + 12 + 7 cas exécutés. Ce qui
manque, c'est l'agrégation, le seuil et le rapport. Autrement dit : on **mesure**,
mais on ne **note** pas, et rien ne **bloque**.

### La tension du brief, et comment on la tranche

`reco_expert.md:25` demande, dans une seule phrase, « **blocage** dès que la note
passe sous le seuil » **et** « sans bloquer pour du **bruit** ». Or ce dépôt a déjà
documenté (2026-07-26) qu'un évaluateur est **instable** — `honest_refusal` — avec
l'argument de ne pas le rustiner. Brancher un seuil bloquant sur un corpus qui
contient une métrique instable, c'est programmer un rouge intermittent, et une CI
dont on dit « c'est encore lui, relance » ne garde plus rien.

**Ce que fait le métier** (et que le brief simplifie) : personne ne bloque sur une
moyenne unique. Une moyenne **masque** — la sécurité qui tombe de 100 % à 80 %
disparaît dedans si la qualité progresse. Le blocage réel est **par dimension**,
avec des régimes distincts, et la non-régression est **relative** à la version
précédente, pas absolue : passer de 0,95 à 0,82 franchit un seuil de 0,8 les doigts
dans le nez tout en étant une régression franche.

**Donc on livre le contrat à la lettre, et la vraie logique dessous.**

### Les six décisions actées (2026-07-29)

1. **La note globale est un chiffre de RAPPORT, pas une porte.** Moyenne non
   pondérée des dimensions *mesurées*. Non pondérée exprès : pondérer une moyenne
   de reporting, c'est exactement là qu'on cache une régression.
2. **Les portes sont par dimension.** `HARD_FLOORS = {guardrails: 1.0, memory: 1.0}` —
   ces deux dimensions sont **déterministes** (aucun appel LLM), donc 100 % est le
   seul plancher sensé : un jailbreak qui passe n'est pas « acceptable à 0,8 ». La
   qualité, non déterministe, n'a **pas** de plancher absolu.
3. **La non-régression se compare à une BASELINE versionnée** (`data/eval-baseline.json`,
   commitée, mise à jour **explicitement** quand on accepte un nouveau niveau).
   Sans elle, `enforce_threshold` ne prouve rien — et « non-régression » est le mot
   du brief.
4. **La tolérance s'exprime en CAS, pas en points** : `max_regression_cases = 1`.
   Sur 7 cas de qualité, 1 cas = 0,143 point ; un seuil en points serait un nombre
   inventé. Et le chiffre vient d'une **mesure de ce dépôt** (2 attentes sur 7
   instables, cf. `eval/corpus.py`) : un cas perdu est un tirage au sort, deux sont
   un signal.
5. **La porte CI ne note que le DÉTERMINISTE.** C'est déjà la doctrine défendue en
   tête de [`.github/workflows/ci.yml`](.github/workflows/ci.yml) — on l'étend, on
   ne la contredit pas. Les corpus live alimentent le **rapport**, jamais le
   blocage.
6. **Aucun prix inventé.** Les **tokens** sont toujours rapportés (lus dans
   `usage_metadata`, déjà disponible grâce à `stream_usage=True`) ; une somme en
   euros n'apparaît que si un barème est **configuré**. Un tarif de mémoire serait
   un chiffre faux dans un rapport de décision.

**Deux conséquences qui tombent gratuitement :**

- **L'agent dégradé du test de régression n'est pas une doublure de test** : c'est
  `GUARDRAILS_ENABLED=false`, un vrai commutateur de production. Le starter devait
  écrire une classe `AllowAllGuardrails` ; ici le kill switch **est** la
  dégradation — donc le test mesure quelque chose qui peut réellement arriver en
  prod.
- **`current_version()` = SHA git + provider/modèle + empreinte des corpus.** Une
  note n'est comparable que si le **jeu de données** est identique : une baseline
  enregistrée sous une autre empreinte est **écartée avec un motif**, jamais
  comparée en silence.

### Découpe

- [x] **M1 — le harnais hors ligne** (`eval/offline.py`) : `CorpusRun`/`CaseResult`,
      `score_guardrails(enabled=…)`, `score_memory(workdir)`. La plomberie
      (embeddings déterministes, padding 30 tours, plancher d'oubli) est
      **factorisée** depuis `tests/test_memory_cases.py` au lieu d'être dupliquée,
      et `eval/corpus.py` gagne `corpus_fingerprint()` + `DELIBERATELY_NOT_BLOCKED`.
- [x] **M2 — l'étage** (`eval/mlops.py`) : `Scores`/`Dimension`, `current_version()`,
      `run_eval()`, `enforce_threshold()`, `DeliveryBlocked`, `Baseline` + load/save,
      `write_report()`, la CLI.
- [x] **M3 — les tokens** : `eval/run.py` expose `usage` dans le retour de
      `make_target` ; `config.py` gagne le barème optionnel (`EVAL_PRICE_PER_1M_*`,
      vides par défaut).
- [x] **M4 — les tests** (`tests/test_mlops.py`, 11 tests) et le raccordement de
      `test_moderation.py` / `test_memory_cases.py` sur la plomberie partagée
      (~60 lignes de fixtures en moins, aucune assertion perdue).
- [x] **M5 — la porte** : `make score`, le câblage CI (hors ligne), la baseline
      initiale commitée (`data/eval-baseline.json`), `docs/ci.md` §8.

### Vérifié, pas déduit (2026-07-29)

| Chemin | Résultat |
|---|---|
| `make score` (hors ligne, = la CI) | mémoire **12/12**, garde-fous **35/35**, globale 1.000, baseline comparée, **sortie 0** |
| `make score ARGS=--degraded` | garde-fous **0.486** (17/35), globale 0.743, **2 motifs** de blocage, **sortie 1** |
| `make score ARGS=--live` | qualité **7/7**, latence p50 **5546 ms** / p95 6241 ms, **26 816 tokens** (25 948 / 868), prix non configuré |
| `uv run pytest` | **250 passed** (dont les tests live) |
| `ruff check .` | propre |

Le motif de blocage du dégradé est **actionnable**, pas binaire : il nomme la
dimension, la note, et les 18 cas en échec un par un (`hate-1`, `violence-1`,
`injection-1`… jusqu'aux 3 cas de sortie).

La latence p50 mesurée ici (**~5,5 s**) recoupe l'investigation TTFT close
([`docs/latence.md`](docs/latence.md)) — même ordre de grandeur, sur un chemin
d'exécution différent. Ce n'est pas une régression, c'est une confirmation
indépendante.

**Ce qui reste ouvert sur ce chantier :** la note de qualité n'a **pas** de
baseline enregistrée (le fichier commité ne porte que le déterministe, seul
recalculable en CI). Tant que personne ne lance
`make score ARGS='--live --update-baseline'`, un run live n'a donc pas de porte de
non-régression sur la qualité — seulement un rapport. C'est un choix, pas un
oubli : une note de qualité commitée depuis un poste, sur un modèle donné,
deviendrait une porte que la CI ne peut pas reproduire.

**Le garde-fou anti-dérive, parce qu'un scoreur qui mesure faux est pire que pas de
scoreur :** les tests **asserten**t (diagnostics riches, un cas par test), le
scoreur **compte** (booléen par cas, ne lève jamais). Deux consommateurs, une seule
plomberie. Et `test_mlops.py` exige les deux dimensions déterministes à **35/35 et
12/12** — si un prédicat du scoreur cesse de correspondre à ce que les assertions
veulent dire, la note tombe, le plancher mord, la CI rougit. La dérive ne peut pas
se signaler comme un succès.

**Déviation argumentée à assumer :** le rapport est en **français accentué**, et nos
tests l'assertent via `fold()`. Le test du starter cherche `"memoire"` après
`.lower()` — ce qui échouerait sur « mémoire ». Écrire « memoire » sans accent pour
faire passer une assertion serait dégrader le livrable pour flatter le test ; c'est
la même classe de bug que `f404775` a déjà payée, et `fold()` est l'outil que ce
dépôt a construit pour elle.

---

## Chantier 5 — Reliquat d'audit : CLOS, audit archivé ✅

Réf. [`docs/archive/audit-code-2026-07-19.md`](docs/archive/audit-code-2026-07-19.md). Il ne reste
qu'**un** finding ouvert — Q2 (A1, A2, I1, I2, Q1 et Q3 sont réglés) :

- ~~**I2 — providers annoncés sans paquet d'intégration.**~~ ✅ **déjà fait** (constaté
  le 2026-07-29 : cette ligne était périmée). `llm/_extras.py` existe, le garde
  `provider_package_required` enveloppe l'import là où il se produit vraiment —
  `llm/factory.py:65` **et** `llm/embeddings.py:49`, les deux endroits qui
  instancient un provider — et `tests/test_llm_extras.py` rend l'invariant
  **exécutable** : ajouter un alias sans déclarer son extra casse un test. Détail
  d'origine : `groq`, `google_genai`, `azure_ai` étaient annoncés dans
  `_PROVIDER_ALIASES` et `.env.example` sans paquet `langchain-*` installé, donc
  `ImportError` brut au runtime.
- ~~**Q1 — `honest_refusal` trop laxiste.**~~ ✅ **fait le 2026-07-26**, forcé par le
  changement de prompt de l'escalade : en retirant « suggère de contacter un
  conseiller », le seul signal que l'évaluateur savait lire a disparu — alors que la
  réponse produite était un refus honnête impeccable. Il détecte maintenant l'**énoncé
  de non-savoir** (« ne précise pas », « je ne peux pas »…) et non le vocabulaire d'un
  agent de support ; il refuserait donc une réponse fabriquée. Détail d'origine : `eval/evaluators.py:61-62` compte
  `"support"` et `"contact"` comme signaux de refus honnête : deux mots omniprésents
  chez un agent *de support*. L'évaluateur passe donc presque toujours.
- **Q2 — les évaluateurs supposent `content: str`.** Certains providers renvoient
  une liste de blocs → `AttributeError`. Fragile pour l'agnosticisme revendiqué.
  **Où il a survécu, précisément** (relevé le 2026-07-29) : la plupart des accès
  sont déjà gardés (`api.py:103`, `memory/compaction.py:157`,
  `memory/consolidate.py:100`, `graph/nodes.py` via `str()`), mais **pas la porte
  d'éval** — `eval/run.py:76` et `:80` prennent `m.content` brut pour construire
  `answer` et `tool_output`, que les évaluateurs passent ensuite à `.lower()` /
  `fold()`. Donc un provider à blocs de contenu ne dégrade pas une réponse : il
  fait **tomber le filet de non-régression**, au moment précis où on change de
  provider. Deux autres points non gardés : `agent.py:106` et `graph/nodes.py:481,533`.

  ✅ **Fait le 2026-07-29** — et l'ampleur réelle était **le double** du relevé :
  **10 sites**, pas 5. Le correctif n'est pas un helper maison : `BaseMessage.text`
  existe (langchain-core 1.5.2) et normalise déjà le cas. Vérifié sur la doc via
  Context7, qui a aussi transformé le statut du finding — la page
  `integrations/chat/google_generative_ai` dit que **Gemini 3 renvoie des blocs**,
  or `google_genai` est dans `_PROVIDER_ALIASES`. Ce n'était donc pas une
  fragilité théorique mais un provider **annoncé dans `.env.example`** sur lequel
  la porte d'éval tombait.

  **Le plus intéressant, et ce qui justifie d'avoir élargi au-delà d'`eval/` :**
  les 5 sites déjà « gardés » par un `str(message.content)` ne corrigeaient rien,
  ils **masquaient**. Pas d'exception, mais un `repr` Python scanné par les
  gardes-fous (`nodes.py:145`, `:568`) et injecté dans le prompt de compaction
  (`compaction.py:157`). Un garde qui scanne la mauvaise chaîne sans le dire est
  la pire forme que pouvait prendre ce bug. `api.py:103` était du même ordre :
  `isinstance(content, str)` ne plantait pas, il servait `GRACEFUL_ERROR_MESSAGE`
  par-dessus une réponse parfaitement bonne.

  Invariant rendu **exécutable** (`tests/test_message_text.py`, 10 tests) en
  lisant l'**AST** du package : zéro accès `.content`. À noter — un `grep` lancé
  juste avant avait déclaré le code propre, et le test a trouvé un reliquat réel
  dans `eval/offline.py`. La leçon vaut le test.

**Audit archivé** le 2026-07-29 dans
[`docs/archive/audit-code-2026-07-19.md`](docs/archive/audit-code-2026-07-19.md),
avec son bandeau et le sort des 7 findings (règle de
[`docs/archive/README.md`](docs/archive/README.md)). Premier document archivé pour
**épuisement** plutôt que pour divergence : il n'est pas devenu faux, il est
devenu fini.

---

## 🔒 Externe — le quota LangSmith est épuisé (constaté 2026-07-29)

**Ce n'est pas un bug du projet, et c'est justement pour ça que ça s'inscrit ici :**
sans note, la prochaine session diagnostiquera une panne de configuration.

Chaque exécution qui appelle un vrai LLM (`make score ARGS=--live`, `pytest` avec
les clés, `make eval`) crache en stderr :

```
LangSmithRateLimitError: 429 ... "tenant exceeded usage limits:
Monthly unique traces usage limit exceeded"
```

**Le symptôme est traître** : l'envoi de traces est **en arrière-plan**, donc la
commande se termine **verte** pendant que plus rien n'est enregistré. Un run réussi
et une UI vide, en même temps.

**Conséquence réelle :** `make eval` tourne mais ne produit plus d'expérience
comparable dans LangSmith — or c'est précisément là que vivent les évaluations
non déterministes que la CI refuse de garder (`docs/ci.md` §8). Le suivi de la
qualité *live* est donc suspendu jusqu'à réinitialisation du quota ou changement
de plan. **La porte de livraison n'est pas affectée** : `make score` est hors ligne
et ne dépend pas de LangSmith.

⚠️ Ne pas confondre avec l'autre cause de « traces disparues » déjà documentée :
regarder l'instance **US** alors que le compte est en **EU**. Ici l'endpoint est
bon, c'est le quota qui est atteint.

---

## Chantier 8 — La mémoire notée sur le moteur de PROD, pas seulement SQLite ✅

**Constaté le 2026-07-29, en répondant à « quid de Postgres ? » juste après avoir
livré l'étage MLOps.** C'est un angle mort *créé* par ce chantier-là, pas un vieux
défaut : `eval/offline.py` construit ses stores avec
`persistence_backend="sqlite"`. Donc quand le rapport annonce **mémoire 12/12**,
c'est vrai — **sur SQLite**. La cible de production est un seul Postgres avec
pgvector (`docs/architecture-cible-2026-07-25.md`).

**Pourquoi ce n'est pas cosmétique :** **R3, c'est l'isolation entre clients**, une
propriété de *sécurité*. Elle est prouvée contre `SqliteStore` ; ce qui tiendra les
vraies données clients sera `PostgresStore`. La séparation repose sur
`memories_namespace(user_id)` — notre code, donc partagé entre les deux — mais la
**requête de similarité** qui pourrait ramener la ligne d'un autre client est
propre à chaque store (sqlite-vec vs pgvector). C'est exactement la moitié non
partagée qui n'est vérifiée nulle part automatiquement.

**Ce qui existe déjà, et sa limite :** Postgres a été réellement exercé à l'étape 3
du déploiement — **à la main, une fois**. C'est une vérification, pas une garde de
non-régression. Tout le reste de ce dépôt a converti ses vérifications manuelles en
invariants exécutables ; celle-ci ne l'est pas encore.

**Ce qui a été fait :**

- **Un « moteur » est devenu une notion de première classe** — `MemoryEngine`
  (`eval/offline.py`) : un `PERSISTENCE_BACKEND` plus l'endroit où ses données
  vont, qui distribue un **slot isolé par cas** (deux fichiers SQLite, ou un
  schéma Postgres). `score_memory()` joue le corpus **une fois par moteur**, donc
  la dimension compte `12 × moteurs`.
- **Les mêmes 12 cas dans `tests/test_memory_cases.py`**, paramétrés par moteur
  via une fixture. Postgres se **skippe** sans base (`make test` sur un portable
  n'exige pas de serveur).
- **La quatrième porte** : `make score ARGS=--require-postgres`, que la CI passe.
  Les trois autres portes lisent un **chiffre** — et un chiffre ne peut pas
  s'apercevoir qu'il a été calculé sur la moitié des moteurs. Sans elle, un
  service de base qui ne démarre pas rendrait un **12/12 vert** couvrant deux
  fois moins.
- **Le rapport nomme les moteurs notés**, et **avertit** quand Postgres manque.
  C'est la moitié « honnêteté » : sans base, le trou devient *connu* au lieu
  d'être aveugle. C'est précisément ce qui manquait au 12/12 d'avant.
- **`EVAL_DATABASE_URL`, et surtout PAS `DATABASE_URL`.** L'éval **crée et
  SUPPRIME** des schémas `eval_*` ; on ne fait pas ça dans la base configurée de
  quelqu'un par héritage. Et `offline_settings` existe pour qu'une note ne dépende
  jamais du `.env` local — un champ de `Settings` aurait remis l'entrée du scorer
  sur ce chemin-là.
- **Bonus non prévu : R1 est enfin durable.** Le fil était rejoué sur un
  `InMemorySaver`, y compris dans la passe « sqlite ». Il tourne maintenant sur le
  saver du moteur, et se relit à travers un **second objet saver** sur le même
  stockage — donc `PostgresSaver` fait son aller-retour sous assertion, ce qui
  n'existait nulle part (`test_persistence.py` ne validait que la config).

**Quatre pièges payés en route, à ne pas re-découvrir.** Les deux premiers ont été
trouvés par la **revue de code**, après une première version qui passait au vert —
et c'est l'enseignement : la revue a trouvé ce que 281 tests verts ne montraient
pas, parce qu'aucun test n'assertait le **second** run.

1. 🔴 **Supprimer un schéma sous un pool vivant fait retomber tout le monde dans
   `public`.** `purge_eval_schemas` tournait à **chaque** `score_memory`, or
   `get_postgres_pool` est `lru_cache`d : au 2ᵉ run d'un même process (ce que fait
   `test_mlops.py` avec ses fixtures `scores` et `degraded`), la purge supprimait
   les schémas auxquels les pools encore en cache étaient liés. `configure` ne
   rejoue pas sur un cache hit, donc **rien ne les recréait** — et Postgres
   **ignore silencieusement** une entrée absente du `search_path`. Résultat mesuré :
   les 11 slots écrivaient dans `public`, **et le corpus notait quand même 24/24**,
   parce que `memories_namespace(user_id)` portait l'isolation à lui seul. Un vert
   dont l'isolation par cas avait disparu : exactement le « juste pour la mauvaise
   raison » que ce chantier combat. Correctif : la purge **recrée** immédiatement
   ce qu'elle supprime, les pools sont **fermés** en fin de run, et
   `test_scoring_twice_in_one_process_keeps_every_slot_in_its_own_schema` assure
   l'invariant sur le **stockage**, pas sur la note.
2. 🔴 **`CREATE EXTENSION IF NOT EXISTS` ne déplace pas une extension existante.**
   Il matche par **nom** sur toute la base : si l'application a démarré d'abord,
   pgvector est déjà dans `DATABASE_SCHEMA` (`agent_state`) et l'ordre est un
   no-op. Les schémas d'éval mouraient alors au `setup()` sur
   `type "vector" does not exist` — reproduit. Correctif : détecter le schéma réel
   de l'extension et la **relocaliser** dans `public`, qui est sur le `search_path`
   de tous les backends du dépôt (donc jamais moins disponible pour personne).
3. **`_` est un joker dans `LIKE`**, et les deux préfixes finissent par un `_`.
   `LIKE 'eval_score_%'` matchait donc aussi `eval_scoreboard`, supprimé en
   CASCADE — alors que le commentaire jurait le contraire. Remplacé par
   `left(nspname, n) = prefix`, une vraie comparaison de préfixe.
4. **La baseline comparait des cas ABSOLUS**, or le compte dépend maintenant du
   nombre de moteurs. Une baseline à 24 bloquait un 12/12 parfait sans base
   (« régresse de 12 cas »), et la baseline à 12 face à un run CI à 24 retirait la
   porte en silence. La comparaison est désormais **par moteur** des deux côtés, et
   le nombre de moteurs est enregistré dans `data/eval-baseline.json`.

**Le coût, assumé :** la CI passe de ~15 s à une ou deux minutes, et gagne une
pièce mobile. Le piège du *healthcheck* est couvert par `options: --health-cmd`
sur le service (GitHub attend « accepte les connexions », pas « le conteneur
tourne »).

**Ce que ça ne couvre PAS, et c'est délibéré :** un Postgres **injoignable** fait
**échouer** le run, il ne le dégrade pas en 12 cas ratés. Un problème
d'infrastructure qui se lit « l'agent a perdu la mémoire » est le pire des
diagnostics.

**La baseline reste à 12 cas, et ce n'est plus un arbitrage — juste un fait.**
`data/eval-baseline.json` enregistre 12 cas sur 1 moteur. Depuis le correctif n°4
ci-dessus, la comparaison est **par moteur** : 12/1 contre 24/2, c'est le même
niveau, donc ni blocage abusif en local ni porte retirée en CI. Le fichier gagnera
`"engines": 2` au prochain `--update-baseline` lancé depuis un environnement qui a
les deux moteurs ; d'ici là, `engines` absent est lu comme `1`, ce qui est
exactement ce qu'il valait quand la ligne a été écrite. La **couverture**, elle,
reste gardée par `--require-postgres` : une porte booléenne, insensible au compte.

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

- [x] Indexer [`docs/archive/audit-code-2026-07-19.md`](docs/archive/audit-code-2026-07-19.md)
      dans la section « Where things live » du `CLAUDE.md` racine (fait : `6575c0e`,
      avec `TODO_priorities.md` et `vision.md`).
