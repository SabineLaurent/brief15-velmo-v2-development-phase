# 2026-07-11 — Étape 1c : mémoire long terme factuelle

_Note de cours, niveau débutant Python / agents IA._

## Le problème que l'épisodique laissait ouvert

L'étape 1b a donné à `MemoryManager` un étage épisodique : un souvenir qui
sort du tampon court terme reste retrouvable par similarité. Mais ce
souvenir vit en RAM, à l'échelle du **process** (`LocalEpisodicStore`). Si le
process redémarre — ou, plus simplement, si on instancie un **second**
`MemoryManager()` pour une nouvelle session, comme le fait vraiment un
utilisateur qui revient le lendemain — tout est perdu. C'est exactement ce
que vérifiait `test_cross_session_persistence`, resté rouge jusqu'ici :

```python
session1 = MemoryManager()
session1.remember_fact("acc-marc", "pointure", "L")

session2 = MemoryManager()  # nouvelle session, même client
rendered = session2.read("acc-marc", "Tu te souviens de moi ?").render()
assert "L" in rendered
```

C'est le trou que `docs/architecture-agent-support.md` assigne à l'étage
**long terme structuré** : « quelle taille prend ce client ? » — une
réponse exacte, pas une similarité floue, portée par une **base
relationnelle**. C'est aussi ce que demande `reco_expert.md` : Postgres
comme « source de vérité des faits durables par utilisateur ».

## Toujours le même patron : backend réel + repli hors-ligne

`facts.py` applique le même patron que `kb_store.py`/`episodic.py`, avec une
différence assumée. Dans les deux étapes précédentes, le repli hors-ligne
est un magasin **neuf à chaque instanciation** de `MemoryManager`
(`LocalKB` relit les fichiers `kb/docs` à chaque fois ; `LocalEpisodicStore`
part vide). Ici, ça casserait justement la persistance multi-session qu'on
cherche à obtenir. Le repli local est donc un **engine SQLite mis en cache
au niveau du module** — créé une seule fois par process, puis réutilisé :

```python
_local_session_factory: sessionmaker[Session] | None = None

def _get_local_session_factory() -> sessionmaker[Session]:
    global _local_session_factory
    if _local_session_factory is None:
        engine = create_engine("sqlite://", future=True)
        Base.metadata.create_all(engine)
        _local_session_factory = sessionmaker(bind=engine, expire_on_commit=False, future=True)
    return _local_session_factory
```

Deux `MemoryManager()` successifs appellent chacun `get_facts_store()`, mais
tous deux reçoivent une fabrique de sessions pointant vers **le même
engine** en mémoire. C'est ce détail — pas un `remember_fact` plus malin —
qui fait passer `test_cross_session_persistence` au vert.

```python
def get_facts_store() -> FactsStore:
    if not os.getenv("DB_URL"):
        return SqlFactsStore(_get_local_session_factory())
    from ..db import session_factory
    return SqlFactsStore(session_factory())
```

Avec `DB_URL` configuré (Postgres réel, en prod), pas besoin de cache
module : chaque appel ouvre une session vers la même base externe, qui
persiste par construction, y compris entre deux process.

## Une seule classe des deux côtés : `SqlFactsStore`

Contrairement à l'épisodique (une classe `Local...` et une `Chroma...`
distinctes), Postgres et SQLite parlent tous deux SQLAlchemy — pas besoin de
dupliquer la logique. `SqlFactsStore` prend juste une `session_factory` en
paramètre (Postgres ou SQLite hors-ligne) et fait le même travail dans les
deux cas : lire/écrire une table `memory_facts` (`user_id`, `key`, `value`,
clé primaire composite), ajoutée à `db.py` sur le modèle des tables métier
existantes, sans clé étrangère vers `customers` — le `user_id` de la mémoire
est un identifiant applicatif générique, pas garanti d'exister comme client
en base (les tests utilisent des ids comme `acc-marc`, absents du jeu de
données).

```python
def set(self, user_id: str, key: str, value: str) -> None:
    with self._session_factory() as session:
        existing = session.get(MemoryFact, (user_id, key))
        if existing is not None:
            existing.value = value
        else:
            session.add(MemoryFact(user_id=user_id, key=key, value=value))
        session.commit()
```

`session.get(MemoryFact, (user_id, key))` : avec une clé primaire composite,
`.get()` prend un tuple dans l'ordre de déclaration des colonnes. C'est un
**upsert** manuel : si le fait existe déjà (ex. le client change de
pointure), on écrase la valeur au lieu d'empiler des doublons.

## Câblage dans `MemoryManager`

`remember_fact` et `inspect` cessent d'être no-op ; `read` fusionne
désormais les trois étages dans le `MemoryContext` :

