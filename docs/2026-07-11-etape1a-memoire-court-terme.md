# 2026-07-11 — Étape 1a : mémoire court terme

_Note de cours, niveau débutant Python / agents IA._

## Où on en est

Le chantier « mémoire » du brief (`docs/reco_expert.md`) demande 6 garanties
(R1 à R6) : rappel sur une longue conversation, persistance entre sessions,
isolation par client, tenue d'une fenêtre de contexte, droit à l'oubli,
traçabilité. C'est beaucoup d'un coup. `docs/architecture-agent-support.md`
nous dit que la mémoire d'un agent se construit classiquement **en étages** :

| Étage | Répond à… | Support classique |
|-------|-----------|--------------------|
| **Court terme** (aujourd'hui) | « de quoi parle-t-on *là* ? » | tampon en RAM |
| Long terme factuel (plus tard) | « quels faits durables sur ce client ? » | base relationnelle |
| Long terme épisodique (plus tard) | « m'a-t-il déjà parlé de X ? » | base vectorielle |

On construit un étage à la fois. Aujourd'hui : **le court terme seul**. Les
autres étages restent des stubs no-op — c'est voulu, pas oublié.

## Le code

Tout se passe dans `src/velmo/memory/__init__.py`, classe `MemoryManager`.

### Le tampon : un dictionnaire Python, rien de plus

```python
self._history: dict[str, list[Turn]] = {}
```

`Turn` est juste un alias pour `tuple[str, str]` (le rôle et le contenu,
par ex. `("user", "Ma commande est O-2024-0101.")`). La clé du dictionnaire
est `user_id` : chaque utilisateur a sa propre liste de tours. C'est ce qui
donne l'**isolation** — Marc ne peut structurellement pas lire la liste de
Sophie, ce sont deux entrées différentes du dict.

Pourquoi un simple dict et pas une base de données ? Parce que la mémoire
court terme, par définition, **n'a pas vocation à survivre** à la conversation
en cours. C'est le tampon RAM de la note de référence — équivalent à la
fenêtre de contexte d'un prompt. La persistance réelle (redémarrage du
process, plusieurs sessions) est le travail de l'étage suivant (long terme
factuel / épisodique), qui utilisera Postgres et Chroma comme l'impose
`reco_expert.md`. Mélanger les deux maintenant serait prématuré : on n'a pas
encore besoin de cette complexité pour satisfaire *ce* comportement précis.

### Écrire un échange

```python
def write(self, user_id, user_message, assistant_message):
    turns = self._history.setdefault(user_id, [])
    turns.append(("user", user_message))
    turns.append(("assistant", assistant_message))
    self._trim_to_budget(turns)
```

`setdefault` crée la liste au premier message d'un utilisateur, sinon
récupère la liste existante — un idiome Python courant pour « donne-moi la
valeur, ou une valeur par défaut si elle n'existe pas encore ».

### Lire le contexte

```python
def read(self, user_id, message):
    return MemoryContext(history=list(self._history.get(user_id, [])))
```

On renvoie une **copie** de la liste (`list(...)`) plutôt que la liste
elle-même, pour que l'appelant ne puisse pas modifier le tampon interne par
accident en manipulant l'objet renvoyé.

### Le budget de tokens : une approximation assumée

Un vrai tokenizer (celui du modèle LLM utilisé) est une dépendance lourde
pour un besoin simple. On utilise une heuristique courante en prompt
engineering : **~4 caractères ≈ 1 token** en français/anglais. C'est
approximatif mais suffisant pour éviter qu'un tampon ne grossisse sans
limite :

```python
_CHARS_PER_TOKEN = 4

def _trim_to_budget(self, turns: list[Turn]) -> None:
    while len(turns) > 1 and self._approx_tokens(turns) > self.token_budget:
        turns.pop(0)
```

`turns.pop(0)` retire le **premier** élément de la liste — donc le tour le
plus ancien. On répète tant qu'on dépasse le budget, en gardant toujours au
moins 1 tour (`len(turns) > 1`) pour ne jamais vider complètement un
échange en cours.

### `inspect()` : la traçabilité, même partielle

`inspect(user_id)` renvoie maintenant `{"history": [...], "facts": {},
"episodic": []}` — on peut déjà voir ce que le court terme a retenu pour un
utilisateur donné. `facts` et `episodic` restent vides : ce sera à l'étage
suivant de les remplir.

## Ce qui reste rouge, et pourquoi c'est normal

`tests/acceptance/test_memory.py` (le contrat figé des 3 chantiers) a 4
tests :

- `test_recall_over_30_turns` — **passe déjà**, par chance : avec le budget
  par défaut (2000 tokens ≈ 8000 caractères), 31 échanges tiennent encore
  dans le tampon. Ça ne restera pas vrai indéfiniment (une conversation plus
  longue ferait sortir le premier tour) : le vrai rappel longue conversation
  viendra de la recherche épisodique, à construire.
- `test_cross_session_persistence` — **rouge**, attendu : deux
  `MemoryManager()` sont deux tampons RAM indépendants. La persistance
  multi-session est le rôle de l'étage long terme factuel (Postgres).
- `test_isolation_between_customers` — **rouge**, attendu : ce test utilise
  `remember_fact`, qui est encore no-op. L'isolation *du tampon court terme*
  fonctionne déjà (voir nos tests complémentaires), mais pas encore celle
  des faits durables.
- `test_right_to_be_forgotten` — **rouge**, attendu : `forget` est encore
  no-op, à construire avec l'étage long terme.

## Tests complémentaires de cette étape

`tests/test_memory_short_term.py` couvre ce qui est réellement construit
aujourd'hui : rappel intra-session, isolation entre utilisateurs dans une
même instance, troncature effective par `token_budget`, et confirmation
explicite que `remember_fact`/`forget` sont encore no-op (pour éviter une
fausse impression de complétude).

## Prochaine étape

Long terme factuel : une table relationnelle (`user_id`, `key`, `value`)
pour `remember_fact`/`forget`, avec repli SQLite hors-ligne quand `DB_URL`
n'est pas configuré — sur le modèle de `db.py` (`session_factory` /
`fresh_sqlite_session`).
