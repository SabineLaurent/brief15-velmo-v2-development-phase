# Architecture

Conception technique de l'agent. Pour le besoin métier, voir [`spec.md`](spec.md).

## 1. Vue d'ensemble

Trois processus, deux coutures. Le client ne connaît qu'une URL ; l'agent ne
connaît qu'un `BaseChatModel` et un port métier.

```
┌──────────┐   HTTP/SSE   ┌──────────────┐          ┌──────────────────────┐
│  client  │─────────────►│  server.py   │          │  Provider LLM        │
│ Chainlit │              │  POST /chat  │          │  (choisi par .env)   │
└──────────┘              └──────┬───────┘          └──────────▲───────────┘
                                 │ stream_reply()              │
                          ┌──────▼───────────────────────┐     │
                          │  api.py — la couture         │     │
                          └──────┬───────────────────────┘     │
                          ┌──────▼───────────────────────┐     │
                          │  graph/ — StateGraph         │─────┘
                          └──┬────────┬────────┬─────────┘
                             │        │        │
                    ┌────────▼──┐ ┌───▼────┐ ┌─▼──────────┐
                    │ memory/   │ │ know…/ │ │ actions/   │
                    │ CT + LT   │ │ FAQ    │ │ port métier│
                    └────┬──────┘ └───┬────┘ └─────┬──────┘
                         │            │            │
                  checkpointer   vector store   SI marchand
                  + store        (embeddings)   (doublure SQL en dev)
```

Chaque bloc correspond à un sous-package de `packages/support-agent/src/support_agent/`.

## 2. La couche LLM agnostique

Le code applicatif ne connaît qu'une interface abstraite, `BaseChatModel`. Le
choix du fournisseur est une donnée de **configuration**.

```
.env : LLM_PROVIDER  ──►  llm/factory.py : get_chat_model()  ──►  BaseChatModel
```

`llm/factory.py` est le **seul** endroit du projet qui instancie un fournisseur.
Deux chemins :

- **fournisseur standard** (`mistral`, `groq`, `google_genai`, `azure_ai`) —
  délégué à `init_chat_model("provider:model")`, le sélecteur natif de LangChain ;
- **API compatible OpenAI** — `ChatOpenAI(base_url=…)`, qui couvre la plupart des
  API tierces ou internes.

Trois variantes sont exposées : `get_chat_model()`, `get_fast_chat_model()` (un
modèle rapide pour le routage, qui domine le temps de première réponse) et
`get_chat_model_fallbacks()` (repli si le fournisseur principal échoue).

**Un fournisseur = un extra** de packaging (`uv sync --extra groq`) : une
installation ne paie pas les SDK qu'elle n'utilise pas. La correspondance entre
les alias acceptés dans `.env` et les extras déclarés est vérifiée par un test —
un alias sans extra serait une valeur de configuration qui échoue à l'usage.

Même principe pour les embeddings (`llm/embeddings.py`), utilisés par la FAQ et
par la mémoire long terme.

## 3. Le graphe

L'agent est un `StateGraph` explicite — nœuds nommés, arêtes conditionnelles —
plutôt qu'un agent préfabriqué. Le comportement est donc lisible, traçable et
testable nœud par nœud.

```
START ─► guard_input ─┬─(bloqué)───────────────────────────────► END
                      ├─(dossier chez un humain)─► human_takeover ─┐
                      └─► compact ─► router ─┬─(answer)─► answer ──┤
                                             ├─(support)─► model ⇄ tools ─┼─► guard_output ─► close_turn ─► END
                                             └─(escalate)► escalate ──────┘
```

| Nœud | Rôle |
|---|---|
| `guard_input` | Filtre le message entrant (injection, contenus modérés) |
| `human_takeover` | Répond sans appel LLM quand un humain a repris le dossier |
| `compact` | Borne la fenêtre de contexte avant que les nœuds LLM lisent l'historique |
| `router` | Classe l'intention (sortie structurée, modèle rapide) |
| `answer` | Réponse directe pour les échanges qui n'appellent aucun outil |
| `model` ⇄ `tools` | Boucle ReAct : FAQ, mémoire, actions métier |
| `escalate` | Ouvre un ticket, coupe le bot, termine le tour |
| `guard_output` | Caviarde la réponse sortante (PII, secrets, fuite de prompt) |
| `close_turn` | Dépose un candidat d'apprentissage épisodique (un upsert, zéro appel LLM) |

`escalate` **ne met pas le graphe en pause** : il n'appelle pas `interrupt()`. Le
raisonnement et les alternatives écartées sont dans [`escalade.md`](escalade.md).

## 4. La mémoire

Deux horizons, et dans le long terme deux types cognitifs.

| Mémoire | Question | Support | Clé |
|---|---|---|---|
| Court terme | « Qu'est-ce qu'on s'est dit dans ce fil ? » | checkpointer | `thread_id` |
| Long terme — sémantique | « Que sais-je de ce client ? » | store + recherche vectorielle | `("memories", user_id)` |
| Long terme — épisodique | « Un cas ressemblant a-t-il été bien traité ? » | store | `("episodes",)`, partagé |

Le sémantique est **cloisonné par client** ; l'épisodique est **partagé**, donc
anonymisé à l'écriture. Au-delà d'un seuil de messages, le nœud `compact`
condense l'historique ancien et le retire de l'état, les faits durables ayant
été sauvegardés en mémoire longue. Détail complet, garde-fous et mesures :
[`memoire.md`](memoire.md).

