# Plan de déploiement — conteneuriser l'agent, du local à Azure

**Objet :** faire de l'agent un **service indépendant, appelable**, empaqueté en
conteneur — vérifié d'abord en local en conditions quasi réelles, déployé *in fine*
sur **Azure**.
**Date :** 2026-07-25
**Statut :** plan retenu. **Étapes 1 à 4 faites** (service HTTP + image Docker +
Postgres/pgvector + client Chainlit en HTTP, vérifiées en live) ; **reste l'étape 5**
(Azure + identité prouvée) — l'avancement se suit dans
[`../TODO_priorities.md`](../TODO_priorities.md).
Ce document décrit la cible et le chemin, pas l'état du code (pour ça :
[`architecture.md`](architecture.md)).
**Portée :** exposition HTTP, images Docker, orchestration locale, hébergement Azure.
Ne traite pas les features métier de l'agent (voir [`../ROADMAP.md`](../ROADMAP.md)).
**Se lit avec :** [`architecture-cible-2026-07-25.md`](architecture-cible-2026-07-25.md)
(les blocs et les 5 invariants) — ce plan-ci en est la **mise en œuvre**.

> 📖 Tous les acronymes employés ici sont définis en **§8**, à la fin.

---

## 1. Le but en une phrase

Un agent **appelable par n'importe qui** (HTTP), qui tourne dans son propre
conteneur, dont l'état survit à un redémarrage, et qui se déploie sur Azure **sans
que l'image change** entre ma machine et le cloud.

---

## 2. Pourquoi passer par Docker en local d'abord

Ce n'est pas une étape de cérémonie. Lancer la pile en local avec Docker Compose
teste **cinq choses qu'aucun test unitaire n'atteint** :

| Ce qui est testé | Pourquoi ça ne se teste pas autrement |
|---|---|
| **La config par environnement** | Plus de `.env`, plus de `load_dotenv()` pour sauver la mise. C'est le vrai examen de l'agnosticisme : si l'agent démarre avec des variables injectées, « changer de provider = une variable » est **prouvé**. |
| **Les chemins** | `./data/kb-velmo` est relatif au **cwd** : il casse ou il tient, on le sait en 10 secondes. |
| **La frontière réseau** | L'invariant n°3 cesse d'être une règle de documentation : le client **ne peut plus** importer `stream_reply`, il n'est pas dans le même process. |
| **Postgres + pgvector réellement exercé** | Était l'inconnue n°1 du plan (annoncé dans `config.py`, jamais testé). **Levée à l'étape 3** — voir son bilan. |
| **Le démarrage à froid** | Ordre de boot, healthcheck, l'agent qui attend que la base soit prête. Le bug classique du premier déploiement. |

### Ce que le local ne prouve PAS (à savoir d'avance)

- **TLS**, **DNS**, ingress — Azure les fournit, donc ils n'auront pas été répétés.
- Un vrai fournisseur d'identité (**IdP**).
- Le comportement **à N répliques** : à une seule réplique, l'indexation au boot et
  le rate-limiter in-process paraissent sains alors qu'ils ne le sont pas.
- Les particularités du Postgres **managé** : **SSL** obligatoire, `pgvector` à
  activer explicitement, pool de connexions limité.
- La gestion des secrets (fichier local ≠ coffre cloud).

---

## 3. L'écart constaté dans le code aujourd'hui

Relevé le 2026-07-25 sur `config.py` et les manifestes. Trois points seulement sont
du travail réel (🔴) :

