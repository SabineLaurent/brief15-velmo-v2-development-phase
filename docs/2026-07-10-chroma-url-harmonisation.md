# Chroma — une seule adresse, réellement lue : `CHROMA_URL`

_Note pédagogique, 2026-07-10. Fichiers concernés : `src/velmo/config.py`,
`src/velmo/kb_store.py`, `src/velmo/memory/retrieval.py`, `scripts/seed_kb.py`._

> Tout est parti d'une erreur banale : `make seed-kb` refusait de démarrer
> (`No module named 'chromadb'`). En tirant le fil, on a découvert que Velmo
> parlait à Chroma de **trois façons différentes et incohérentes** — dont une qui
> cachait un vrai bug. Cette note raconte le diagnostic, puis la mise au propre :
> une convention unique, `CHROMA_URL`, effectivement analysée. Série mémoire :
> voir le [palier 3 épisodique](2026-07-07-memoire-palier3-episodique.md), qui est
> l'un des consommateurs de Chroma concernés.

---

## 1. Le déclencheur : une dépendance optionnelle absente

`chromadb` ne fait pas partie du socle : il vit dans l'extra `vector` de
`pyproject.toml`. Or `make install` lance `uv sync`, qui **n'installe pas les
extras**. Le cœur de Velmo tourne exprès sans Chroma (repli `LocalKB`, FAQ
TF-IDF), mais `scripts/seed_kb.py` a pour seul rôle de *pousser* la FAQ dans
Chroma : lui, il en a réellement besoin.

```bash
uv sync --extra vector      # installe chromadb + sentence-transformers
```

La leçon d'archi : **un repli hors-ligne bien conçu rend une dépendance
optionnelle invisible… jusqu'au jour où on emprunte le chemin qui l'exige.**
L'erreur n'était pas un bug, c'était le système qui disait « tu m'as demandé le
chemin Chroma sans installer Chroma ».

## 2. La vraie surprise : trois conventions pour une seule adresse

En vérifiant *quelle* variable d'environnement le seed attendait, on a trouvé un
désaccord à trois voix — et une seule des trois faisait ce qu'elle prétendait :

| Endroit | Ce qu'il lisait | Ce qu'il en faisait |
|---------|-----------------|---------------------|
| `kb_store.py` | présence de `CHROMA_URL` | **ignorait la valeur**, codait en dur `chroma:8000` |
| `retrieval.py` | présence de `CHROMA_URL` | idem, `chroma:8000` en dur |
| `seed_kb.py` | `CHROMA_HOST` / `CHROMA_PORT` | construisait l'adresse |

Le piège est subtil : l'app testait `if not os.getenv("CHROMA_URL")` — on *croit*
lire une URL. En réalité `CHROMA_URL` ne servait que d'**interrupteur on/off**, et
le host/port étaient figés dans le code juste en dessous.

```python
if not os.getenv("CHROMA_URL"):        # ← simple présence : allumé ou éteint
    return LocalKB()
...
client = chromadb.HttpClient(host="chroma", port=8000)   # ← valeur jamais lue
```

## 3. Le bug caché derrière le nommage

Cette valeur figée n'est pas qu'inélégante : c'est un **bug latent**. `chroma`
est le nom du service Docker Compose — il ne résout **que** dans le réseau Docker.
Conséquence : l'app était **incapable** de joindre un Chroma exposé sur la
machine hôte (`localhost:8001`, cf. le mapping `"8001:8000"` du compose), quelle
que soit la configuration. On pouvait poser n'importe quel `CHROMA_URL`, il était
lu comme un booléen et jeté.

Analogie : on avait une **enveloppe avec une adresse écrite dessus**, mais le
facteur regardait seulement *si* l'enveloppe existait, puis livrait toujours à la
même adresse gravée dans sa mémoire.

## 4. La mise au propre : lire l'adresse, une fois, au bon endroit

`config.py` centralise déjà les sélecteurs de backend. On y ajoute un seul
helper, qui **analyse vraiment** l'URL et renvoie `None` si elle est absente :

```python
def chroma_host_port() -> tuple[str, int] | None:
    """(host, port) depuis `CHROMA_URL` ; `None` si la variable est absente."""
    url = os.getenv("CHROMA_URL")
    if not url:
        return None
    parsed = urlparse(url)
    return parsed.hostname or "chroma", parsed.port or 8000
```

Les deux sites applicatifs remplacent alors le flag + host figé par la valeur
lue — le `None` conserve exactement l'ancienne sémantique (variable absente →
repli hors-ligne) :

```python
endpoint = chroma_host_port()
if endpoint is None:
    warn_backend_unavailable("FAQ Chroma", "CHROMA_URL absent")
    return LocalKB()
...
client = chromadb.HttpClient(host=endpoint[0], port=endpoint[1])
```

