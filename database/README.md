# `database/` — l'état runtime, en développement

> Tout ce que l'**agent** écrit pendant qu'il tourne. **Aucune donnée** n'est
> versionnée ici : le `.gitignore` écarte les bases (`*.db`) et les fichiers de
> checkpoint (`database/*/*.bin`). Seuls sont suivis ce README et un `.gitkeep`
> par sous-dossier — pour que l'**arborescence** existe après un `git clone`,
> sans que son **contenu** ne parte dans l'historique.
>
> ⚠️ Ce dossier ne **simule** pas la production. Il la **cartographie**. La
> différence est expliquée plus bas, et elle compte.

## La règle de rangement, en une question

> Est-ce que ça a été écrit par **l'agent**, et est-ce que **personne d'autre**
> n'en a une copie ?

| Réponse | Où ça va | Pourquoi |
|---|---|---|
| Oui aux deux | **`database/working_memory` · `agent_memory`** | perte sèche : ça n'existe nulle part ailleurs |
| Écrit par un **humain** | `data/` | c'est de la **source**, versionnée dans git |
| Reconstructible | `database/index/` *(à venir)* | une **projection** : sa perte coûte du CPU |
| Appartient à un **système tiers** | `database/business/` *(à venir)*, **en dev seulement** | une doublure : en prod, on appelle son API |

## La distinction qui compte : ce qu'on POSSÈDE vs ce qu'on DOUBLE

Tout ce qui est ici n'a pas le même destin en production. Deux catégories :

| | En dev | En prod | Propriétaire |
|---|---|---|---|
| `working_memory/` · `agent_memory/` | SQLite | **Postgres, à nous** | **nous**, des deux côtés |
| `index/` *(à venir)* | fichier local | Chroma serveur / base managée | nous (mais reconstructible) |
| `business/` *(à venir)* | SQLite | ❌ **disparaît** | **le marchand** |

Les mémoires sont à nous **des deux côtés**. La base métier — commandes, clients,
tickets : le standard d'un commerce — est une **doublure**, qui n'existe que parce
qu'on n'a pas le vrai système sous la main. Aujourd'hui elle est même seulement en
RAM (`actions/backend.py`, `_seed_orders()` / `_seed_tickets()` rejoués à chaque
démarrage) ; lui donner un fichier ne changera rien à son statut.

En production, **on ne la migre pas : on la débranche**, et `get_order_status` /
`create_ticket` appellent Zendesk, Salesforce ou le SI du marchand. Le backend
métier est un **port** que l'agent consomme — il ne le possède pas.

⚠️ **Le piège à éviter** : traiter cette base comme les nôtres, et finir par faire
des **jointures** entre nos souvenirs et les commandes du client. Ça marcherait en
dev (même fichier, même moteur) et deviendrait impossible en prod, où les deux
vivent dans des systèmes distincts, souvent chez des fournisseurs distincts. La
frontière doit rester un **appel**, jamais un `JOIN`.

📖 `docs/memoire.md` formule bien le partage des rôles : *le backend dit **que**
c'est arrivé (la vérité) ; le sémantique **personnalise***.

## Ce qu'il y a dedans

```
database/
├── working_memory/
│   └── checkpoints.db     ← le fil en cours         (clé : thread_id)
├── agent_memory/
│   └── memories.db        ← ce qu'on sait du client (clé : user_id)
├── index/                 ← (à venir, Phase 13) l'index vectoriel de la FAQ
└── business/              ← (à venir) la doublure du SI marchand
```

### `working_memory/` — un CHECKPOINT, pas un souvenir

Tables `checkpoints` + `writes`. Écrit par le **checkpointer** LangGraph
(`memory/short_term.py`). Contient l'historique des messages du fil, mais **pas
seulement** — aussi l'**état d'exécution du graphe** : le nœud suivant à jouer,
les écritures en attente, et les **`interrupt()` en cours**.

C'est cette dernière ligne qui justifie le mot *checkpoint*, au sens du jeu vidéo :
quand le routeur choisit `escalate`, le graphe se met en **pause**, et la pause vit
ici. Reprendre une escalade, ce n'est pas *se souvenir* — c'est **restaurer une
exécution suspendue**. On le `resume`, on ne le `recall` pas.

### `agent_memory/` — la vraie mémoire longue

Tables `store` + `store_vectors`. Écrit par le **store** LangGraph
(`memory/long_term.py`), namespace `("memories", user_id)` : un client ne peut
jamais lire les souvenirs d'un autre.

⚠️ Ce fichier est **aussi vectoriel** : chaque souvenir est embeddé (mêmes
embeddings agnostiques que la FAQ) et indexé via `sqlite-vec`, dans le même
fichier SQLite. « Relationnel » et « vectoriel » ne sont donc pas deux endroits
distincts — c'est la même base.

📖 La taxonomie complète (sémantique / épisodique / procédural) est dans
[`docs/memoire.md`](../docs/memoire.md). Aujourd'hui seul le **sémantique** est
construit ; les deux autres sont des extensions naturelles.

### `index/` — réservé, volontairement absent