## 5. La base de connaissance (RAG)

```
documents FAQ ─► découpage ─► embeddings ─► vector store
                                                 │
question ─► embedding ─► recherche par similarité ◄┘ ─► chunks + question ─► LLM
```

L'index est un `InMemoryVectorStore` **reconstruit à chaque démarrage**
(`knowledge/ingest.py`) : aucune dépendance externe, au prix de quelques
secondes au lancement. Passer à un store persistant (pgvector, Chroma, Azure AI
Search) est un changement d'un seul fichier. En production, l'ingestion cesse
d'être faite par l'application et devient un job découplé.

La FAQ est exposée au modèle comme un **outil**, pas injectée systématiquement :
c'est le modèle qui décide d'interroger la base et qui formule sa requête.

## 6. Les garde-fous

Trois points de contrôle, tous derrière le kill switch `GUARDRAILS_ENABLED` :

- **entrée** (`guard_input`) — injection de prompt, catégories modérées ;
- **outils** (`tool_guard`) — validation des champs, nettoyage avant persistance ;
- **sortie** (`guard_output`) — caviardage des PII et secrets, remplacement d'une
  réponse qui recopie le prompt système.

Le garde de sortie est un nœud **postérieur** aux nœuds LLM. C'est ce qui
interdit le streaming token par token : on ne peut pas afficher un texte que le
garde n'a pas encore validé. Voir [`streaming.md`](streaming.md).

## 7. Le port métier

`actions/` définit un **port** (`SupportBackend`) que l'agent consomme sans
connaître son implémentation. Deux adaptateurs, choisis par `SUPPORT_BACKEND` :
un jeu de données en mémoire, et une boutique SQLite peuplée par `make seed`. En
production, l'adaptateur appelle le SI du marchand ; la base de démonstration est
débranchée, pas migrée. Voir [`../database/README.md`](../database/README.md).

## 8. Évaluation

Trois corpus de cas (mémoire, garde-fous, qualité) sont à la fois **assertés**
par des tests et **notés** par `make score`, qui produit un rapport et bloque
sous un seuil. Les dimensions déterministes tournent hors ligne et sont gardées
par la CI ; la dimension qualité demande un appel LLM et reste locale. Le corpus
mémoire est noté **une fois par moteur de persistance** — l'isolation entre
clients dépend du store, pas seulement de notre code.

Le tracing LangSmith (`LANGSMITH_*`) instrumente chaque exécution.

## 9. La couture et l'exposition

`api.py` expose une fonction unique :

```python
stream_reply(message, *, user_id, thread_id) -> AsyncIterator[str]
```

Elle **cache entièrement LangGraph** : aucun type `lang*` ne franchit cette
frontière. Elle livre l'état terminal du graphe, donc la réponse déjà passée par
`guard_output`, et garantit **un chunk non vide sur tout chemin** — réponse
normale, entrée bloquée, panne.

`server.py` la transporte en SSE (`POST /chat`, plus `/health` et `/ready`) sans
contenir la moindre logique d'agent. Le client Chainlit réimplémente la même
signature au-dessus du réseau, ce qui lui permet d'ignorer qu'il y a un HTTP au
milieu.

Deux identités à ne pas confondre : `API_KEY` autorise **l'appelant**, `user_id`
désigne **le client**.

## 10. Persistance

`PERSISTENCE_BACKEND` choisit `memory`, `sqlite` ou `postgres` sans toucher au
code de l'agent. En Postgres : une seule base, les deux horizons de mémoire
séparés par schéma, pgvector dans cette même base, pool de connexions partagé et
balayage de rétention (`MEMORY_TTL_DAYS`, compté depuis le dernier accès).

## 11. Structure du dépôt

Mono-repo **uv workspace** : chaque package a son `pyproject.toml`, tous
partagent un unique `uv.lock` et un unique `.venv`.

```
├── pyproject.toml          # [tool.uv.workspace] members = ["packages/*"]
├── uv.lock                 # un lockfile partagé
├── Makefile                # porte d'entrée unique
├── data/                   # sources versionnées : FAQ + corpus d'évaluation
├── database/               # état runtime, non versionné
└── packages/
    ├── support-agent/      # l'agent + son API HTTP
    └── client/             # client de démonstration Chainlit
```

`packages/client` **ne dépend pas** de `support-agent` : il l'appelle en HTTP. Le
découplage est un fait du graphe de dépendances, vérifié par le build de l'image
du client, et non une règle de discipline.

Le lockfile mutualisé n'impose aucune topologie de déploiement : un lockfile
produit N images indépendantes.

## 12. Décisions techniques

| Sujet | Choix | Raison |
|---|---|---|
| Langage | Python 3.12 | Compatibilité de l'écosystème `lang*` |
| Gestion de projet | `uv` | Lockfile reproductible, workspace natif |
| Configuration | Pydantic Settings | Typée, validée, lue depuis `.env` |
| Orchestration | LangGraph `StateGraph` | État, persistance et reprise natifs |
| Abstraction LLM | `BaseChatModel` + factory | Le fournisseur devient une variable |
| Conteneurs | Multi-stage, non-root | Image sans sources ni secrets |
