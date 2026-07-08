# Mémoire — Recherche et tokenisation

_Note pédagogique, 2026-07-07. Fichier : `src/velmo/memory/manager.py`. En marge du
[palier 3](2026-07-07-memoire-palier3-episodique.md)._

> Comment la mémoire *retrouve* l'information, et sur quelle brique de découpage
> du texte ça repose. Deux comparaisons : les **trois régimes de recherche**, puis
> l'état réel de la **tokenisation**.

---

## Partie 1 — Les trois régimes de recherche

À la lecture (`read`), les deux mémoires ne sont **pas** traitées pareil.

### Mémoire des faits → pas de recherche

```python
facts = self._store.facts(user_id)     # SELECT * WHERE user_id = ?
```

On charge **tous** les faits de l'utilisateur, en bloc. Aucun tri, aucun
classement, aucun filtrage par le message. Le seul « filtre » est l'**isolation
par `user_id`**.

Pourquoi c'est volontaire : les faits sont **peu nombreux et tous durables**
(taille, clubs, segment…). Les remonter tous coûte quasi rien et évite de
« rater » un fait pertinent.

### Mémoire épisodique → deux recherches

Trop volumineuse pour tout remonter. `read` la coupe en deux :

**1. Court terme — par récence** (pas par pertinence)
```python
recent = episodes[-_RECENT:]           # les 10 derniers tours, bruts
```
Une simple tranche par la fin. Le critère est le **temps**, pas le contenu.

**2. Long terme — par recouvrement lexical**
```python
overlap = len(query & _tokens(content))   # mots communs
scored.sort(key=..., reverse=True)        # tri par pertinence
return [...][:_EPISODIC_K]                 # top-3
```
Une **vraie recherche** : pour chaque tour ancien, on compte les **mots
signifiants communs** avec la question, on garde ceux qui en partagent au moins
un, on **classe par nombre de mots partagés**, on prend le top-3. Matching de
**mots exacts**, pas de sens (« voiture » ≠ « automobile »).

### Vue d'ensemble

| | Faits | Épisodique court terme | Épisodique long terme |
|--|-------|------------------------|-----------------------|
| Recherche ? | non (tout) | non (tranche) | **oui** |
| Critère | isolation user | récence | **pertinence lexicale** |
| Classement | — | chronologique | overlap décroissant, top-K |
| Complexité | O(faits) | O(1) | O(n), sans index |

### Le point d'attention

Le long terme est en **force brute linéaire** : on re-scanne tout l'historique à
chaque `read`. Parfait pour une démo (quelques dizaines de tours) ; c'est ce que
**Chroma** remplacerait — recherche **sémantique** (par sens) et **indexée**
(plus proches voisins, sous-linéaire), derrière la même interface `MemoryStore`.

---

## Partie 2 — La tokenisation

### Le pourquoi : rendre le texte *comparable* et *mesurable*

Avant de regarder le code, la vraie question : **à quoi ça sert ?** Une machine ne
sait ni comparer ni mesurer deux phrases brutes. *« Mon flocage se décolle »* et
*« le nom sur le maillot s'abîme »* sont proches par le sens, mais n'ont presque
aucun caractère commun. Tokeniser, c'est passer d'un texte à une représentation
que le code peut **manipuler**. Et dans ce fichier, ça sert deux besoins distincts :

| Besoin | Ce qu'on produit | À quoi ça sert concrètement |
|--------|------------------|------------------------------|
| **Retrouver** (`_tokens`) | un **ensemble de mots** | comparer deux textes → score de pertinence (R1) |
| **Tenir le budget** (`_estimate_tokens`) | un **nombre** | mesurer la taille du contexte → savoir quoi rogner (R4) |

Le premier rend le texte **comparable** (intersection d'ensembles) ; le second le
rend **mesurable** (un compte). Sans le premier, pas de rappel épisodique — le
`_retrieve` n'aurait rien à classer. Sans le second, on ne saurait pas *quand*
arrêter de couper l'historique : soit on dépasse la fenêtre du LLM (erreur, coût
qui explose), soit on tronque trop tôt (on perd du contexte utile). Les deux
usages ci-dessous découlent de ces deux besoins.

### `_tokens()` — une tokenisation lexicale, maison

