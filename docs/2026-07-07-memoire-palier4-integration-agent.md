# Mémoire — Palier 4 : brancher la mémoire dans l'agent

_Note pédagogique, 2026-07-07. Fichiers concernés : `src/velmo/agent.py`,
`src/velmo/memory/manager.py`, `tests/acceptance/test_memory_agent.py`._

> Les paliers 1 à 3 ont construit un **cerveau mémoire** complet et testé. Mais
> ce cerveau était posé **à côté** de l'agent, pas **dans** son pipeline. Ce
> palier ferme trois fils débranchés : le contexte que l'agent reconstituait puis
> jetait, l'oubli qu'aucune phrase ne déclenchait, les faits qu'aucun échange ne
> retenait. À la fin, la mémoire *sert* enfin en conversation, et 3 tests
> d'intégration le prouvent. Prérequis :
> [palier 3](2026-07-07-memoire-palier3-episodique.md).

---

## 1. Le vrai problème : une mémoire branchée à vide

Un piège classique. Les 4 tests d'acceptance mémoire passaient — mais ils
appellent `MemoryManager` **en direct**. Le parcours réel d'un client passe par
`Agent.respond`, et là, trois fils pendaient dans le vide :

| Fil | Symptôme | Conséquence |
|-----|----------|-------------|
| **`read`** | `self.memory.read(...)` appelé, résultat **jeté** | l'agent ne se sert jamais de sa mémoire pour répondre |
| **`forget`** | appelé nulle part dans l'agent | « oublie mon adresse » ne supprimait rien |
| **`remember_fact`** | appelé nulle part | « je porte du L » ne devenait jamais un fait durable |

La leçon : **une brique correcte en isolation n'est pas une brique intégrée.**
Un test qui court-circuite le pipeline peut être vert alors que la fonctionnalité
est morte en production. C'est la différence entre tester le *moteur* sur banc et
tester la *voiture* sur route.

## 2. Correctif 1 — rebrancher `read` sur le LLM

Le contexte était reconstitué… puis perdu, comme un brouillon qu'on rédige et
qu'on ne commit jamais. Or la place lui était **déjà réservée** : le LLM accepte
un 2ᵉ argument `context` (`llm.py`), qui recevait une chaîne vide.

```python
ctx = self.memory.read(user_id, message)      # on garde le résultat
answer = self._handle(user_id, message, ctx)  # on le transmet au routage
...
return self.llm.invoke(SYSTEM_PROMPT, ctx.render(), message)  # au lieu de ""
```

Seul le **repli conversationnel** consomme le contexte : les réponses outils
(commande, stock, FAQ) sont déterministes et interrogent la base directement,
elles n'en ont pas besoin. C'est le plus petit branchement qui rende `read`
utile.

**Le piège de vérification** : `EchoLLM` *ignore* le contexte. Hors-ligne,
l'injection ne se **voit pas** dans la réponse. Il faut donc l'observer là où elle
existe — dans ce que le LLM **reçoit**, pas dans ce qu'il renvoie. D'où le
LLM-espion du test (§5).

## 3. Correctif 2 — l'oubli, une intention de routage

Oublier n'est pas un effet de bord silencieux : c'est une **demande** qui doit
court-circuiter et se confirmer. Sa place est donc dans le routage déterministe,
en tête, **avant** les branches commande (« oublie ma commande O-… » contient un
numéro qui, sinon, partirait vers un autre outil).

```python
if "oubli" in low:
    target = self._forget_target(low)
    if target:
        self.memory.forget(user_id, target)
        return f"C'est oublié : je ne conserve plus votre {target}."
```

Tout se joue dans **comment nommer la cible**. Le choix retenu : **pas un `Enum`
fermé**, mais une liste de reconnaissance *prioritaire*, avec **repli libre**.

```python
for target in _FORGET_TARGETS:     # cibles connues : taille, clubs, adresse…
    if target in low:
        return target
match = _FORGET_FALLBACK_RE.search(low)   # sinon : le mot après « mon/ma/mes »
return match.group(1) if match else None
```

Pourquoi pas d'`Enum` : la couche `store.forget` accepte **n'importe quelle
chaîne** (elle efface tout fait ou épisode qui la contient). Figer un vocabulaire
fini **refermerait** le droit à l'oubli (R5) — un client qui dit « oublie mon
email » ne serait pas entendu. Le `frozenset` n'est donc pas une barrière mais un
**raccourci** : il couvre proprement les cibles qu'on sait retenir, et pour le
reste on dégrade en douceur plutôt que de refuser.

## 4. Correctif 3 — classer l'information à l'écriture

Où extraire les faits ? Deux écoles : dans l'agent (symétrique du routage), ou
dans la mémoire. On a choisi la mémoire, car c'est le **canon** de l'architecture
à étages : *« à l'écriture, on classe l'info — un fait durable va en
relationnel »*. Bonus : `write` est **déjà appelé** à chaque tour, donc l'agent
reste inchangé.

```python
self._store.add_episode(user_id, "user", user_message)     # le tour → journal
self._store.add_episode(user_id, "assistant", assistant_message)
for key, value in _extract_facts(user_message).items():    # la préférence → fait
    self._store.upsert_fact(user_id, key, value)
```

L'extraction (`_extract_facts`) est **volontairement conservatrice** : des motifs
déterministes pour quatre préférences (taille, clubs, segment, canal). Mieux
vaut **rater** un fait — il reste rappelable en épisodique — que d'en **inventer**
un faux. Un fait promu devient une **vérité structurée** : toujours chargée,
insensible au rognage de budget, contrairement au souvenir épisodique qui peut
être coupé.

## 5. La preuve : tester la voiture, pas le moteur

Trois tests passent désormais par `Agent.respond` de bout en bout :

| Test | Ce qu'on observe | Ce que ça démontre |
|------|------------------|--------------------|
| `test_read_context_reaches_llm` | le LLM-espion reçoit `taille=L` en contexte | `read` **atteint** le LLM (correctif 1) |
| `test_forget_via_respond` | après « oublie mon adresse », l'adresse a disparu | l'oubli est **déclenché** par la conversation (correctif 2) |
| `test_facts_persist_across_sessions_via_respond` | session 2 retrouve `segment=revendeur`, `taille=L` | un fait dit à l'oral **survit** au changement de session (correctif 3) |

L'astuce clé est le **LLM-espion** : puisque `EchoLLM` ignore le contexte, on
substitue un faux LLM qui *mémorise ce qu'on lui passe*. On teste ainsi le
**branchement**, pas la génération. Bilan : **14 tests verts** (7 métier + 4
mémoire + 3 intégration), zéro régression.

## 6. Ce qui reste (volontairement)

- **Rappel sémantique (Chroma).** Le long terme reste en recouvrement lexical
  (mots exacts). Brancher les embeddings e5 + Chroma derrière `MemoryStore` — le
  vrai « palier 5 » demandé par l'expert — remplacera *mot* par *sens*.
- **Extraction limitée à quatre motifs.** Pas d'extraction pilotée par le LLM :
  ce qui n'entre pas dans un motif reste capté en épisodique, pas en fait
  structuré. Choix assumé (simplicité, zéro faux fait).
- **Contexte injecté au seul repli conversationnel.** Les branches outils n'en
  bénéficient pas — elles n'en ont pas besoin aujourd'hui, mais ce serait le point
  d'extension si un outil devait tenir compte d'une préférence mémorisée.

> Suite logique : le rappel **sémantique** via Chroma, qui referme la dernière
> case de la stack imposée par l'expert pour la mémoire.
