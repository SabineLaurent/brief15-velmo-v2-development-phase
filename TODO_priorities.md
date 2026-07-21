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

- [ ] **1.1 — Dépendance.** `uv add --package support-agent langchain-chroma`
      (résolution vérifiée : `langchain-chroma 1.1.0` + `chromadb 1.5.9` OK avec
      `langchain-core 1.4.9`). Coût mesuré : **26 paquets, ~160 Mo**.
- [ ] **1.2 — Config** (`config.py`) : `knowledge_index_dir` (défaut
      `./TEMP/database/chroma`) et `knowledge_collection_name` (défaut `faq`).
      Sortir `chunk_size=800` / `chunk_overlap=120` du corps de fonction vers des
      réglages — ils doivent entrer dans l'empreinte (cf. 1.3).
- [ ] **1.3 — Module d'empreinte** (`knowledge/fingerprint.py`, neuf).
      Un hash SHA-256 sur : nom + contenu de chaque `*.md` (triés) **+** provider
      et modèle d'embeddings **+** `chunk_size` / `chunk_overlap`. Écrit dans
      `TEMP/database/faq_index.meta.json` (à côté du dossier Chroma, pas dedans —
      pour qu'un `rm -rf chroma/` ne laisse pas d'empreinte orpheline).
      → **Fonction pure, testable sans réseau ni Chroma.**
- [ ] **1.4 — Réécriture de `build_vector_store`** (`ingest.py`) :
      - empreinte identique **et** dossier d'index présent → ouvrir Chroma et
        rendre la main (**aucun** `add_documents`, **aucun** appel d'embedding) ;
      - sinon → purger la collection, re-découper, `add_documents(ids=...)` avec
        des **IDs stables** (hash de `source + n° de chunk + contenu`), puis écrire
        l'empreinte.
      - **Log explicite** dans les deux cas (« FAQ index loaded from cache » /
        « rebuilding FAQ index: <raison> ») — sans ça, le comportement est invisible.
- [ ] **1.5 — `make reindex`** : supprime dossier d'index + empreinte. La porte de
      sortie quand on veut forcer, sans avoir à retenir des chemins.
- [ ] **1.6 — Tests hors-ligne** : l'empreinte change bien si (a) un `.md` est
      modifié, (b) le modèle d'embeddings change, (c) `chunk_size` change ; et
      elle **ne** change **pas** sur deux appels identiques. Aller-retour Chroma
      complet avec un embedding factice déterministe (donc **sans réseau**).
- [ ] **1.7 — Vérif live** : deux démarrages consécutifs — le 2ᵉ doit logger le
      cache hit ; puis modifier un `.md` et vérifier le rebuild automatique.
- [ ] **1.8 — Docs.** ⚠️ `packages/support-agent/CLAUDE.md` affirme « FAQ =
      `InMemoryVectorStore` reconstruit à CHAQUE démarrage » → **devient faux**.
      Mettre à jour aussi : docstring d'`ingest.py`, `docs/architecture.md`,
      `.env.example`, et noter que **prod = Chroma serveur**.

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