L'index vectoriel de la FAQ y vivra **en Phase 13**, pas avant. Il a été codé
(Chroma), mesuré, puis **annulé** le 2026-07-21 — voir
[`TODO_priorities.md`](../TODO_priorities.md). Aujourd'hui la FAQ est réindexée en
RAM à chaque démarrage (~1,6 s, zéro dépendance).

Il aura son propre dossier plutôt que d'être mêlé aux deux mémoires, précisément
parce qu'il est **reconstructible** : on doit pouvoir l'effacer sans hésiter, et
hésiter avant d'effacer les deux autres.

### `business/` — réservé, la doublure du SI marchand

Commandes, clients, tickets — le standard d'un commerce. Aujourd'hui **en RAM**
(`actions/backend.py`), re-seedé à chaque démarrage : un `InMemoryBackend` qui sert
à démontrer `get_order_status`, `create_ticket` et la détection de récurrence.

Le jour où on veut des commandes qui **survivent au redémarrage** (utile dès qu'on
teste un parcours sur plusieurs sessions), ce dossier accueille son SQLite. Voir
la mise en garde plus haut : c'est une **doublure**, pas une base à nous.

## Pourquoi DEUX fichiers ?

Un seul suffirait techniquement — c'était le cas jusqu'au 2026-07-21
(`TEMP/database/agent_state.db`). La séparation est un **confort de développement** :
chaque horizon de mémoire s'inspecte, se vide et se raisonne isolément.

```bash
# vider les conversations sans perdre ce qu'on sait des clients
rm database/working_memory/checkpoints.db
```

Ce n'est **pas** une frontière d'architecture : en production, les deux retournent
dans **une seule base Postgres**. Ne construis donc rien qui dépende du fait qu'ils
soient deux fichiers. Ce n'est plus une intention : en backend `postgres`, les huit
tables (`checkpoints*`, `store*`, …) cohabitent bien dans le schéma `agent_state`
d'une base unique.

## Ce qui change en production

Le vrai saut n'est pas le format du fichier — c'est que la base devienne un
**service réseau partagé** entre N instances derrière un load-balancer. Un fichier
local ne peut pas enseigner ça.

```
DEV (ici)                          PROD (cible)
─────────                          ────────────
PERSISTENCE_BACKEND=sqlite         PERSISTENCE_BACKEND=postgres
  2 fichiers, 1 process              1 base, N process concurrents
l'app INDEXE la FAQ au démarrage   job d'ingestion découplé (CI / cron)
                                     └─ l'app n'indexe JAMAIS
```

Côté agent, le basculement est **une variable d'environnement** — le code métier
ne change pas (c'est le même esprit que l'agnosticisme LLM). Ce qui change vraiment,
c'est l'**ingestion**, qui cesse d'être faite par l'application. Détail dans
[`TODO_priorities.md`](../TODO_priorities.md) § Phase 13.

## Réglages

| Variable (`.env`) | Défaut |
|---|---|
| `PERSISTENCE_BACKEND` | `memory` (RAM, perdu au redémarrage) · `sqlite` · `postgres` |
| `WORKING_MEMORY_DB_PATH` | `./database/working_memory/checkpoints.db` (backend `sqlite`) |
| `AGENT_MEMORY_DB_PATH` | `./database/agent_memory/memories.db` (backend `sqlite`) |
| `DATABASE_URL` | *(vide)* — **requis** si backend `postgres` |
| `DATABASE_SCHEMA` | `agent_state` |
| `MEMORY_TTL_DAYS` | `365` (vide = conservation infinie) |

Les deux `_DB_PATH` sont nommées d'après le **rôle**, pas le moteur, et suffixées
ainsi parce que c'est ce qu'elles contiennent — un chemin passé tel quel à
`sqlite3.connect()`. D'où un **`DATABASE_URL`** distinct : une chaîne de connexion
n'est pas un chemin, et `PERSISTENCE_BACKEND` choisit laquelle est lue.

> ✅ **Le backend `postgres` est câblé et vérifié** (déploiement étape 3, le
> 2026-07-25) : `PostgresSaver` + `PostgresStore`, **une seule base**, les deux
> horizons séparés par **schéma** (`agent_state`), recherche sémantique en
> **pgvector** dans cette même base. Deux différences avec SQLite qui ne sont pas
> cosmétiques :
>
> - **Un pool de connexions partagé** (`memory/postgres_conn.py`), pas une
>   connexion : le serveur HTTP répond depuis un pool de threads, et le *sweeper*
>   TTL tourne sur le sien.
> - **Une rétention RGPD réelle** : `MEMORY_TTL_DAYS` arme un balayage de fond qui
>   supprime les souvenirs périmés. ⚠️ Le compteur repart au **dernier accès**, pas
>   à la création — c'est une rétention d'**inactivité**. Non disponible en
>   `sqlite`/`memory`, où le réglage est simplement ignoré.
>
> ⚠️ **L'image officielle `postgres` ne suffit pas** : il faut `pgvector/pgvector`,
> sinon le store long terme échoue à son `setup()`.

Les chemins sont **relatifs au répertoire de lancement** : lance toujours depuis la
**racine du repo** (`make run`, ou `chainlit run packages/client/...`). Les
dossiers parents sont créés à la connexion (`memory/sqlite_conn.py`).