| Constat | Impact en conteneur |
|---|---|
| **Aucun endpoint HTTP** — l'agent est un **CLI** + un import Python | 🔴 le vrai travail |
| `persistence_backend = "memory"` par défaut, sinon SQLite en fichier | 🔴 un conteneur qui redémarre perd tout (système de fichiers éphémère). **Refermé à l'étape 3** : l'état vit dans Postgres |
| `user_id` non signé (trou §5.1 de l'archi cible) | 🔴 sur Internet, c'est une faille, plus une note de doc |
| Chemins relatifs au **cwd** (`./data/kb-velmo`, `./database/…`) | 🟠 tient si on maîtrise le `WORKDIR`, mais fragile → chemins absolus par variable d'env |
| `load_dotenv()` + `env_file=".env"` | 🟢 **non-problème** : Pydantic Settings lit l'environnement en priorité. Le `.env` ne doit simplement jamais entrer dans l'image |
| L'app indexe la FAQ au boot (**2,9 s mesurés** + 2 appels embeddings) | 🟠 tolérable à 1 réplique ; le healthcheck doit attendre la fin |
| Un seul `.venv` de workspace | 🟠 l'image doit builder **une tranche**, pas tout le monorepo (§4) |

Le reste — factory LLM, ports, couture `stream_reply` — est déjà taillé pour ça.
C'est ce qui rend l'opération courte.

---

## 4. Ce que le workspace `uv` apporte au build Docker

C'était l'une des raisons du choix `uv` workspace, et la doc `uv` documente
**explicitement** ce cas. Trois gains concrets :

1. **Un seul `uv.lock`** → `--frozen` / `--locked` : le build ne résout aucune
   dépendance, l'image contient **exactement** ce qui a été testé en local.
2. **`--package support-agent`** → l'image n'embarque **que la tranche** utile :
   ni Chainlit, ni les dépendances du client.
3. **`--no-install-workspace`** → la couche des dépendances lourdes (langchain,
   langgraph) est mise en cache **séparément** du code applicatif. Modifier un nœud
   du graphe fait un rebuild de quelques secondes, pas de plusieurs minutes.

```dockerfile
FROM python:3.12-slim
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/
WORKDIR /app
# 1) les dépendances seules — couche invalidée uniquement si uv.lock change
RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    uv sync --frozen --no-install-workspace
# 2) puis le code de la tranche
COPY packages/support-agent packages/support-agent
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --package support-agent
```

Autres exigences de l'image, côté sérieux : utilisateur **non-root**, build
**multi-stage** (ni `uv` ni cache dans l'image finale), `.dockerignore` excluant
`.env`, `database/` et `.venv`.

---

## 5. Le rail lang\* de déploiement : écarté, et pourquoi

LangGraph propose son propre rail (**CLI** `langgraph`, `langgraph.json`,
`langgraph dev` en local, LangSmith Deployment en production). Vérifié via Context7
le 2026-07-25, il est écarté pour trois raisons :

1. **Son contrat public est le graphe.** `POST /runs/stream` prend un `assistant_id`
   et un `stream_mode` (ex. `messages-tuple`) : *le client choisit comment le graphe
   streame*. C'est exactement le streaming token depuis `answer` que le commit
   `519f254` a interdit — offert cette fois par une API qu'on ne contrôle pas. Ça
   casse l'invariant n°3 et rouvre le finding A1.
2. **Il amène sa propre gestion des threads et sa persistance**, en doublon de notre
   checkpointer/store, contre l'invariant n°1 (une seule base pour ce qu'on possède).
3. **Il exige une clé LangSmith, même en local**, et l'API déployée s'authentifie
   avec `X-Api-Key: <LANGSMITH_API_KEY>`. L'authentification de *notre* service
   serait celle de LangSmith, et l'agent ne serait pas appelable librement.

> Le lock-in lang\* qu'on assume porte sur l'**orchestration** et l'**observabilité**
> — pas sur la façon dont l'agent est publié au monde.

**Retenu à la place :** **FastAPI** + Uvicorn, en **dépendance optionnelle**
(`[project.optional-dependencies] server = [...]`) pour que le CLI et Chainlit ne
traînent pas un serveur web. Ce qu'on perd, honnêtement : Studio (débogage visuel),
la gestion gratuite des runs/crons, la reprise après interruption. Rien n'empêche de
garder un `langgraph.json` comme **outil de dev** pour brancher Studio en local,
sans en faire le contrat exposé.

---

## 6. Le plan — 5 étapes, chacune avec son but

Une étape = un livrable vérifiable. On n'en ouvre **qu'une à la fois**.

### Étape 1 — Le service HTTP

- **But technique :** donner à l'agent une porte d'entrée réseau. `POST /chat` en
  **SSE**, `GET /health`, `GET /ready`, dans `support_agent/server.py` (scope A —
  l'exposition appartient au cerveau, cf. [`vision.md`](vision.md) §3).
- **But pédagogique :** comprendre la différence entre *produire* un flux (l'agent),
  le *transporter* (l'API) et l'*afficher* (le front) — cf. [`streaming.md`](streaming.md).
- **Décision associée :** une **clé d'API de service** dès maintenant (un header
  vérifié). Pas pour la sécurité fine, mais parce qu'un endpoint public branché sur
  une clé LLM se fait vider son crédit en une nuit.
- **Vérification :** `curl` obtient une réponse, sans Chainlit.
- **À faire au passage :** les tests de contrat de la couture (elle en a **zéro**
  aujourd'hui) — l'endpoint va dépendre de la garantie « exactement un chunk non vide ».

### Étape 2 — L'image Docker de `agent-api`

- **But technique :** empaqueter la tranche `support-agent` (§4) et la lancer seule,
  état sur un **volume** (SQLite) pour commencer.
- **But pédagogique :** voir ce qu'un conteneur exige qu'un `make run` pardonnait —
  chemins, variables d'environnement, utilisateur, port exposé.
- **Vérification :** `make docker-up` → agent appelable depuis l'hôte.

**Fait le 2026-07-25.** `packages/support-agent/Dockerfile` (multi-stage) + `compose.yaml`
+ `.dockerignore` + cibles `make docker-build/up/logs/down`. Vérifié en live : conteneur
`healthy`, vraie réponse FAQ à travers HTTP, `401` sans clé, process en **non-root**
(`appuser`), **aucun `.env` dans l'image**, ni `pytest` ni `ruff` (image de 509 Mo).
Surtout : après un `docker compose down` puis `up`, l'agent **se souvient** du fil
précédent — l'état vit dans le volume nommé, pas dans le conteneur.

**Trois pièges rencontrés, qui valent d'être retenus :**

1. **Un `--mount=type=bind` ne vit que le temps de son `RUN`.** La couche 2 ne
   trouvait plus les manifestes racine (`No pyproject.toml found`) : il faut les
   `COPY` pour la seconde synchro.
2. **`env_file` de Compose ÉCRASE les `ENV` de l'image.** Le `.env` de l'hôte porte
   `KNOWLEDGE_DIR=./data/kb-velmo` (relatif à *sa* racine), ce qui n'a aucun sens en
   conteneur. D'où le bloc `environment:` de `compose.yaml`, de priorité supérieure,
   qui rétablit les chemins absolus.
3. **Même image de base dans les deux stages.** Un venv fige le chemin de son
   interpréteur : le construire contre le Python managé d'`uv` puis l'exécuter sur
   une autre base donne un venv qui pointe vers un interpréteur absent.

**Mesure pour l'étape 5 :** le warm-up passe de **2,9 s sur l'hôte à 4,4 s en
conteneur**. C'est ce chiffre — pas celui de l'hôte — qui doit décider du
`minReplicas` sur ACA.

### Étape 3 — L'état durable : Postgres + pgvector

- **But technique :** `langgraph-checkpoint-postgres`, `PERSISTENCE_BACKEND=postgres`,
  `PostgresStore(index=…, ttl=…)`, schémas séparés `agent_state` / `knowledge`.
  Compose = `agent-api` + `postgres`.
- **But pédagogique :** vérifier que « basculer de persistance = une variable d'env »
  est vrai **en fait**, pas seulement dans la doc. C'est l'invariant n°1 mis à l'épreuve.
- **Vérification :** redémarrer les conteneurs ; conversation et souvenirs intacts.

**Fait le 2026-07-25.** `memory/postgres_conn.py` (pool partagé) + les deux branches
`postgres` câblées + service `postgres` dans `compose.yaml` + `tests/test_persistence.py`.
Annoncée comme « l'étape la plus incertaine », elle a démarré **du premier coup** :
schéma `agent_state` créé, 8 tables, extension `vector` installée, sweeper TTL lancé.

**Vérifié en live, dans cet ordre** (c'est l'ordre qui fait la preuve) :

1. Une vraie réponse FAQ à travers HTTP, backend `postgres`.
2. `docker compose down` → **zéro conteneur** (`docker compose ps -a` vide), puis `up`.
3. **Court terme** (`thread_id` identique) : « redis-moi ma question précédente » →
   restituée **mot pour mot**.
4. **Long terme** (`user_id` identique, `thread_id` **neuf**) : « quelle est ma couleur
   préférée ? » → « le vert ».
5. **Contre-épreuve d'isolation** — la vérification qui manque le plus souvent :
   *un autre* `user_id`, même question → « je ne connais pas encore votre couleur ».
   Sans elle, on aurait pu confondre « la mémoire marche » avec « la mémoire fuit ».
6. Le TTL n'est pas décoratif : `expires_at = 2027-07-25`, `ttl_minutes = 525600`.

**L'inconnue levée :** la bascule a bien coûté **zéro ligne de code métier**. Le
graphe, les nœuds et la couture n'ont pas bougé — seules la config et `memory/`
ont changé. L'invariant n°1 est vérifié *en fait*, plus seulement en doc.

**Trois pièges rencontrés :**

1. **`psycopg` seul ne se connecte à rien.** C'est un *wrapper* : sans `libpq`, il
   lève « no pq wrapper available » **à l'import**. Il faut l'extra `psycopg[binary]`,
   qui embarque un libpq précompilé dans la roue — sinon il faudrait installer des
   paquets système dans l'image.
2. **L'image officielle `postgres` n'a pas pgvector.** Le checkpointer démarrerait ;
   c'est le store long terme qui échouerait à son `setup()`. D'où `pgvector/pgvector:pg17`.
3. **LangGraph compte les TTL en MINUTES.** On configure une rétention en *jours*
   (c'est ainsi qu'une politique s'écrit), donc la conversion est faite à un seul
   endroit — et **testée** : se tromper d'un facteur 1440 supprime les souvenirs
   clients le lendemain, ou conserve des données personnelles pendant des siècles.

**Deux choses apprises qui ne se voyaient pas sur le papier :**

- **Le `search_path` est par SESSION**, donc il se pose sur *chaque* connexion du
  pool (le hook `configure`), pas une fois au démarrage. Une connexion recréée après
  une coupure retrouve ainsi le bon schéma.
- **Un pool, pas une connexion** : le serveur répond depuis un pool de threads
  (`asyncio.to_thread` dans `api.py`) et le sweeper TTL a le sien. Une connexion
  unique aurait sérialisé tout le monde derrière un verrou — invisible à un seul
  appelant, et c'est bien ça le problème.

**Mesure pour l'étape 5 :** le warm-up reste à **4,4 s** — Postgres ne coûte rien au
démarrage. Le chiffre qui décidera du `minReplicas` est donc inchangé.

### Étape 4 — Le conteneur `client` : Chainlit devient client HTTP

- **But technique :** deux conteneurs séparés ne partagent pas de process : le client
  ne peut plus importer `stream_reply`, il consomme l'**URL** de `agent-api`.
- **But pédagogique :** c'est **la démonstration que la couture valait le coup**. Si
  le remplacement de l'import par un appel HTTP ne touche que le client, le
  découplage est prouvé, pas revendiqué.
- **Vérification :** deux conteneurs, l'un ne connaissant de l'autre que son URL.

**Fait le 2026-07-25.** Nouveau `packages/client/src/client_chainlit/agent_client.py`
(la couture **par-dessus le réseau**), `packages/client/Dockerfile`, service `client`
dans `compose.yaml`, et 15 tests de contrat hors ligne
(`packages/client/tests/test_agent_client.py`).

**Le chiffre qui répond à la question posée :** `app.py` a changé d'**UNE ligne**.

```diff
- from support_agent import stream_reply          # un process, un appel Python
+ from client_chainlit.agent_client import stream_reply   # un réseau, un POST SSE
```

Le corps du handler n'a pas bougé d'un caractère, parce que le client HTTP expose
**exactement** la même signature `stream_reply(message, *, user_id, thread_id)`.
Une couture qu'il faut renégocier quand le transport change n'en était pas une.

**Le geste qui rend le découplage vérifiable :** `support-agent` a été **retiré des
dépendances** de `packages/client`. Ce n'est plus une règle de style qu'on se
rappelle, c'est un fait du graphe de dépendances. Contrôlé dans l'image construite :

| Module | Dans l'image `client` |
|---|---|
| `chainlit`, `httpx_sse`, `client_chainlit` | présents |
| `support_agent`, `langgraph`, `langchain` | **absents** |

⚠️ Nuance honnête : le `.venv` **partagé** du workspace contient toujours
`support_agent` (le groupe dev l'installe pour les tests). Ce qui attrape une
régression, c'est donc le **build de l'image**, pas la machine de dev.

**Ce que le réseau ajoute, et qu'il fallait traiter :** l'appel en mémoire ne
pouvait pas échouer *pour des raisons de transport*. Le client HTTP, si :
connexion refusée, 401, timeout de lecture, flux coupé en deux. L'invariant du
package (« tout chemin livre exactement un chunk non vide ») est donc **réaffirmé
côté client**, sans quoi une panne réseau s'afficherait dans Chainlit comme une
**bulle vide**. La cause réelle part dans les logs ; le client, lui, voit une phrase.

**Deux pièges payés :**

1. **Chainlit écrit dans le cwd, et il le fait à l'IMPORT.** `chainlit/config.py`
   appelle `FILES_DIRECTORY.mkdir()` pendant l'import du CLI — donc avant tout
   argument de ligne de commande. En non-root dans un `/app` appartenant à root, le
   conteneur meurt sur `PermissionError: /app/.files` **avant d'avoir logué quoi que
   ce soit**. La solution n'est pas d'énumérer ses dossiers (`.files`, `.chainlit`,
   `chainlit.md` — cette liste appartient à Chainlit) mais de lui donner un **cwd
   inscriptible** : `WORKDIR /home/appuser`. Les sources restent dans `/app`,
   trouvées par `PYTHONPATH`.
2. **Le `.dockerignore` est PARTAGÉ par les deux images.** Il excluait
   `packages/client/` — parfait tant qu'une seule image se construisait, fatal dès
   la seconde. Ce qui garantit que le cerveau n'entre pas dans l'image du client,
   ce n'est pas cette liste : c'est que **chaque Dockerfile ne `COPY` que sa
   tranche**.

**Un choix inverse de celui de l'agent, assumé :** l'image du client **copie ses
sources** au lieu d'installer une roue (`--no-editable`). Raison concrète :
`chainlit run` prend un **chemin de fichier**. Installer *aussi* la roue mettrait
un second exemplaire du module dans `site-packages` — exactement le piège de
péremption que le Dockerfile de l'agent évite. Un seul exemplaire, atteignable à
la fois comme chemin (pour le CLI) et comme paquet (pour l'import), via `PYTHONPATH`.

**Vérifié en live :** les trois conteneurs *healthy* dans l'ordre
(`postgres` → `agent-api` → `client`, chaîné par `depends_on: service_healthy`),
puis une vraie conversation lancée **depuis le conteneur `client`**, avec ses
propres variables : réponse FAQ sourcée, et **mémoire courte conservée d'un tour à
l'autre** à travers le réseau (l'agent se souvient du prénom donné au tour
précédent). Image client : **405 Mo** (contre 544 Mo pour l'agent).

**Contre-épreuve, depuis le conteneur `client`, corroborée des deux côtés** — le
client seul ne prouve rien, un client cassé refusant tout lui aussi :

| Cas | Ce que le client affiche | Ce que le serveur logue |
|---|---|---|
| **Témoin positif** (bonne clé) | réponse FAQ, 5,1 s | `POST /chat 200 OK` |
| Mauvaise clé | « le service a refusé la demande », 0,0 s | `POST /chat 401` |
| Aucune clé | « le service a refusé la demande », 0,0 s | `POST /chat 401` |
| Agent injoignable | « momentanément injoignable », 0,0 s | **aucune ligne** |

Trois lectures, dans l'ordre d'importance. **Un chunk non vide dans les quatre
cas** : l'invariant tient à travers le réseau. **Les refus tombent en 0,0 s**, donc
*avant* tout appel au LLM — la clé de service est aussi une protection budgétaire,
pas seulement un contrôle d'accès. Et la **ligne absente** du 4ᵉ cas est celle qui
vaut le plus : elle prouve que « injoignable » vient d'un échec de **transport**,
et non d'un refus serveur qu'on aurait mal étiqueté.

**Un incident d'environnement, instructif :** une coupure internet pendant les
tests a produit trois symptômes qui semblaient sans rapport — le témoin positif en
échec, le CLI Docker figé, et le dashboard sans logs. Une seule cause : le
conteneur ne pouvait plus **sortir** (confirmé après coup par un
`ConnectTimeout` LangSmith dans ses logs), et Docker Desktop se bloque sur perte
réseau. Deux leçons durables. Un `Quit` de Docker Desktop **ne tue pas**
`com.docker.backend` : il faut `pkill -9`, sinon l'application refuse de se rouvrir.
Et un arrêt brutal de la VM **casse le tuyau de logs** des conteneurs : ils
fonctionnent, mais n'écrivent plus rien de visible — un `docker compose up -d
--force-recreate <service>` le rétablit. Sans le témoin positif, on aurait conclu
« la clé est vérifiée » alors que le client n'atteignait plus rien.

**Ce que le client ne reçoit PAS**, et c'est le vrai bénéfice de sécurité : pas de
clé LLM, pas d'URL de base de données, pas de `KNOWLEDGE_DIR`. **Deux variables**,
`AGENT_API_URL` et `AGENT_API_KEY`. Jusqu'à l'étape 3, il connaissait tout ça —
non par besoin, mais parce qu'il partageait le process.

### Étape 5 — Azure

- **But technique :** **ACR** pour les images, **ACA** pour l'exécution (ingress
  **HTTPS** géré, secrets, scale), **Azure Database for PostgreSQL Flexible Server**
  avec `pgvector` activé en extension. Plus l'**identité prouvée** : `user_id` dérivé
  d'un token vérifié côté serveur, **jamais lu du corps de la requête** — c'est le
  trou 🔴 §5.1 de l'archi cible, à refermer **avant** toute mise en ligne publique.
- **But pédagogique :** mesurer ce qui se transpose (l'image, à l'identique) et ce qui
  ne se transpose pas (le Compose, remplacé par le YAML d'ACA).
- **Bonus cohérent :** `config.py` porte déjà `llm_inference_endpoint` /
  `openai_compatible`. Si l'abonnement de formation inclut Azure OpenAI ou AI Foundry,
  le LLM passe chez Azure **sans une ligne de code** — un seul cloud, une seule
  facture. C'est la factory qui paie.
- **Vérification :** une URL live, appelable au `curl`.

⚠️ **Piège Azure à traiter à cette étape :** ACA peut descendre à **zéro réplique**.
Chaque réveil ré-indexerait la FAQ — appels embeddings facturés + latence de démarrage
à froid. Le coût est **mesuré** depuis l'étape 1 : le serveur logue son warm-up au
démarrage, **2,9 s** sur la FAQ Velmo (16 fichiers, 2 appels embeddings). C'est ce
chiffre, et non une intuition, qui doit décider du `minReplicas`. C'est là que l'invariant n°5 (« l'application n'indexe jamais en production »)
cesse d'être de la doctrine et devient une ligne de facture. Deux issues :
`minReplicas: 1`, ou sortir l'ingestion du boot pour de vrai.

### Alternatives d'hébergement écartées

**App Service for Containers** (plus simple, mais taillé pour un conteneur unique) et
**AKS** (surdimensionné pour un projet de formation : un cluster à administrer).

---

## 7. Deux décisions révisées, assumées

1. **Chainlit bascule bien en client HTTP** (étape 4). J'avais écrit l'inverse quand
   la cible était encore un déploiement in-process : faux dès qu'on veut deux
   conteneurs. Le changement est petit **et** il valide la couture.
2. **`shop-double` n'est PAS conteneurisé** pour l'instant : il reste in-process
   derrière le port `actions/`. Le port suffit à marquer la frontière ; en faire un
   service demanderait un adaptateur HTTP dont le déploiement n'a pas besoin. À
   rouvrir seulement si on veut démontrer la frontière marchand *en réseau*.

---

## 8. Glossaire des acronymes employés ici

Rangés par thème. Les termes durables ont vocation à remonter dans
[`glossaire.md`](glossaire.md) (qui contient déjà **SSE**, **token**, **RAG**…).

### Réseau & exposition

| Acronyme | Développé | Ce que c'est ici |
|---|---|---|
| **API** | *Application Programming Interface* | Le contrat d'appel d'un service. Ici : `POST /chat`, la porte d'entrée réseau de l'agent |
| **HTTP** | *HyperText Transfer Protocol* | Le protocole du web : une requête, une réponse |
| **HTTPS** | *HTTP Secure* | Le même, chiffré par TLS |
| **TLS** | *Transport Layer Security* | Le chiffrement de la connexion (l'ancien « SSL »). Fourni par Azure, pas par notre code |
| **SSL** | *Secure Sockets Layer* | Ancêtre de TLS ; le mot survit dans les options de connexion Postgres (`sslmode=require`) |
| **DNS** | *Domain Name System* | La traduction `mon-agent.example.com` → adresse IP |
| **SSE** | *Server-Sent Events* | Le serveur pousse plusieurs messages au fil de l'eau sur **une seule** connexion HTTP. Unidirectionnel serveur → client — exactement notre besoin |
| **URL** | *Uniform Resource Locator* | L'adresse du service ; en étape 4, la **seule** chose que le client connaît de l'agent |

### Empaquetage & exécution

| Acronyme | Développé | Ce que c'est ici |
|---|---|---|
| **CLI** | *Command-Line Interface* | Interface en ligne de commande. L'agent n'est **que** ça aujourd'hui (`python -m support_agent.agent`) |
| **cwd** | *current working directory* | Le dossier depuis lequel un process est lancé. Nos chemins `./data/…` en dépendent — d'où la fragilité en conteneur |
| **PaaS** | *Platform as a Service* | L'hébergeur exécute ton conteneur et gère la machine (ACA en est un) |
| **VPS** | *Virtual Private Server* | Une machine virtuelle louée, à administrer soi-même |
| **SaaS** | *Software as a Service* | Logiciel consommé en ligne, sans l'héberger (LangSmith) |
| **YAML** | *YAML Ain't Markup Language* | Format de fichier de configuration (Compose, ACA) |

### Azure

| Acronyme | Développé | Ce que c'est ici |
|---|---|---|
| **ACR** | *Azure Container Registry* | L'entrepôt d'images Docker — on y pousse celle testée en local |
| **ACA** | *Azure Container Apps* | Le service qui exécute les conteneurs : ingress HTTPS, secrets, scale. Notre cible |
| **AKS** | *Azure Kubernetes Service* | Kubernetes managé. **Écarté** : surdimensionné ici |

### Données, sécurité, conformité

| Acronyme | Développé | Ce que c'est ici |
|---|---|---|
| **RGPD** | Règlement Général sur la Protection des Données | Impose rétention et droit à l'oubli sur la mémoire long terme (d'où le `ttl` du store) |
| **IdP** | *Identity Provider* | Fournisseur d'identité (Entra ID, Auth0…) qui **prouve** qui appelle → dérive le `user_id` |
| **PII** | *Personally Identifiable Information* | Données personnelles identifiantes, caviardées par `guard_output` avant persistance |
| **JWT** | *JSON Web Token* | Format de jeton signé ; le candidat naturel pour la preuve d'identité de l'étape 5 |

### Agent & LLM

| Acronyme | Développé | Ce que c'est ici |
|---|---|---|
| **LLM** | *Large Language Model* | Le modèle de langage. Agnostique par contrat (invariant n°4) |
| **RAG** | *Retrieval-Augmented Generation* | Génération augmentée par recherche documentaire : la FAQ Velmo |
| **TTFT** | *Time To First Token* | Délai avant le premier morceau de réponse. Ici égal au temps **total**, la couture ne livrant qu'un chunk (cf. [`latence.md`](latence.md)) |
| **SDK** | *Software Development Kit* | Bibliothèque cliente d'un service (le SDK LangGraph, les SDK providers) |

### Méthode

| Sigle | Développé | Ce que c'est ici |
|---|---|---|
| **YAGNI** | *You Aren't Gonna Need It* | Ne pas construire avant d'avoir constaté le besoin. Le précédent du projet : l'index Chroma, codé puis annulé |
| **CI/CD** | *Continuous Integration / Continuous Deployment* | Automatiser tests et déploiement. Manque encore (cf. [`perimetre-final.md`](perimetre-final.md) §B.4) |

---

## 9. Ce que ce document ne dit pas

| Question | Où |
|---|---|
| Les blocs déployés et les 5 invariants | [`architecture-cible-2026-07-25.md`](architecture-cible-2026-07-25.md) |
| L'état actuel du code | [`architecture.md`](architecture.md) |
| Qui produit / transporte / affiche le flux | [`streaming.md`](streaming.md) |
| Le rangement `data/` vs `database/` | [`../database/README.md`](../database/README.md) |
| Ce qui manque pour mériter le mot « prod » | [`perimetre-final.md`](perimetre-final.md) |
| L'ordre de travail transverse | [`../TODO_priorities.md`](../TODO_priorities.md) |
