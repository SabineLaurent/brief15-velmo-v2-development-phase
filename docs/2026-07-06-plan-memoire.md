# Plan d'implémentation — Chantier Mémoire

_Note de cadrage, 2026-07-06. Ligne directrice : `docs/reco_expert.md` (exigence n°1)._

## Pourquoi la mémoire en premier

Ordre retenu pour les trois chantiers : **Mémoire → Garde-fous → MLOps**, exactement
l'ordre des exigences de l'expert. Raisons :

1. C'est l'exigence n°1 et la plus structurante (le pipeline `agent.respond` lit/écrit
   la mémoire *autour* de tout le reste).
2. Le MLOps *mesure* la mémoire et les garde-fous → il vient forcément en dernier.
3. Mémoire et garde-fous sont indépendants ; la mémoire est le morceau le plus lourd
   (deux étages Postgres + Chroma) → à traiter tant qu'on est frais.

## Le modèle mental → où vit quoi

Trois étages (cf. `docs/architecture-agent-support.md`), mappés sur du concret :

| Étage | Contenu | Backend dev/feature (hors-ligne) | Backend pré-prod |
|-------|---------|----------------------------------|------------------|
| Court terme (`history`) | N derniers tours | table SQLite (fichier) | Postgres |
| Long terme structuré (`facts`) | faits clé→valeur par user | table SQLite (fichier) | Postgres |
| Long terme épisodique (`episodic`) | tours passés, recherchés par pertinence | recherche mots-clés sur la table SQL | Chroma (collection par user) |

**Constat clé** (lecture de `eval/memory_cases.jsonl`) : le *recall* ET la *persistance*
passent par l'étage épisodique, pas par une extraction de faits en NLP. Ex. `R2-pointure`
stocke le tour « je porte la taille L » ; la question « quelle taille ? » le fait remonter
par recouvrement de mots-clés (`taille`). Donc **pas d'extracteur de faits magique** :
`remember_fact` reste la voie *explicite* et structurée, l'épisodique couvre le reste.

## Schéma — deux tables, isolées par `user_id`

Réutilise les patterns de `db.py` (SQLAlchemy 2), sur un **`Base` dédié à la mémoire**
(découplé du schéma métier et d'Alembic ; `create_all` idempotent) :

- `memory_facts(user_id, key, value, updated_at)` — PK `(user_id, key)`. `remember_fact` = upsert.
- `memory_turns(id, user_id, role, content, ts)` — un tour = une ligne. `write` ajoute
  les deux tours (user + assistant).

Chaque requête porte `WHERE user_id = ?` → l'isolation (R3) est **structurelle**, pas un
contrôle ajouté après coup.

## Comportement des 5 méthodes

- **`write(user, u_msg, a_msg)`** → insère 2 lignes dans `memory_turns`.
- **`read(user, message)`** → construit `MemoryContext` :
  - `history` = N derniers tours du user (court terme) ;
  - `facts` = lignes `memory_facts` du user ;
  - `episodic` = top-k tours du user classés par recouvrement de mots-clés avec `message`
    (le tour 0 remonte même après 30 tours → R1) ;
  - puis **troncature au `token_budget`** (≈ chars/4), faits + épisodique pertinents d'abord.
- **`remember_fact(user, key, value)`** → upsert dans `memory_facts`.
- **`forget(user, target)`** → `DELETE` des tours **et** faits du user dont le
  contenu/clé/valeur contient `target` (insensible à la casse). Renvoie le nombre
  supprimé (R5 exige `>= 1`).
- **`inspect(user)`** → `{"facts": {...}, "episodic": [...]}` → traçabilité (R6).

## Décisions arrêtées

### A. Backend — les deux, sélectionnés par variable d'environnement

Ce n'est pas un compromis : c'est le pattern déjà en place (`db.py:session_factory`,
`kb_store.get_kb`). Un seul chemin de code, seule l'URL change.

| Étage | Dev / feature (hors-ligne) | Pré-prod |
|-------|----------------------------|----------|
| faits + tours | SQLite **fichier** (tmp OS, partagé entre instances) | Postgres si `DB_URL` / `MEMORY_DB_URL` |
| épisodique | recherche mots-clés sur la table SQL | **Chroma** si `CHROMA_URL` |

Le fichier SQLite (vs RAM d'instance) est requis par `test_cross_session_persistence`,
qui crée **deux `MemoryManager()` distincts** et attend que le second retrouve les faits
du premier → honore littéralement la persistance multi-session (R2).

### B. Câblage dans l'agent — différé, en étape 2 distincte

Aujourd'hui `agent.respond` appelle `self.memory.read(...)` mais **jette le résultat**
(`agent.py:77`). Décision : on **diffère** le branchement de `ctx.render()` dans le prompt
LLM. Raison : construire `MemoryManager` est un préalable dans tous les cas, auto-testable
en isolation ; le câblage touche la fabrication du prompt et pourrait perturber les 7 tests
métier au vert. On sépare en deux changements traçables :
1. `MemoryManager` vert sur ses tests (aucune modification de `agent.py`) ;
2. câblage dans l'agent, changement chirurgical distinct, validé sur diff à froid.

## Comment on sait que c'est fini

- **Étape 1** : `pytest tests/acceptance/test_memory.py` (4 tests) au vert ;
  les 12 cas de `eval/memory_cases.jsonl` rejouables ; **aucune régression** sur
  `test_business` (7/7).
- **Étape 2** (câblage agent) : la mémoire est effectivement injectée dans les réponses,
  sans casser `test_business`.

## Reste à décider plus tard (hors scope étape 1)

- Migration Alembic pour les tables mémoire en Postgres (offline : `create_all` suffit).
- Backend Chroma épisodique réel (interface prête, implémentation en ligne).
