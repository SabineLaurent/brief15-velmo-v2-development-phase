# `database/` — état runtime

Contient tout ce que l'agent écrit à l'exécution. **Rien n'est versionné** : le
`.gitignore` écarte les bases (`*.db`) et les fichiers de checkpoint
(`database/*/*.bin`). Seuls ce README et un `.gitkeep` par sous-dossier sont
suivis, pour que l'arborescence existe après un `git clone`.

Les chemins sont relatifs au répertoire de lancement : lancer toujours depuis la
racine du dépôt. Les dossiers parents sont créés à la connexion
(`memory/sqlite_conn.py`).

## Contenu

```
database/
├── working_memory/
│   └── checkpoints.db     ← état du fil en cours       (clé : thread_id)
├── agent_memory/
│   └── memories.db        ← mémoire long terme         (clé : user_id)
├── shop/
│   └── shop.db            ← backend métier de démonstration (`make seed`)
├── eval/
│   └── report.md          ← rapport d'évaluation écrit par `make score`
└── index/                 ← réservé à l'index vectoriel persistant de la FAQ
```

La FAQ est actuellement réindexée en mémoire à chaque démarrage
(`knowledge/ingest.py`) ; `index/` est vide tant qu'un store persistant n'est pas
branché.

### `working_memory/`

Tables `checkpoints` et `writes`, écrites par le checkpointer LangGraph
(`memory/short_term.py`). Contient l'historique des messages du fil ainsi que
l'état d'exécution du graphe : nœud suivant, écritures en attente, `interrupt()`
en cours. Une escalade suspendue est reprise depuis cet état.

### `agent_memory/`

Tables `store` et `store_vectors`, écrites par le store LangGraph
(`memory/long_term.py`), sous le namespace `("memories", user_id)` — un client
ne peut pas lire les souvenirs d'un autre. Chaque souvenir est embeddé et indexé
via `sqlite-vec` dans le même fichier SQLite.

### `shop/`

Backend métier de démonstration (commandes, clients, expéditions, retours,
tickets). Deux adaptateurs existent derrière le port `actions/`, sélectionnés par
`SUPPORT_BACKEND` :

| `SUPPORT_BACKEND` | Adaptateur | Contenu | Persistant |
|---|---|---|---|
| `memory` *(défaut)* | `InMemorySupportBackend` | 3 commandes en RAM | non |
| `sqlite` | `SqlSupportBackend` | 14 commandes / 10 clients / expéditions / retours / remboursements / tickets | oui (`shop/shop.db`) |

```bash
make seed                 # peuple la boutique (idempotent)
make seed ARGS=--reset    # drop + recréation + re-seed
```

Notes :

- En backend `sqlite`, utiliser un `user_id` existant (`C-marc-dubois`,
  `C-sophie-martin`, …) : un identifiant inconnu ne possède aucune commande.
- Le défaut reste `memory` car les deux adaptateurs n'ont pas la même convention
  d'identifiants (`CMD-1001` vs `O-2024-0103`) et `eval/dataset.py` épingle celle
  de `memory`.
- Pas de migrations Alembic : `create_all()` crée les tables manquantes, et
  `--reset` reconstruit le jeu de données, qui est déterministe.
- Cette base représente un système tiers. Aucune jointure ne doit être faite entre
  elle et les mémoires de l'agent : en production, ce backend est remplacé par des
  appels au SI du marchand (`get_order_status`, `create_ticket`).

## Configuration

| Variable (`.env`) | Défaut / valeurs |
|---|---|
| `PERSISTENCE_BACKEND` | `memory` (RAM) · `sqlite` · `postgres` |
| `WORKING_MEMORY_DB_PATH` | `./database/working_memory/checkpoints.db` (backend `sqlite`) |
| `AGENT_MEMORY_DB_PATH` | `./database/agent_memory/memories.db` (backend `sqlite`) |
| `DATABASE_URL` | *(vide)* — requis si backend `postgres` |
| `DATABASE_SCHEMA` | `agent_state` |
| `MEMORY_TTL_DAYS` | `365` (vide = conservation infinie) |
| `SUPPORT_BACKEND` | `memory` · `sqlite` |
| `SHOP_DB_PATH` | `./database/shop/shop.db` (backend `sqlite`) |

`PERSISTENCE_BACKEND` gouverne la mémoire de l'agent ; `SUPPORT_BACKEND` gouverne
le backend métier. Les deux sont indépendants — `SUPPORT_BACKEND=postgres`
n'existe pas et lève une erreur.

## Backend `postgres`

`PostgresSaver` + `PostgresStore` dans une base unique, les deux horizons de
mémoire séparés par schéma (`DATABASE_SCHEMA`), recherche sémantique en pgvector
dans cette même base.

- Pool de connexions partagé (`memory/postgres_conn.py`) : le serveur HTTP répond
  depuis un pool de threads et le sweeper TTL tourne sur le sien.
- `MEMORY_TTL_DAYS` arme un balayage de fond qui supprime les souvenirs périmés.
  Le compteur repart au **dernier accès**, pas à la création. Le réglage est
  ignoré en backend `sqlite` et `memory`.
- L'image Docker doit être `pgvector/pgvector`, pas `postgres` : sans l'extension,
  le store long terme échoue à son `setup()`.