Le script de seed s'aligne sur la même source, mais avec une nuance : il n'a
**pas** de repli hors-ligne (sans Chroma, il n'a rien à faire). On lui laisse donc
un défaut Docker, pour que `make seed-kb` marche sans configuration dans le
conteneur :

```python
host, port = chroma_host_port() or ("chroma", 8000)
```

Pourquoi cette asymétrie est saine : l'app et le seed ont des **besoins
différents** face à l'absence de config. L'app doit dégrader gracieusement
(offline) ; le seed, lui, vise un but précis et prend le défaut le plus probable
(Docker). Même variable lue, réactions adaptées.

## 5. La preuve

Le critère de succès : le même `CHROMA_URL` doit produire la bonne adresse dans
les scénarios Docker **et** hôte. Vérifié en direct :

| `CHROMA_URL` | Sortie de `chroma_host_port()` | Ce que ça démontre |
|--------------|-------------------------------|--------------------|
| _(absente)_ | `None` | l'app retombe offline, le seed prend son défaut |
| `http://chroma:8000` | `('chroma', 8000)` | le flux Docker reste identique |
| `http://localhost:8001` | `('localhost', 8001)` | **le bug est corrigé** : l'hôte est joignable |
| `http://chroma` | `('chroma', 8000)` | port par défaut robuste |

Plus besoin de jongler avec `CHROMA_HOST`/`CHROMA_PORT` : ces variables
disparaissent du vocabulaire du projet.

## 6. La cause racine du second échec : un `.env` jamais lu

Après avoir installé `chromadb`, `make seed-kb` échouait encore — cette fois sur
un `[Errno 8] nodename nor servname` : le nom `chroma` ne résolvait pas. Or le
`.env` contenait `CHROMA_URL=http://localhost:8001` **depuis le début**. Le
problème n'était donc pas la valeur, mais que **rien ne chargeait `.env` sur
l'hôte** : `load_dotenv()` n'était appelé que dans `cli.py`. `uv run` ne lit pas
`.env` tout seul ; le script de seed voyait donc un environnement vide, retombait
sur son défaut `chroma:8000`, injoignable hors Docker.

Correctif d'une ligne, calqué sur `cli.py` :

```python
load_dotenv()   # en tête de main() : le seed lit enfin le .env de l'hôte
```

La leçon : **un fichier `.env` n'est pas magique — quelqu'un doit le charger.**
En Docker, c'est `env_file:` du compose ; sur l'hôte, c'est `load_dotenv()`. Un
process qui n'appelle ni l'un ni l'autre ne voit rien, sans erreur.

## 7. Le piège RAG : la troncature silencieuse

Question soulevée en revue : et si on indexait plus tard un document plus gros ?
Réponse : **erreur silencieuse garantie.** Le modèle d'embedding a une fenêtre de
`max_seq_length = 512` tokens. Au-delà, `sentence-transformers` **coupe la fin du
texte avant de le vectoriser** — sans exception, sans log. Pire, Chroma stocke le
texte *entier* : d'où un désaccord entre ce qui est **stocké** (tout) et ce qui
est **cherchable** (les 512 premiers tokens seulement).

Démonstration faite en direct : deux documents identiques sauf leur phrase finale
(« remboursement plafonné à 50 € » vs « illimité »), tous deux au-delà de 512
tokens, produisent des embeddings de **similarité cosinus 1.0000** — la queue,
pourtant contradictoire, a été purement ignorée. Une clause enfouie en fin de long
document serait ainsi **invisible à la recherche**, et une fiche remontée
afficherait un texte que le modèle n'a jamais « lu ».

Le danger est **latent** : les 16 fiches font ≤ 90 tokens, on est loin du plafond.
La bonne réponse n'est donc pas d'implémenter le chunking maintenant (complexité
spéculative), mais de **rendre le futur échec bruyant**. Garde-fou ajouté à
l'ingestion :

```python
model = SentenceTransformer(model_name)
token_limit = model.max_seq_length
for path in sorted(KB_DOCS_DIR.glob("*.md")):
    text = path.read_text(encoding="utf-8")
    n_tokens = len(model.tokenizer.encode(text))
    if n_tokens > token_limit:
        raise SystemExit(
            f"{path.name} : {n_tokens} tokens > fenêtre {token_limit} … à découper."
        )
```

Vérifié dans les deux sens : la FAQ actuelle passe (16 docs), une fiche piégée de
2006 tokens **arrête `make seed-kb` net**. Le principe : *fail loud, fail early* —
peu de code, mais il rend visible aujourd'hui un piège qui, sinon, se paierait en
silence en production.

## 8. Ce qui reste (volontairement)

- **Les 7 erreurs `mypy` du fichier `kb_store.py`/`retrieval.py`** (fonctions non
  annotées, stubs `chromadb` manquants) sont **préexistantes** et hors sujet :
  elles n'apparaissent que parce que `chromadb` est maintenant installé
  localement, et la CI ne lance de toute façon pas `mypy`. On n'y a pas touché —
  ce serait un autre chantier, à mener délibérément et non en passant.
- **Le nom `chroma` figé pour l'embedder** (`EMBEDDING_MODEL`) et le nom de
  collection ne sont pas concernés : seul le point de connexion réseau était en
  cause.
- **`CHROMA_URL` ne porte pas encore de schéma d'authentification** (token,
  tenant). Chroma 0.5 en local n'en a pas besoin ; le jour où un Chroma managé
  l'exige, `chroma_host_port()` est l'endroit unique où l'enrichir.
- **Le chunking n'est pas implémenté** — le garde-fou (§7) *détecte* un document
  trop long et refuse d'indexer, il ne le *découpe* pas. Le jour où une fiche
  légitime dépasse la fenêtre, il faudra insérer une étape de découpage entre
  `read_text()` et `upsert()` (par titre `##`, ou fenêtre glissante à recouvrement).
