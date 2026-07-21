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
| 1 | Index FAQ persistant (Chroma) | démarrage instantané ; Chroma = **cible prod** assumée | ⬜ |
| 2 | Cache de la dimension d'embeddings | supprime le **dernier** appel réseau au démarrage | ⬜ |
| 3 | Audit A2 — escalade cassée via la couture | bloque **B1.6** (le cas escalade dans l'UI) | ⬜ |
| 4 | Audit A1 — guard de sortie contourné en streaming | **sécurité** sur le seul chemin client réel | ⬜ |
| 5 | B1.4 — session, `thread_id` & identité | reprise de la roadmap scope B | ⬜ |
| 6 | Mémoire longue & changement de modèle d'embeddings | piège documenté, pas urgent | ⬜ |

> Les chantiers **1 et 2 se font ensemble** : ils partagent le mécanisme
> d'empreinte (*fingerprint*) et visent le même but — un démarrage sans appel réseau.

---

## Chantier 1 — Index FAQ persistant (Chroma) ⬜

**Le problème.** `knowledge/ingest.py` reconstruit l'index à **chaque démarrage** :
4 fichiers → 8 chunks → 1 appel d'embedding. Avec Chainlit `-w` (rechargement à
chaque sauvegarde), c'est rejoué en boucle. Et le jour où la FAQ fait 500
documents, ce n'est plus tenable.

**La décision.** Chroma **embarqué** (dossier local) en dev, dans
`TEMP/database/chroma/`. Chroma est la **cible prod** — en prod il tournera en
**serveur** (`HttpClient`), et seul le mode de connexion changera dans `ingest.py`.

**Le piège central : l'invalidation.** Un index persistant est une *projection* de
`data/faq/`. Si la source bouge et pas la projection, l'agent répond sur une FAQ
périmée **sans rien signaler**. D'où une **empreinte** vérifiée au démarrage.

### Étapes

- [x] **1.1 — Dépendance.** `uv add --package support-agent langchain-chroma`
      (`langchain-chroma 1.1.0` + `chromadb 1.5.9`, OK avec `langchain-core 1.4.9`).
      Coût mesuré : **26 paquets, ~160 Mo**.
- [x] **1.2 — Config** (`config.py`) : **un seul** réglage, `knowledge_index_dir`.
      Le plan en prévoyait 5 : `chunk_size` / `chunk_overlap` restent des
      **constantes** dans `ingest.py` (personne ne règle un overlap depuis un
      `.env`) — elles entrent dans l'empreinte sans être des réglages ; le nom de
      collection est une constante ; le chemin d'empreinte est **dérivé**.
- [x] **1.3 — Module d'empreinte** (`knowledge/fingerprint.py`).
      SHA-256 sur : nom + contenu de chaque `*.md` (triés) **+** provider et
      modèle d'embeddings **+** `chunk_size` / `chunk_overlap`, avec un
      **séparateur `\x00`** entre les parties (sans lui, `("ab","c")` et
      `("a","bc")` collisionnent → index périmé accepté).
      ⚠️ **Correction du plan initial :** l'empreinte va **DANS** le dossier
      d'index, pas à côté. Le raisonnement écrit ici était inversé — à côté, elle
      **survit** à un `rm -rf chroma/` et certifie un index vide : l'agent perd sa
      FAQ en silence. Dedans, supprimer le dossier emporte les deux.
      `read_fingerprint` ne lève **jamais** : toute anomalie ⇒ `None` ⇒ rebuild.
- [x] **1.4 — Réécriture de `build_vector_store`** (`ingest.py`) : empreinte
      identique → ouvrir et rendre la main (0 appel d'embedding) ; sinon
      `reset_collection()` (méthode confirmée via Context7 — la purge manuelle
      prévue est inutile), `add_documents(ids=...)` avec des **IDs stables**
      (`source:n°`), puis empreinte écrite **en dernier** (un plantage en cours ⇒
      pas d'empreinte ⇒ rebuild au lieu d'un index partiel). Logs explicites.
- [x] **1.5 — `make reindex`** : supprime le dossier d'index (l'empreinte étant
      dedans, elle part avec). ⚠️ **App arrêtée** : Chroma cache un client par
      dossier **et par process**, supprimer sous un client vivant donne
      `readonly database`.
- [x] **1.6 — Tests hors-ligne** (+15, suite à 48/48) : `test_fingerprint.py` (9,
      purs) couvre les **deux moitiés** du contrat — l'empreinte change quand il
      le faut (contenu, ajout/retrait, **renommage**, modèle, chunking) **et**
      reste stable sinon (sans quoi un hash aléatoire passerait). `test_ingest.py`
      (6) fait l'aller-retour Chroma avec un embedding factice **qui compte ses
      appels** : démarrage à chaud = **0 embedding**, pas de duplication.
