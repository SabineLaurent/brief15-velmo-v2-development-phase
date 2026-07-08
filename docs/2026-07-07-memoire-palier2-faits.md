# Mémoire — Palier 2 : les faits durables dans le cerveau

_Note pédagogique, 2026-07-07. Fichiers concernés : `src/velmo/memory/manager.py`
et `src/velmo/memory/__init__.py`._

> Ce palier fait deux choses : il **range le code** (refactor en vitrine +
> implémentation) et il **branche les faits** dans l'orchestrateur
> `MemoryManager`. À la fin, la moitié « faits durables » de la mémoire est
> vivante ; la moitié « souvenirs de conversation » reste branchée sur du vide,
> en attendant le palier 3. Prérequis :
> [palier 1](2026-07-07-memoire-palier1-persistance.md).

---

## 1. Le refactor : « vitrine » vs implémentation

Avant, tout vivait dans `__init__.py`. Désormais :

```
memory/
├── __init__.py   ← la VITRINE (ce que le package expose)
├── manager.py    ← le CERVEAU (l'orchestration)
└── store.py      ← le MAGASIN (la persistance, palier 1)
```

Le `__init__.py` d'un package Python est ce qui s'exécute quand on écrit
`import velmo.memory`. Réduit à une façade :

```python
from .manager import MemoryContext, MemoryManager
from .store import Turn

__all__ = ["MemoryContext", "MemoryManager", "Turn"]
```

il déclare : « le package mémoire, vu de l'extérieur, **c'est ces trois noms** ;
peu importe le fichier où ils sont écrits ». C'est le **motif de la façade** : la
vitrine annonce le contrat public, le stock est rangé à l'arrière.

Conséquence qui compte : `agent.py` et `test_memory.py` écrivent toujours
`from velmo.memory import MemoryManager` — **inchangé**. On a réorganisé la
cuisine sans déplacer l'enseigne. Règle d'or : ne jamais bouger la cible que le
test doit atteindre.

`__all__` est une liste explicite des noms officiels : de la documentation
exécutable. Un `from velmo.memory import *` n'obtient que ces trois-là, pas les
détails internes.

---

## 2. Le branchement : l'injection de dépendance

```python
def __init__(self, *, token_budget=2000, store: MemoryStore | None = None):
    self.token_budget = token_budget
    self._store = store or MemoryStore()
```

La ligne `store or MemoryStore()` est le patron **injection de dépendance avec
repli** :

- **Cas normal** (`MemoryManager()`) : personne ne fournit de store → on en
  fabrique un par défaut (le fichier SQLite du palier 1). C'est ce qui fait que
  les tests, qui appellent `MemoryManager()` sans argument, marchent seuls.
- **Cas injecté** (`MemoryManager(store=...)`) : on peut fournir un magasin de
  test ou un Postgres réel **sans changer une ligne** du `MemoryManager`.

Le cerveau *dépend* d'un magasin mais ne *décide pas* lequel : il l'accepte de
l'extérieur, avec une valeur de secours. C'est ce qui garde les deux couches
découplées.

Le `*` dans la signature force à **nommer** les arguments (`MemoryManager(
token_budget=1000)`, jamais `MemoryManager(1000)`). Ça évite les erreurs
silencieuses le jour où l'ordre des paramètres change.

---

## 3. `read` : où les faits deviennent du contexte

```python
def read(self, user_id: str, message: str) -> MemoryContext:
    return MemoryContext(facts=self._store.facts(user_id))
```

`read` va chercher **les faits de cet utilisateur** (isolation garantie en
dessous, palier 1) et les emballe dans un `MemoryContext`. Trois remarques :

- Le paramètre `message` est **reçu mais pas encore utilisé** : au palier 2 on
  remonte *tous* les faits (durables, peu nombreux). Il servira au palier 3 pour
  choisir *quels souvenirs* épisodiques sont pertinents.
- `MemoryContext` est un conteneur à trois tiroirs : `history`, `facts`,
  `episodic`. Ici on ne remplit que `facts`.
- Sa méthode `render()` transforme le conteneur en **texte plat**, injectable
  dans un prompt :

```python
for key, value in self.facts.items():
    parts.append(f"fact:{key}={value}")
```

C'est là qu'opèrent les tests. `remember_fact("acc-marc", "taille", "L")`
devient, au `render()`, la ligne `fact:taille=L`. Le test vérifie juste
`assert "L" in rendered` — et « L » est bien dans « fact:taille=L ». Idem « OM »
dans `fact:clubs=OM et Brésil`, « revendeur » dans `fact:segment=revendeur`. **Le
test ne vérifie pas une structure, il vérifie qu'une info survit et ressort dans
le texte final.**

---

## 4. Les trois délégations

```python
def remember_fact(self, user_id, key, value):  self._store.upsert_fact(...)
def forget(self, user_id, target) -> int:       return self._store.forget(...)
def inspect(self, user_id) -> dict:             return {"facts": ..., "episodic": ...}
```

Ces méthodes ne font presque rien elles-mêmes : elles **passent la main au
store**. Règle de conception : *le cerveau décide, le magasin exécute*.
`remember_fact` ignore si l'on est sur SQLite ou Postgres — il dit « range ce
fait » et le store se débrouille.

`inspect` est l'outil de **traçabilité** (exigence R6 : « on doit pouvoir
inspecter ce qui a été retenu »). Il rend une photo lisible de la mémoire d'un
client : ses faits + ses souvenirs. Aujourd'hui les souvenirs sont vides, car…

---

## 5. Pourquoi deux tests échouent encore — et c'est correct

```python
def write(self, user_id, user_message, assistant_message):
    return None  # palier 3
```

`write` est **volontairement inerte**. Or `test_recall_over_30_turns` et
`test_right_to_be_forgotten` commencent tous deux par `write(...)` pour déposer un
souvenir. Rien n'étant écrit, `read` remonte un contexte vide et l'assertion tombe
(`assert 'O-2024-0101' in ''`).

Ce n'est pas un bug, c'est la **frontière du palier**. Le tableau attendu :

| Test | Statut | Palier |
|------|--------|--------|
| `test_cross_session_persistence` | ✅ passe | 2 (faits) |
| `test_isolation_between_customers` | ✅ passe | 2 (faits) |
| `test_recall_over_30_turns` | ❌ | 3 (épisodique) |
| `test_right_to_be_forgotten` | ❌ | 3 (épisodique) |

Deux verts (faits), deux rouges (épisodique) : la signature exacte d'un travail
fait *pas à pas*. Chaque rouge est un contrat encore ouvert, pas une régression.

---

## 6. En une phrase

Le palier 2 a donné au cerveau **la moitié de sa mémoire** — la partie « faits
durables », exacte et isolée par client — et laissé l'autre moitié, les
« souvenirs de conversation », branchée sur du vide.

**Suite → palier 3** : `write` alimente l'épisodique, et `read` fusionne faits +
**récupération par recouvrement lexical** (retrouver le tour n°1 parmi 30) + tenue
du **budget de tokens**.
