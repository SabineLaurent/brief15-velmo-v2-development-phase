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
| 1 | Audit A2 — escalade cassée via la couture | bloque **B1.6** (le cas escalade dans l'UI) | ⬜ |
| 2 | Audit A1 — guard de sortie contourné en streaming | **sécurité** sur le seul chemin client réel | ⬜ |
| 3 | B1.4 — session, `thread_id` & identité | reprise de la roadmap scope B | ⬜ |
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

## Chantier 1 — Audit A2 : escalade cassée via la couture ⬜

Réf. [`docs/audit-code-2026-07-19.md`](docs/audit-code-2026-07-19.md) §A2.
Quand le routeur choisit `escalate`, le graphe se met en pause (`interrupt()`) ;
`stream_reply` ne streame rien et son repli ne trouve pas d'`AIMessage` → **bulle
vide** dans Chainlit, et le payload `__interrupt__` est ignoré. Le CLI gère le cas,
la couture non.

**Pourquoi en premier :** bloque **B1.6**, dont le livrable est précisément « le cas
escalade honnêtement affiché ». Inutile d'avancer sur B tant que ça casse.

---

## Chantier 2 — Audit A1 : guard de sortie contourné en streaming ⬜

Réf. [`docs/audit-code-2026-07-19.md`](docs/audit-code-2026-07-19.md) §A1.
`stream_reply` diffuse les tokens **avant** que `guard_output` ne s'exécute : sur
le seul chemin client réel (Chainlit), la rédaction PII/secrets et la protection
anti-fuite du system prompt sont **cosmétiques**. Le guard ne mord que sur les
chemins non streamés (CLI, éval).

Trois correctifs possibles (bufferisation / guard incrémental dans `_pump` /
a minima corriger la docstring mensongère + documenter la limite). **À trancher
au moment de le traiter** — c'est un vrai arbitrage streaming ⇄ sécurité.

---

## Chantier 3 — B1.4 : session, `thread_id` & identité ⬜

Reprise normale de [`docs/roadmap-frontend.md`](docs/roadmap-frontend.md).
À faire **après** 1 et 2, qui touchent la même couture.

---

## Chantier 4 — Mémoire longue & changement de modèle d'embeddings ⬜

Le store long terme indexe ses souvenirs avec les mêmes embeddings, **persistés en
SQLite** depuis la Phase 10. Changer `EMBEDDINGS_MODEL` rendrait la recherche
mémoire silencieusement incohérente — et contrairement à la FAQ (reconstructible
depuis `data/faq/`), les souvenirs n'existent **que** dans la base : il faudrait
les **ré-embedder** un par un. C'est une **migration**, pas un rebuild.

Ne se déclenche que si on change de modèle d'embeddings — improbable en tuto.
**Action minimale retenue : documenter le piège**, coder la migration seulement
si le cas se présente.

---

## Divers (petit, à caser)

- [x] Indexer [`docs/audit-code-2026-07-19.md`](docs/audit-code-2026-07-19.md)
      dans la section « Where things live » du `CLAUDE.md` racine (fait : `6575c0e`,
      avec `TODO_priorities.md` et `vision.md`).
