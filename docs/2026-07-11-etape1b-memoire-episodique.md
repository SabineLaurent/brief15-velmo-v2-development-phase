# 2026-07-11 — Étape 1b : mémoire long terme épisodique

_Note de cours, niveau débutant Python / agents IA._

## Le problème que le court terme laissait ouvert

L'étape 1a a donné à `MemoryManager` un tampon en RAM par utilisateur, borné
par `token_budget` : les tours les plus anciens sont jetés quand la fenêtre
déborde (`_trim_to_budget`, `src/velmo/memory/__init__.py`). Très bien pour
« de quoi parle-t-on *là* ? », mais ça a une conséquence directe : si un
client cite sa commande au tour 1 d'une longue conversation, et que le tour 1
sort du tampon au tour 20, l'agent l'a **perdu**, même si le client demande
« rappelle-moi ma commande » juste après.

C'est exactement le trou que `docs/architecture-agent-support.md` assigne à
l'étage **long terme épisodique** : une base interrogée « par similarité »,
indépendante de la fenêtre court terme, qui répond à « m'a-t-il déjà parlé de
X ? » même quand X est sorti du tampon depuis longtemps.

## Le patron déjà en place ailleurs dans le code

Le projet a déjà résolu ce problème générique deux fois : **un vrai backend
externe optionnel, avec un repli hors-ligne déterministe**.

- `kb_store.py` : `ChromaKB` (recherche sémantique) si `CHROMA_URL` est
  configuré, sinon `LocalKB` (TF-IDF léger sur les fichiers `kb/docs/*.md`).
- `llm.py` : `AzureLLM` si `AZURE_AI_INFERENCE_ENDPOINT` est configuré, sinon
  `EchoLLM`.

`src/velmo/memory/episodic.py` applique le même patron à la mémoire :

```python
def get_episodic_store() -> EpisodicStore:
    if not os.getenv("CHROMA_URL"):
        return LocalEpisodicStore()
    try:
        import chromadb
        ...
    except ImportError:
        return LocalEpisodicStore()
    ...
    return ChromaEpisodicStore(collection)
```

L'import de `chromadb` est différé (à l'intérieur de la fonction) : le
module se charge sans erreur même si la librairie n'est pas installée — ce
qui est le cas dans cet environnement de dev, d'où les deux erreurs mypy
« Cannot find implementation for chromadb » qu'on assume (elles existent
déjà, à l'identique, pour `kb_store.py`).

`EpisodicStore` est un `Protocol` (comme `LLM` dans `llm.py`) : il décrit
juste la forme attendue (`add`, `search`, `all_for`) sans imposer d'héritage.
`LocalEpisodicStore` et `ChromaEpisodicStore` la respectent chacune à leur
façon — c'est du duck typing explicite.

## Le repli local : recherche par recouvrement de tokens

Pas d'embeddings hors-ligne (ce serait une dépendance lourde pour un besoin
simple). `LocalEpisodicStore` garde un magasin en RAM **non tronqué** par
utilisateur (contrairement au tampon court terme) et note, pour une requête,
combien de mots significatifs elle partage avec chaque souvenir stocké :

```python
def _tokens(text: str) -> set[str]:
    return {t for t in re.findall(r"[a-z0-9]+", text.lower()) if len(t) > 2}
```

Exemple : le souvenir `"user: Ma commande prioritaire est O-2024-0101."`
donne les tokens `{ma, commande, prioritaire, est, 2024, 0101}` (les mots de
2 lettres ou moins sont ignorés). La question `"Quelle était ma commande
prioritaire ?"` donne `{quelle, etait, commande, prioritaire}` — 2 tokens en
commun (`commande`, `prioritaire`) → score 2. Les 3 souvenirs au score le
plus élevé sont renvoyés (`k=3` par défaut).

C'est volontairement grossier (pas de pondération par rareté du terme, à la
différence du TF-IDF de `LocalKB` — la mémoire conversationnelle n'a pas
besoin de cette finesse pour ce cas d'usage), mais suffisant pour retrouver
un souvenir sorti du tampon court terme.

## Câblage dans `MemoryManager`

- `write()` indexe chaque échange dans l'épisodique en plus du tampon court
  terme : `self._episodic.add(user_id, f"user: {user_message}")` et pareil
  pour la réponse.
- `read()` interroge l'épisodique avec le message courant, et **exclut** les
  souvenirs déjà présents dans `history` (comparaison de chaînes exactes) —
  sinon un souvenir encore dans la fenêtre court terme serait dupliqué dans
  le rendu.

```python
def read(self, user_id, message):
    history = list(self._history.get(user_id, []))
    already_present = {f"{role}: {content}" for role, content in history}
    hits = self._episodic.search(user_id, message, k=_EPISODIC_K)
    episodic = [hit for hit in hits if hit not in already_present]
    return MemoryContext(history=history, episodic=episodic)
```

## Une simplification assumée : `k=3` fixe, hors budget formel

L'étape 1a borne le tampon court terme par `token_budget`. On aurait pu
étendre ce calcul pour compter aussi les résultats épisodiques dans le même
budget global — c'est ce que suggère la note d'architecture (« on fusionne +
on tronque au budget de tokens »). On ne l'a pas fait : ça aurait obligé à
repartager un budget déjà entièrement consommé par le court terme entre deux
étages, avec un arbitrage (quelle proportion pour chacun ?) qui n'a pas de
réponse évidente et qui aurait retouché une logique déjà validée à l'étape
1a. À la place, l'épisodique est borné par un simple `k=3` : au plus 3
souvenirs injectés, une taille intrinsèquement petite qui ne fait pas
exploser le contexte en pratique. Si un budget combiné strict s'avère
nécessaire plus tard, c'est un point de retouche identifié, pas un oubli.

## Effet de bord sur un test existant

`tests/test_memory_short_term.py::test_token_budget_trims_oldest_turns`
vérifiait auparavant que le texte rogné du tampon court terme n'apparaissait
plus **dans le rendu complet**. Avec l'épisodique, c'est désormais faux par
construction : un ancien tour peut légitimement resurgir via `.episodic`
si la question s'y rapporte — c'est tout l'intérêt de cette étape. Le test a
été ajusté pour vérifier `.history` spécifiquement (le tampon court terme
seul), qui reste, lui, correctement rogné.

## Ce qui reste hors périmètre

- **Faits durables structurés** (`remember_fact`) : toujours no-op.
- **Droit à l'oubli** (`forget`) : toujours no-op — et devra, une fois
  construit, purger *aussi* l'épisodique (pas seulement les faits), sans
  quoi une commande « oublie mon adresse » laisserait le souvenir
  retrouvable par similarité. Noté pour l'étape suivante.
- **Persistance multi-session réelle** : le repli local (`LocalEpisodicStore`)
  vit en RAM, à l'échelle du process — comme le tampon court terme. Le vrai
  cross-session ne peut venir que de `CHROMA_URL` configuré (backend Chroma
  réel, non testé hors-ligne) ou de la persistance des faits (Postgres,
  étape suivante).

## État de la suite de tests

`tests/acceptance/test_memory.py` : `test_recall_over_30_turns` reste vert
(porté maintenant par l'épisodique en plus du court terme — plus robuste
qu'à l'étape 1a, où ça tenait par chance sous le budget par défaut).
`test_cross_session_persistence`, `test_isolation_between_customers` (via
`remember_fact`) et `test_right_to_be_forgotten` restent rouges, comme
attendu : ce sont les étapes suivantes (faits durables, droit à l'oubli).
