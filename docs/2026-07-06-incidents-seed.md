# Incidents rencontrés à la mise en route (seed / seed-kb)

Date : 2026-07-06 — première mise en route de l'environnement complet
(`make up && make migrate && make seed && make seed-kb`).

---

## 1. `make seed` — `ForeignKeyViolation` sur les escalations

### Symptôme

```
psycopg.errors.ForeignKeyViolation: insert or update on table "escalations"
violates foreign key constraint "escalations_order_id_fkey"
DETAIL:  Key (order_id)=(O-2024-0110) is not present in table "orders".
```

Le `make seed` échouait au `session.commit()` de `src/velmo/sampledata.py`.

### Diagnostic

La commande `O-2024-0110` **existe pourtant** dans les données de référence
(`_orders()`). Les données étaient donc cohérentes : le problème venait de
l'**ordre d'insertion**.

En capturant le SQL émis, on voyait `INSERT INTO customers` puis directement
`INSERT INTO escalations` — **avant** les `orders`.

Cause racine : l'unit-of-work de SQLAlchemy ordonne les `INSERT` d'après les
**`relationship()`** déclarés entre modèles, **pas** d'après les colonnes
`ForeignKey`. Dans `db.py` :

- `OrderItem` a `order: relationship(back_populates="items")` → correctement
  inséré après `orders`.
- `Shipment`, `Return`, `Refund`, `Escalation` n'ont **qu'une colonne
  `ForeignKey`, sans `relationship()`** → l'UOW ne connaît pas la dépendance
  `escalations → orders` et choisit un ordre arbitraire, ici défavorable.

Pourquoi invisible jusque-là : la suite de tests tourne sur **SQLite en
mémoire**, qui **n'applique pas les contraintes de clé étrangère** par défaut.
Le bug ne se manifeste donc que sur **Postgres**, au `make seed`.

### Correctif appliqué

Dans `seed()` (`src/velmo/sampledata.py`), un `session.flush()` après chaque
`add_all` force l'envoi de chaque lot dans l'ordre de la boucle (déjà correct) :

```python
for batch in (...):
    session.add_all(batch)
    session.flush()  # force l'INSERT du lot avant le suivant (respecte l'ordre des FK)
session.commit()
```

Correctif minimal (1 ligne), sans modification du schéma.
Commit : `b242aef`.

### Alternative écartée

Ajouter les `relationship()` manquants sur `Escalation`/`Shipment`/`Return`/
`Refund` corrigerait aussi la cause. Écarté : ça touche le schéma (`db.py`)
pour un besoin que rien d'autre n'exprime aujourd'hui, hors du scope du bug.

### Point de vigilance

Comme la SQLite de test ignore les FK, d'autres incohérences de clés étrangères
pourraient passer les tests tout en cassant sur Postgres. À garder en tête.

---

## 2. `make seed-kb` — `ModuleNotFoundError: No module named 'chromadb'`

### Symptôme

```
File "scripts/seed_kb.py", line 16, in main
    import chromadb
ModuleNotFoundError: No module named 'chromadb'
```

### Diagnostic

Ce n'est **pas un bug** : `chromadb` fait partie de l'extra **`vector`**,
optionnel et désactivé par défaut (voir `CLAUDE.md`). `make seed-kb` requiert
cet extra installé **et** un service Chroma joignable.

Or le code prévoit un **repli hors-ligne** : sans Chroma, `kb_store.py` bascule
sur `LocalKB` (TF-IDF léger sur `kb/docs/*.md`). La suite d'acceptance et le
REPL fonctionnent donc entièrement sans Chroma.

### Décision

`make seed-kb` **ignoré** (option A) : non nécessaire pour les trois chantiers
en cours (mémoire, garde-fous, MLOps), qui tournent tous hors-ligne. La FAQ
reste servie par `LocalKB`.

### Pour activer Chroma plus tard (option B)

```bash
uv sync --extra vector     # installe chromadb + sentence-transformers (télécharge le modèle e5, lourd)
```

Deux pièges relevés :

- le script pointe par défaut sur `CHROMA_HOST=chroma` (nom du service dans le
  réseau docker) ; depuis l'hôte, Chroma écoute sur `localhost:8001`, d'où :
  `CHROMA_HOST=localhost CHROMA_PORT=8001 make seed-kb` ;
- l'installation télécharge le modèle d'embeddings e5 via `sentence-transformers`.
