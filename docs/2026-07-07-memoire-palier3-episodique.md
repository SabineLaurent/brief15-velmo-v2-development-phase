# Mémoire — Palier 3 : l'épisodique, le rappel et le budget

_Note pédagogique, 2026-07-07. Fichier concerné : `src/velmo/memory/manager.py`._

> Ce palier donne au cerveau sa seconde moitié : les **souvenirs de
> conversation**. `write` empile désormais les échanges, et `read` sait
> **retrouver le bon souvenir ancien** parmi des dizaines, puis **tenir la
> fenêtre de contexte** au budget de tokens. À la fin, les 4 tests mémoire
> passent. Prérequis : [palier 2](2026-07-07-memoire-palier2-faits.md).

---

## 1. Deux étages du temps : court terme vs long terme

Un humain ne se souvient pas de la même façon des 3 dernières phrases et d'une
conversation d'il y a un mois. L'agent non plus. `read` sépare les épisodes en
deux :

```python
recent = episodes[-_RECENT:]                                   # court terme
older  = episodes[:-_RECENT] if len(episodes) > _RECENT else []  # long terme
```

- **Court terme** (`recent`) : les `_RECENT = 10` derniers tours, gardés **tels
  quels**. C'est « de quoi parle-t-on là, maintenant ». On les met dans
  `history`.
- **Long terme** (`older`) : tout le reste. On ne le remonte pas en bloc — ce
  serait noyer le contexte. On y **pêche** seulement les souvenirs pertinents.

C'est la distinction de la note d'architecture : la fenêtre du prompt d'un côté,
la mémoire associative de l'autre.

---

## 2. Le rappel par recouvrement lexical

Comment « pêcher » le bon souvenir ancien ? Idée simple et hors-ligne : **le
souvenir pertinent partage des mots avec la question**.

```python
def _tokens(text: str) -> set[str]:
    words = re.split(r"[^0-9a-zàâäéèêëïîôöùûüç]+", text.lower())
    return {w for w in words if len(w) > 2 and w not in _STOP}
```

On réduit un texte à son **ensemble de mots signifiants** : minuscules, sans
ponctuation, et sans les mots vides (`_STOP` : « le », « de », « que »…) qui sont
partout et ne discriminent rien. Puis on **compte les mots communs** :

```python
overlap = len(query & _tokens(content))
```

L'intersection de deux ensembles. La question « Quelle était ma commande
**prioritaire** ? » partage `commande` et `prioritaire` avec le tout premier
tour « Ma **commande prioritaire** est O-2024-0101. » → score 2. Les 30 tours
« Question de suivi *i* sur un maillot » ne partagent rien → score 0. On trie par
score et on garde les `_EPISODIC_K = 3` meilleurs.

C'est l'ancêtre pauvre de la recherche sémantique : on matche des **mots**, pas du
**sens**. « voiture » et « automobile » auraient un score nul. C'est le repli
hors-ligne assumé ; Chroma remplacera ce bloc par une vraie proximité de sens.

---

## 3. `write` : empiler, ne jamais écraser

```python
def write(self, user_id, user_message, assistant_message):
    self._store.add_episode(user_id, "user", user_message)
    self._store.add_episode(user_id, "assistant", assistant_message)
```

Deux lignes, mais la bonne asymétrie avec les faits : un fait s'**upsert**
(un seul état courant), un épisode s'**ajoute** (l'historique grossit). Chaque
échange laisse deux traces horodatées — c'est ce qui rend le rappel possible plus
tard, et la traçabilité (R6) réelle.

Note : `write` **n'extrait pas** de fait automatiquement (« il porte du L » ne
crée pas `taille=L`). Ce serait le rôle d'un LLM ; on l'a laissé de côté, aucun
test ne l'exige.

---

## 4. Le budget de tokens : quoi sacrifier d'abord

La fenêtre de contexte d'un LLM est finie (`token_budget = 2000`). Si le contexte
déborde, il faut couper — mais **pas au hasard** :

```python
while _estimate_tokens(ctx.render()) > self.token_budget and ctx.history:
    ctx.history.pop(0)          # 1. le plus vieux du court terme
while _estimate_tokens(ctx.render()) > self.token_budget and ctx.episodic:
    ctx.episodic.pop()          # 2. le souvenir le moins pertinent
```

L'ordre du sacrifice traduit une hiérarchie de valeur :

1. on lâche **l'historique le plus ancien** en premier (`pop(0)`) — le moins utile ;
2. puis les **souvenirs les moins proches** (`pop()` retire le dernier, or la
   liste est triée par pertinence décroissante → on enlève le moins bon) ;
3. **les faits ne sont jamais touchés** — ils sont durables et compacts.

`_estimate_tokens` fait une approximation grossière (`len // 4`, ~4 caractères
par token) : suffisant pour un garde-fou de taille, sans dépendre d'un vrai
tokeniseur.

---

## 5. `read` : l'assemblage final

Tout se recompose ici :

```python
facts    = self._store.facts(user_id)          # palier 2
episodes = self._store.episodes(user_id)
recent   = episodes[-_RECENT:]                 # court terme
older    = episodes[:-_RECENT] ...             # long terme
episodic = self._retrieve(message, older)      # rappel pertinent
ctx = MemoryContext(history=recent, facts=facts, episodic=episodic)
self._fit_budget(ctx)                          # tenue de la fenêtre
```

Le paramètre `message`, reçu mais inutilisé au palier 2, **sert enfin** : c'est
lui qui pilote la pêche aux souvenirs.

---

## 6. La preuve

| Test | Ce qui est exercé | Verdict |
|------|-------------------|---------|
| `test_recall_over_30_turns` | tour n°1 retrouvé parmi 31 via rappel lexical | ✅ |
| `test_right_to_be_forgotten` | souvenir présent, puis effacé par `forget` | ✅ |
| `test_cross_session_persistence` | faits (palier 2, non régressé) | ✅ |
| `test_isolation_between_customers` | faits (palier 2, non régressé) | ✅ |

Suite complète : **11 passent / 8 échouent** (les 8 = chantiers *garde-fous* et
*MLOps*, non touchés). Aucune régression métier.

---

## 7. Ce qui reste (volontairement)

- **Rappel sémantique (Chroma)** : `_retrieve` matche des mots, pas du sens. Le
  point d'accroche est prêt — remplacer ce bloc derrière la même interface.
- **Extraction de faits par LLM** : aujourd'hui les faits ne s'écrivent que via
  `remember_fact` explicite, jamais déduits d'un échange.
- **Résumé du vieux contexte** : au lieu de simplement couper au budget, on
  pourrait compresser les anciens tours (étage « résumé » de la note d'archi).

Le chantier mémoire (R1–R6) est fonctionnellement bouclé ; ces trois pistes sont
des raffinements, pas des trous.