```python
def _tokens(text: str) -> set[str]:
    words = re.split(r"[^0-9a-zàâäéèêëïîôöùûüç]+", text.lower())
    return {w for w in words if len(w) > 2 and w not in _STOP}
```

C'en est une : découpage en **mots**, minuscules, retrait des mots courts et des
mots vides → un **sac de mots**. C'est ce qui alimente la recherche par
recouvrement. Mais rudimentaire, **au niveau du mot** :

- pas de *stemming*/lemmatisation → « commande » ≠ « commandes » ;
- pas de sous-mots → « prioritaire » sans rapport avec « priorité » ;
- des règles écrites à la main, pas un modèle.

### `_estimate_tokens()` — *pas* une tokenisation

```python
def _estimate_tokens(text: str) -> int:
    return max(1, len(text) // 4)
```

Malgré son nom, ça ne découpe rien : une **règle de trois** (~4 caractères par
token) pour **estimer** la consommation d'un LLM et tenir le budget. On approxime
un nombre, on ne produit pas de tokens.

### Ce qui n'est PAS encore là

| Élément | Tokenisation ? | Usage | État |
|---------|----------------|-------|------|
| `_tokens()` | oui — **mots**, artisanale | recherche lexicale | présent |
| `_estimate_tokens()` | non — heuristique de longueur | budget de contexte | présent |
| tokeniseur du LLM (Kimi-K2.6) | oui — exact | compter précisément le budget | absent |
| embeddings e5 / Chroma | oui — **vecteurs de sens** | recherche sémantique | absent |

Donc à ce stade : une tokenisation **lexicale, au mot, faite maison** — suffisante
pour le rappel hors-ligne. Ni la tokenisation exacte du LLM, ni la vectorisation
sémantique ne sont branchées.

---

## Hors-ligne : sans Chroma, es-tu quand même complet ?

Question naturelle à ce stade : « sans ChromaDB, on est bien sur du hors-ligne ? »
Oui — mais deux précisions changent la lecture.

### Chroma n'est pas *débranché*, il n'est pas encore *branché*

Dans le module mémoire actuel, **aucune ligne n'appelle Chroma**. Le rappel
épisodique est entièrement assuré par `_retrieve` / `_tokens` (recouvrement
lexical). « Sans Chroma » n'est donc pas un **mode dégradé** subi : c'est l'**état
nominal** du palier. Les 4 tests d'acceptance mémoire passent ainsi.

### Ce qui te rend hors-ligne, c'est SQLite — pas l'absence de Chroma

Le vrai repli est dans `store.py` :

```python
def _default_url() -> str:
    env = os.getenv("MEMORY_DB_URL")
    if env:
        return env
    path = Path.home() / ".velmo" / "memory.db"   # ← repli fichier
    return f"sqlite:///{path}"
```

Sans `MEMORY_DB_URL`, on écrit dans un **fichier** `~/.velmo/memory.db` — pas du
SQLite en mémoire. C'est *ça* qui garantit la persistance multi-session (R2) sans
aucun service.

### Le tableau des dépendances

| Étage | Service « en ligne » | Repli hors-ligne actuel | Sans lui, tu perds… |
|-------|----------------------|--------------------------|----------------------|
| Faits + journal | Postgres (`MEMORY_DB_URL`) | fichier SQLite | rien de fonctionnel — juste le SGBD partagé |
| Rappel épisodique | Chroma + embeddings e5 | recouvrement lexical (`_tokens`) | le rappel **sémantique** (« flocage » ≈ « nom sur le maillot ») |

**Réponse honnête** : tu es entièrement hors-ligne et fonctionnellement correct.
L'absence de Chroma ne casse rien — elle change la *qualité* du rappel (mots exacts
au lieu du sens), pas sa présence. Seule limite à garder en tête : la recherche
lexicale est en **force brute O(n)** (re-scan complet à chaque `read`), ce que
l'indexation de Chroma résoudrait le jour venu.

## Le fil rouge

Les deux comparaisons pointent le **même** point d'évolution : passer du **mot**
au **sens**. La recherche lexicale (force brute sur des mots exacts) et la
tokenisation maison sont les deux faces du repli hors-ligne. Les brancher sur les
embeddings e5 + Chroma remplacerait *à la fois* la tokenisation et la recherche
par leurs versions sémantiques et indexées.
