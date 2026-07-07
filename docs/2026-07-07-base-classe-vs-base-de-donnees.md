# `Base` (la classe) ≠ base de données

_Note pédagogique, 2026-07-07. En marge du chantier mémoire
([palier 1](2026-07-07-memoire-palier1-persistance.md))._

> Point de vocabulaire qui prête à confusion : dans SQLAlchemy, avoir **deux
> `Base`** dans le code ne veut **pas** dire avoir deux bases de données. Cette
> note lève l'ambiguïté.

---

## Deux sens du mot « base »

| Terme | Ce que c'est | Où ça vit |
|-------|--------------|-----------|
| `Base` (la classe) | un **registre Python** de définitions de tables (`DeclarativeBase`) | dans le code |
| une base de données | le **serveur/fichier réel** qui stocke les données (Postgres, SQLite…) | sur disque / serveur |

Le registre Python dit *quelles tables existent*. C'est **l'URL de connexion
(l'engine)** qui décide *dans quelle base réelle* elles sont créées. Deux `Base`
(classes) ne forcent donc pas deux bases de données.

---

## Le cas Velmo : deux registres

- `db.py` a **son** `Base` → tables métier (`customers`, `orders`, `products`…),
  connectées via `DB_URL`.
- `store.py` a **son** `Base` → tables mémoire (`memory_facts`,
  `memory_episodes`), connectées via `MEMORY_DB_URL`.

Chaque classe qui hérite d'un `Base` **s'enregistre** dans le `metadata` de ce
`Base`. D'où `create_all` de la mémoire ne crée **que** les deux tables mémoire —
il ignore complètement les tables métier, qui vivent dans l'*autre* registre.

---

## Qui décide « une ou deux bases » ? L'URL

C'est la configuration, pas le code, qui tranche :

| Configuration | Base métier | Base mémoire | Résultat |
|---------------|-------------|--------------|----------|
| **Par défaut (hors-ligne)** | Postgres (ou rien) via `DB_URL` | fichier `~/.velmo/memory.db` | **séparées** (Postgres ≠ fichier SQLite) |
| `MEMORY_DB_URL` = **même** URL que `DB_URL` | Postgres X | Postgres X | **une seule base** ; tables mémoire *à côté* du métier |
| `MEMORY_DB_URL` = **autre** Postgres | Postgres X | Postgres Y | **deux bases Postgres** distinctes |

---

## L'analogie : deux locataires, même immeuble

Même quand métier et mémoire partagent **la même** base Postgres :

- les deux `Base` (registres) restent séparés dans le code ;
- les tables mémoire **cohabitent** avec les tables métier dans la même base,
  sans se marcher dessus.

Partager **l'adresse** (la base Postgres) ne veut pas dire partager
**l'appartement** (le jeu de tables). Deux `Base` distincts en code et une seule
base de données réelle : les deux dimensions sont **indépendantes**.

---

## Reco pratique

La reco de l'expert dit « Postgres = source de vérité des faits durables ». Le
plus simple et fidèle en production : **`MEMORY_DB_URL = DB_URL`** → une seule
base Postgres, tables mémoire à côté du métier. Une base dédiée à la mémoire ne se
justifie que pour des raisons d'exploitation (scaling, sauvegardes séparées) —
inutile ici.

Et les deux `Base` **distincts** en code sont un choix délibéré (le module mémoire
ne dépend pas du schéma métier), totalement indépendant de la question « une ou
deux bases Postgres ».