- [x] **1.7 — Vérif live** (4 process séparés) : froid → rebuild (2 appels
      embeddings HTTP, 1.63 s) ; chaud → **réutilisé, 0 appel** (0.68 s) ;
      `livraison.md` modifié → **rebuild automatique**, nouvelle info retrouvée ;
      fichier restauré → rebuild à nouveau. Empreinte écrite avec son contexte
      lisible (modèle, découpage, 4 documents → 8 chunks).
- [x] **1.8 — Docs** : `packages/support-agent/CLAUDE.md` (affirmait « reconstruit
      à CHAQUE démarrage » — **était devenu faux**), `docs/architecture.md` §4,
      `.env.example`, docstrings d'`ingest.py` / `fingerprint.py`.

### Pièges identifiés

- **Duplication** : `add_documents` sur une collection persistante **empile** les
  chunks à chaque démarrage. Neutralisé par les IDs stables + la purge explicite.
- **Changement de modèle d'embeddings** : dimension de vecteurs différente →
  la collection existante devient inutilisable. Couvert par l'empreinte (rebuild).
- `TEMP/` est déjà gitignoré → **rien** à ajouter au `.gitignore`.

---

## Chantier 2 — Cache de la dimension d'embeddings ⬜

**Le problème.** `memory/long_term.py:60` :

```python
dims = len(embeddings.embed_query("probe"))
```

Un **appel réseau à chaque démarrage** pour découvrir un seul nombre (1024 pour
`mistral-embed`) — qui ne change jamais tant que le modèle ne change pas. La
sonde est astucieuse (aucune dimension codée en dur, agnosticisme préservé), mais
sans cache elle annule une partie du gain du chantier 1.

⚠️ **Ce n'est pas un contrôle** — c'est de la mémoïsation : « ai-je déjà posé
cette question pour ce modèle ? ». Le nom du modèle est une **clé de rangement**,
pas un critère de validation. (Le vrai contrôle, c'est l'empreinte du chantier 1.)

### Étapes

- [ ] **2.1** — `get_embedding_dims(settings)` dans `llm/embeddings.py` : lit
      `TEMP/database/embeddings_dims.json` (clé `"<provider>:<model>"`) ; absent →
      sonde, puis écrit. Échec d'écriture (disque en lecture seule) → on continue
      quand même, ce n'est qu'un cache.
- [ ] **2.2** — `long_term.py` appelle cette fonction au lieu de sonder en direct.
- [ ] **2.3** — Test hors-ligne : 2ᵉ appel = **0 sonde** (embeddings factice
      comptant ses invocations) ; changement de modèle = nouvelle sonde.

**Critère de succès des chantiers 1+2 :** au 2ᵉ démarrage consécutif, **zéro**
appel d'embedding avant la première question du client.

---

## Chantier 3 — Audit A2 : escalade cassée via la couture ⬜

Réf. [`docs/audit-code-2026-07-19.md`](docs/audit-code-2026-07-19.md) §A2.
Quand le routeur choisit `escalate`, le graphe se met en pause (`interrupt()`) ;
`stream_reply` ne streame rien et son repli ne trouve pas d'`AIMessage` → **bulle
vide** dans Chainlit, et le payload `__interrupt__` est ignoré. Le CLI gère le cas,
la couture non.

**Pourquoi en 3ᵉ :** bloque **B1.6**, dont le livrable est précisément « le cas
escalade honnêtement affiché ». Inutile d'avancer sur B tant que ça casse.

---

## Chantier 4 — Audit A1 : guard de sortie contourné en streaming ⬜

Réf. [`docs/audit-code-2026-07-19.md`](docs/audit-code-2026-07-19.md) §A1.
`stream_reply` diffuse les tokens **avant** que `guard_output` ne s'exécute : sur
le seul chemin client réel (Chainlit), la rédaction PII/secrets et la protection
anti-fuite du system prompt sont **cosmétiques**. Le guard ne mord que sur les
chemins non streamés (CLI, éval).

Trois correctifs possibles (bufferisation / guard incrémental dans `_pump` /
a minima corriger la docstring mensongère + documenter la limite). **À trancher
au moment de le traiter** — c'est un vrai arbitrage streaming ⇄ sécurité.

---

## Chantier 5 — B1.4 : session, `thread_id` & identité ⬜

Reprise normale de [`docs/roadmap-frontend.md`](docs/roadmap-frontend.md).
À faire **après** 3 et 4, qui touchent la même couture.

---

## Chantier 6 — Mémoire longue & changement de modèle d'embeddings ⬜

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

- [ ] Indexer [`docs/audit-code-2026-07-19.md`](docs/audit-code-2026-07-19.md)
      dans la section « Where things live » du `CLAUDE.md` racine — sinon il est
      invisible pour une session future (commité mais introuvable après un `/clear`).