```python
def read(self, user_id: str, message: str) -> MemoryContext:
    history = list(self._history.get(user_id, []))
    already_present = {f"{role}: {content}" for role, content in history}
    hits = self._episodic.search(user_id, message, k=_EPISODIC_K)
    episodic = [hit for hit in hits if hit not in already_present]
    facts = self._facts.all_for(user_id)
    return MemoryContext(history=history, episodic=episodic, facts=facts)
```

## Le droit à l'oubli, maintenant réel — et sur les trois étages

`docs/architecture-agent-support.md` est explicite : « le droit à l'oubli =
suppression ciblée dans les deux stores [relationnel, vectoriel] ». En
pratique, il faut aussi le tampon court terme, sans quoi « oublie mon
adresse » laisserait la donnée réapparaître au tour suivant via `.history`.
`forget` purge donc les trois :

```python
def forget(self, user_id: str, target: str) -> int:
    target_l = target.lower()
    turns = self._history.get(user_id, [])
    kept = [turn for turn in turns if target_l not in turn[1].lower()]
    removed = len(turns) - len(kept)
    self._history[user_id] = kept
    removed += self._episodic.forget(user_id, target)
    removed += self._facts.forget(user_id, target)
    return removed
```

Ça a demandé d'ajouter `forget(user_id, target) -> int` à `EpisodicStore`
(le `Protocol`, pas seulement `MemoryManager`) et à ses deux implémentations
: recherche par sous-chaîne insensible à la casse pour `LocalEpisodicStore`
(même esprit que `_tokens()` en 1b, mais volontairement plus simple — pas
besoin de score de pertinence pour une suppression, juste d'un critère de
correspondance), et un `collection.delete(where=..., where_document=
{"$contains": target})` pour `ChromaEpisodicStore` (non testé hors-ligne,
comme le reste du backend Chroma).

## Une simplification assumée : correspondance par sous-chaîne, pas par intention

`forget(user_id, "adresse")` supprime tout fait ou souvenir dont la **clé**
ou le **contenu** contient littéralement `"adresse"` (insensible à la
casse). Pas de compréhension du langage naturel (« oublie où j'habite » ne
matcherait pas). C'est délibérément grossier, dans la continuité de la
recherche par recouvrement de tokens de l'épisodique (1b) : suffisant pour
satisfaire R5 tel qu'exigé par les tests et les cas d'éval
(`eval/memory_cases.jsonl`, tag `R5`, qui utilisent tous un mot-clé explicite
comme `target`), sans construire un classifieur d'intention pour un besoin
qui n'en demande pas.

## Effet de bord sur la suite de tests

`tests/test_memory_short_term.py::test_remember_fact_and_forget_remain_noop_for_now`
vérifiait que `remember_fact`/`forget` ne faisaient rien — c'est
maintenant faux par construction, donc supprimé (même logique qu'en 1b avec
le test de troncature court terme). Les nouveaux tests dédiés vivent dans
`tests/test_memory_facts.py` : persistance entre deux `MemoryManager()`,
écrasement d'un fait existant, isolation par utilisateur, oubli ciblé
(un seul fait supprimé parmi plusieurs), et oubli combiné sur les trois
étages en une seule requête.

## Ce qui reste hors périmètre

- **Traçabilité fine (R6)** : `inspect()` expose l'état courant (faits,
  historique, épisodique), ce qui satisfait l'exigence telle que formulée
  dans `reco_expert.md` (« pouvoir inspecter ce qui a été retenu »). Un
  **journal d'écritures** horodaté (qui a écrit quoi, quand) irait plus
  loin mais n'est pas demandé par les tests actuels — à revisiter si le
  besoin se précise, notamment au moment des garde-fous (`events`) qui,
  eux, exigent explicitement un journal.
- **Fusion budgétée** : les faits ne sont pas comptés dans `token_budget`
  (comme l'épisodique, cf. note 1b) — un magasin de faits reste
  intrinsèquement petit (quelques clés par client), donc pas de risque de
  faire exploser le contexte en pratique.
- **Migration Alembic** : `alembic/versions/0002_memory_facts.py` ajoute la
  table `memory_facts` pour les bases déjà migrées par `0001_initial`
  (`Base.metadata.create_all` est idempotent : il ne recrée que ce qui
  manque). Non exécutée dans cette étape (pas de Postgres dans cet
  environnement), donc non testée end-to-end — cohérent avec le reste du
  backend Postgres du projet.

## État de la suite de tests

`tests/acceptance/test_memory.py` : **4/4 verts**. Les 6 exigences R1–R6 du
chantier mémoire sont maintenant couvertes par construction : R1 (rappel
longue conversation, via épisodique), R2 (persistance multi-session, via
faits), R3 (isolation, filtrée par `user_id` à chaque étage), R4 (fenêtre de
contexte tenue, `token_budget`), R5 (droit à l'oubli, sur les trois étages),
R6 (traçabilité via `inspect()`). Le chantier **mémoire** du brief est
terminé ; restent les garde-fous et le MLOps.
