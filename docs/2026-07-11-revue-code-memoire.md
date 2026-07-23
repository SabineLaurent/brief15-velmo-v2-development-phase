# 2026-07-11 — Revue de code : chantier Mémoire

_Revue technique du chantier mémoire (court terme, long terme épisodique, long
terme factuel). Périmètre : `src/velmo/memory/` (`__init__.py`, `episodic.py`,
`facts.py`), le câblage dans `src/velmo/agent.py`, le schéma `MemoryFact`
(`db.py`), la migration `0002_memory_facts.py` et les tests associés._

Référentiels utilisés : `docs/reco_expert.md` (exigences R1–R6),
`docs/architecture-agent-support.md` (mémoire à étages), et les trois notes de
cours (`2026-07-11-etape1a/1b/1c`).

**Bilan global.** Le travail est solide : les trois étages sont en place, le
patron « backend réel + repli hors-ligne » est respecté avec cohérence, les
choix sont documentés et assumés dans les notes de cours, et les 16 tests
mémoire passent. Les remarques ci-dessous portent surtout sur **l'intégration
bout-en-bout** (la mémoire est construite mais pas encore consommée par
l'agent) et sur quelques **écarts par rapport aux exigences** (R4, R6) qui sont
documentés comme « assumés » mais méritent d'être traités avant de considérer
le chantier clos.

---

## Partie 1 — Remarques concises

### Priorité haute — intégration & exigences

- **[H1] La mémoire est écrite mais jamais relue par l'agent.**
  `agent.py:77` appelle `self.memory.read(...)` et **jette le résultat** ; le
  LLM est invoqué avec un contexte vide (`agent.py:138`, argument `""`). Tout
  le rappel (historique, faits, épisodique) n'a donc **aucun effet** sur les
  réponses réelles. R1/R2 sont vrais en isolation (tests directs sur
  `MemoryManager`) mais invisibles de bout en bout.

- **[H2] `write()` ne classe pas l'information : tout part en épisodique, rien
  en factuel.** La note d'architecture demande de trier à l'écriture (fait
  durable → relationnel, narratif → épisodique). Ici `write()` déverse chaque
  échange dans l'épisodique, et `remember_fact`/`forget` ne sont **jamais
  appelés dans le pipeline** de l'agent. L'étage factuel n'est alimenté que par
  les tests.

- **[H3] `inspect()` renvoie un épisodique vide sur le backend réel (Chroma).**
  `ChromaEpisodicStore.all_for()` retourne `[]` en dur (`episodic.py:79-82`).
  La traçabilité R6 fonctionne hors-ligne mais **pas en production** — là où
  elle compte le plus.

### Priorité moyenne — robustesse & conformité

- **[M1] R4 (fenêtre de contexte) n'est tenue que sur l'historique.**
  `token_budget` ne borne que `self._history` ; les faits (`all_for`) et
  l'épisodique (`k=3`) s'ajoutent **par-dessus, non comptés**. Le contexte
  rendu peut dépasser le budget annoncé.

- **[M2] Le repli SQLite en mémoire n'est pas thread-safe.**
  `create_engine("sqlite://")` utilise un `SingletonThreadPool` : une seconde
  thread reçoit une **base vide distincte**. Sûr pour le CLI/les tests
  (mono-thread), cassant pour un serveur multi-thread hors-ligne. À signaler
  car les tests reposent sur cet engine global mis en cache.

- **[M3] État global mutable partagé entre tests, sans réinitialisation.**
  L'engine SQLite est mis en cache au niveau module (`facts.py:64`). Les tests
  ne s'isolent que par des `user_id` uniques — discipline fragile, pas de
  teardown.

- **[M4] `forget` : sur-suppression possible et sémantique du compteur floue.**
  La correspondance par sous-chaîne insensible à la casse fait que
  `forget(user, "L")` supprimerait beaucoup. Et la valeur de retour **somme
  les trois étages** : un tour présent à la fois dans `history` et l'épisodique
  est compté deux fois.

### Priorité basse — finitions

- **[L1] `LocalEpisodicStore` grossit sans borne** (aucune éviction) — assumé,
  mais à surveiller sur longue session.
- **[L2] Aucun try/except runtime autour de Chroma/SQL** : une panne backend
  fait remonter l'exception jusqu'à `respond()`.
- **[L3] Les faits sont injectés en totalité, sans filtrage par pertinence**
  au message courant (`all_for` renvoie tout).
- **[L4] `render()` mélange souvenirs rappelés et tours récents** sans
  marqueur : le LLM ne distingue pas « ceci vient d'être dit » de « ceci a été
  retrouvé en mémoire ».

---

## Partie 2 — Remarques détaillées (pourquoi & comment)

### [H1] La mémoire est écrite mais jamais relue par l'agent

**Constat.** Dans `agent.py` :

```python
def respond(self, user_id: str, message: str) -> str:
    ...
    self.memory.read(user_id, message)      # ← résultat jeté
    answer = self._handle(user_id, message)
    ...
    self.memory.write(user_id, message, answer)
    return answer
```

et le fallback conversationnel :

```python
return self.llm.invoke(SYSTEM_PROMPT, "", message)   # ← contexte = ""
```

**Pourquoi c'est important.** C'est le point le plus structurant de la revue.
Les trois étages sont corrects *pris isolément* (les tests le prouvent en
appelant `MemoryManager` directement), mais l'agent n'exploite **aucun** de
leurs résultats : le `MemoryContext` reconstitué est immédiatement abandonné, et
le LLM reçoit un contexte vide. Concrètement, un client qui donne son numéro de
commande au tour 1 ne bénéficiera d'aucun rappel au tour 30 *dans une vraie
conversation via l'agent* — R1 et R2 n'ont d'existence que dans les tests
unitaires. La `reco_expert.md` exige une mémoire « exemplaire » ; elle n'est
pour l'instant pas branchée sur la génération.

**Comment corriger.** Injecter le contexte rendu dans le prompt du LLM (et, à
terme, dans le routage) :

```python
context = self.memory.read(user_id, message)
answer = self._handle(user_id, message, context)
...
# dans _handle, pour le fallback conversationnel :
return self.llm.invoke(SYSTEM_PROMPT, context.render(), message)
```

`llm.invoke` accepte déjà un deuxième argument « contexte » — il suffit de le
remplir. Ajouter un test bout-en-bout au niveau `Agent` (pas seulement
`MemoryManager`) qui vérifie qu'une information donnée au tour 1 ressort dans une
réponse ultérieure garantirait la non-régression de ce câblage.

> Note : `agent.py` fait partie du scaffold initial, mais brancher la mémoire
> sur la génération relève bien du chantier mémoire — sans ce fil, l'étage
> construit reste théorique.

---

### [H2] `write()` ne classe pas l'information (fait durable vs narratif)

**Constat.** `docs/architecture-agent-support.md` est explicite sur l'écriture :

> « À l'écriture (`write`) : on classe l'info — un fait durable (« je suis
> revendeur ») va en relationnel ; un échange narratif va en épisodique. »

Or `MemoryManager.write()` envoie **systématiquement** les deux tours vers
l'épisodique, et rien ne peuple l'étage factuel automatiquement.
`remember_fact` et `forget` existent mais **ne sont appelés nulle part** dans
`agent.py`.

**Pourquoi c'est important.** L'étage factuel (Postgres, « source de vérité »
selon `reco_expert.md`) est celui qui porte la persistance multi-session R2 et
les préférences exactes (« pointure L »). S'il n'est jamais alimenté en
condition réelle, R2 repose uniquement sur des faits injectés à la main dans les
tests. On a construit le tiroir mais aucune main n'y range quoi que ce soit.

**Comment corriger.** Introduire une étape de classification légère à
l'écriture. Deux niveaux possibles, du plus simple au plus riche :

1. **Règles/patterns** (dans l'esprit déterministe du reste de l'agent) :
   détecter les tournures « je suis / ma pointure / je préfère / mon adresse
   est… » et router vers `remember_fact(user_id, clé, valeur)`.
2. **Extraction LLM** : un appel dédié « extrais les faits durables de ce
   message au format clé-valeur », déclenché après la réponse.

Documenter le choix dans une note de cours (comme les précédentes) et le
couvrir par un test : « après un échange contenant une préférence, celle-ci est
retrouvée via `inspect().facts` ».

---

### [H3] `inspect()` renvoie un épisodique vide en production

**Constat.**

```python
class ChromaEpisodicStore:
    def all_for(self, user_id: str) -> list[str]:
        # Chroma n'a pas d'équivalent simple à un "SELECT *" ...
        return []
```

`inspect()` s'appuie sur `all_for` pour l'étage épisodique. Hors-ligne
(`LocalEpisodicStore`) il renvoie bien tout ; avec Chroma configuré (la prod),
il renvoie toujours `[]`.

**Pourquoi c'est important.** R6 (traçabilité) et le principe directeur
« on doit pouvoir inspecter ce qui a été retenu » de `reco_expert.md`. La
capacité d'audit tombe silencieusement dès qu'on passe sur le vrai backend —
c'est exactement le contexte où l'on veut pouvoir prouver ce qui est mémorisé
(RGPD, litige client). Un `return []` muet est pire qu'une erreur : il donne
l'illusion d'une mémoire épisodique vide.

**Comment corriger.** Chroma sait paginer via `collection.get(where=...)` :

```python
def all_for(self, user_id: str) -> list[str]:
    result = self._collection.get(where={"user_id": user_id})
    return list(result.get("documents", []))
```

`collection.get` (sans `query_texts`) renvoie tous les documents filtrés par
métadonnée — exactement ce qu'il faut, la même API que celle déjà utilisée dans
`forget`. Si l'on craint le volume, paginer par `limit`/`offset`. À défaut de
pouvoir tester Chroma hors-ligne, au minimum lever un `NotImplementedError`
explicite plutôt que de renvoyer `[]`, pour ne pas masquer le trou.

---

### [M1] R4 n'est tenue que sur l'historique court terme

**Constat.** `_trim_to_budget` ne rogne que `self._history`. Dans `read`, on
ajoute ensuite `k=3` souvenirs épisodiques et **tous** les faits, sans les
compter dans `token_budget`. La note 1b assume ce choix (« budget combiné
strict = point de retouche identifié »).

**Pourquoi c'est important.** R4 demande de « tenir la fenêtre de contexte ». Le
`MemoryContext.render()` réellement injecté dans le prompt peut dépasser le
budget annoncé, puisque deux des trois étages ne sont pas comptabilisés. Tant
que les faits restent peu nombreux et `k=3`, l'impact pratique est faible — mais
l'exigence, telle qu'écrite, porte sur le contexte *rendu*, pas sur le seul
tampon court terme.

**Comment corriger.** Deux options proportionnées :

- **Minimal** : tronquer `render()` (ou une méthode dédiée) au budget global
  après fusion, en gardant l'ordre de priorité de la note d'architecture (faits
  et souvenirs les plus pertinents d'abord). C'est peu de code et ça ferme
  l'écart formel.
- **Propre** : répartir explicitement le budget (ex. 70 % historique / 30 %
  long terme) et documenter l'arbitrage. Plus lourd, à réserver si le besoin se
  précise.

Ajouter un test : avec un `token_budget` serré, `len(render()) // 4` reste
sous le budget même quand faits + épisodique sont fournis.

---

### [M2] Le repli SQLite en mémoire n'est pas thread-safe

**Constat.** `_get_local_session_factory()` met en cache un unique
`create_engine("sqlite://")`. Pour une base **`:memory:`**, SQLAlchemy emploie
par défaut un `SingletonThreadPool` : une connexion **par thread**. Or chaque
connexion `:memory:` ouvre une base **distincte et vide**.

**Pourquoi c'est important.** En mono-thread (CLI, pytest) tout va bien. Mais
dès qu'un serveur hors-ligne traite deux requêtes sur deux threads, la seconde
thread voit une base vide — sans même le schéma (`create_all` n'a tourné que sur
la première). Symptôme : `no such table: memory_facts` ou des faits qui
« disparaissent ». La prod réelle utilise `DB_URL`/Postgres, donc l'impact est
circonscrit, mais le repli est présenté comme un équivalent fonctionnel et ne
l'est pas sous charge concurrente.

**Comment corriger.** Forcer un pool partagé entre threads pour l'in-memory :

```python
from sqlalchemy.pool import StaticPool

engine = create_engine(
    "sqlite://",
    future=True,
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
```

`StaticPool` maintient **une seule** connexion réutilisée par tous les threads,
donc **une seule** base en mémoire — le comportement attendu. À documenter dans
la note 1c (le choix actuel « garder la connexion vivante » est correct en
intention mais incomplet sur la concurrence).

---

### [M3] État global mutable partagé entre tests, sans réinitialisation

**Constat.** L'engine SQLite est un singleton de module. Les tests mémoire
partagent donc le **même** magasin de faits sur toute la session pytest ; ils ne
s'isolent qu'en choisissant des `user_id` distincts (`acc-marc`, `facts-marc`,
`facts-marc-iso`…).

**Pourquoi c'est important.** C'est une source classique de tests
« fantômes » : un test qui oublie de préfixer son `user_id` verra les faits d'un
autre, et l'ordre d'exécution peut devenir signifiant. La discipline actuelle
tient, mais elle est implicite et fragile — un futur contributeur ne la devinera
pas.

**Comment corriger.** Fournir un point de réinitialisation et une fixture :

```python
# facts.py
def _reset_local_store_for_tests() -> None:
    global _local_session_factory
    _local_session_factory = None
```

```python
# conftest.py
@pytest.fixture(autouse=True)
def _fresh_facts_store():
    from velmo.memory import facts
    facts._reset_local_store_for_tests()
    yield
```

Chaque test repart alors d'un magasin vierge, et l'isolation ne dépend plus du
nommage des `user_id`.

---

### [M4] `forget` : sur-suppression et sémantique du compteur

**Constat.** `forget` supprime toute entrée dont la clé/le contenu **contient**
la sous-chaîne cible (insensible à la casse), sur les trois étages, et renvoie
la **somme** des suppressions.

**Pourquoi c'est important.** Deux effets :

1. **Sur-suppression** : `forget(user, "L")` (cible courte) ou une cible
   fréquente balaierait bien plus que voulu. Le droit à l'oubli doit être
   *ciblé* (`reco_expert.md`) ; une suppression trop large est une perte de
   données silencieuse.
2. **Compteur ambigu** : un tour écrit par `write()` existe à la fois dans
   `history` et dans l'épisodique ; `forget` le compte donc **deux fois**. La
   valeur de retour n'est pas « nombre de souvenirs oubliés » mais « nombre
   d'entrées supprimées, tous étages confondus » — ce qui peut tromper un
   appelant (et le test se contente de `>= 1` / `>= 2`, ce qui masque l'écart).

**Comment corriger.**

- Garder de préférence les correspondances sur **mots entiers** (comme
  `_tokens` en 1b) plutôt que sur sous-chaîne brute, ou imposer une longueur
  minimale de cible, pour limiter la casse.
- Clarifier le contrat de retour : soit documenter noir sur blanc « nombre
  d'entrées supprimées, étages cumulés », soit dédupliquer (compter une unité
  logique une seule fois). Un docstring précis suffit si l'on garde la
  sémantique actuelle.

---

### [L1] `LocalEpisodicStore` grossit sans borne

Assumé dans la note 1b (magasin « non tronqué » exprès, pour retrouver un vieux
souvenir). Correct sur le principe, mais aucune éviction : une session très
longue fait croître la RAM indéfiniment. **Correctif** : plafonner par un
nombre max de souvenirs par `user_id` (fenêtre glissante) ou par ancienneté, si
le besoin de longévité apparaît. Faible priorité tant que les sessions restent
courtes.

### [L2] Pas de dégradation gracieuse sur panne backend

Le choix Chroma/SQL vs repli se fait à la **construction** ; une panne au
**runtime** (Chroma injoignable, timeout SQL) remonte en exception jusqu'à
`respond()` et casse la réponse. **Correctif** : envelopper les appels
`search`/`add`/`all_for` d'un try/except qui journalise et dégrade (renvoyer un
contexte partiel plutôt que planter) — cohérent avec l'esprit « repli
hors-ligne » du projet.

### [L3] Faits injectés sans filtrage de pertinence

`read` récupère **tous** les faits du user (`all_for`), sans lien avec le
message courant. Pour quelques clés par client c'est sans conséquence ; à
grande échelle, cela gonfle le contexte et rejoint [M1]. **Correctif** : si le
nombre de faits croît, sélectionner les plus pertinents (par mot-clé du message,
ou par récence) plutôt que tout injecter.

### [L4] `render()` ne distingue pas rappel et tours récents

Les souvenirs épisodiques sont des chaînes `"user: …"` mêlées aux tours
d'historique, sans marqueur. Injecté tel quel, le LLM ne sait pas qu'un souvenir
est *rappelé* (potentiellement ancien) et non *dit à l'instant*. **Correctif** :
préfixer la section épisodique (ex. `"[souvenir] user: …"` ou un en-tête
« Éléments rappelés de conversations passées : »), pour ancrer correctement le
modèle — utile surtout une fois [H1] corrigé et le contexte réellement injecté.

---

## Ce qui est bien fait (à conserver)

- **Cohérence du patron** « backend réel + repli hors-ligne » sur les trois
  étages, calqué sur `kb_store.py`/`llm.py` — lisible et prévisible.
- **Isolation par `user_id`** effective à chaque étage (clé de dict, filtre
  SQL `WHERE user_id`, `where={"user_id": ...}` Chroma) : R3 tenue par
  construction, la faute la plus grave est structurellement évitée.
- **Choix assumés et tracés** dans les notes de cours (heuristique 4 car/token,
  `k=3` hors budget, cache module SQLite) : la dette est *documentée*, pas
  cachée — exactement ce que demande le brief.
- **`forget` sur les trois étages** : le réflexe de purger aussi le court terme
  et l'épisodique (pas seulement les faits) est correct et bien argumenté.
- **Tests complémentaires ciblés** par étape, distincts du contrat d'acceptance,
  couvrant rappel, isolation, troncature, persistance et oubli.

---

## Suggestion de priorisation

| # | Remarque | Effort | Exigence touchée |
|---|----------|--------|------------------|
| H1 | Brancher `read()` sur la génération | Faible | R1, R2 (effet réel) |
| H3 | `all_for` Chroma → vrai `get()` | Faible | R6 |
| M2 | `StaticPool` pour SQLite in-memory | Très faible | Robustesse |
| H2 | Classification à l'écriture | Moyen | R2 (alimentation) |
| M1 | Budget global au rendu | Moyen | R4 |
| M3 | Fixture de reset des tests | Faible | Qualité tests |
| M4 | Cibler `forget` / clarifier le compteur | Faible | R5 |
| L1–L4 | Finitions | Variable | Robustesse/qualité |

Les deux gains les plus rentables sont **H1** et **H3** : peu de code, ils font
passer la mémoire de « correcte en théorie » à « effective en production ».
