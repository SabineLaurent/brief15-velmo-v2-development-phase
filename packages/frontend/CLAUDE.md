# CLAUDE.md — `packages/frontend` (coquille de démo Chainlit, scope B)

> Complément **local** au `CLAUDE.md` racine (qui, lui, est toujours chargé et
> porte les invariants transverses : agnosticisme LLM, fr/en, `uv`/Python 3.12,
> structure workspace). Ce fichier n'est lu **qu'en travaillant dans ce package**
> et ne fait que **préciser** les règles propres au front. Il ne remplace rien.

## Le rôle de ce package en une phrase

Une **UI de chat jetable** (Chainlit) dont le seul but est de *voir l'agent
parler* (niveau 1, cf. [`docs/roadmap-frontend.md`](../../docs/roadmap-frontend.md)).
**Assumée non-prod.** Le vrai front déployé, c'est le niveau 2 (phase B2).

## Invariant n°1 : le front ne connaît JAMAIS lang\* (NON NÉGOCIABLE)

- Ce package importe **uniquement** la couture : `from support_agent import stream_reply`.
- **Interdit** ici : `langgraph`, `langchain`, un `graph`, un `state`, un nœud,
  `stream_mode=...`. Toute cette complexité vit **derrière** `stream_reply`.
- Pourquoi : c'est précisément ce couplage-zéro qui rend Chainlit **remplaçable**
  (par du React en B2) sans toucher au cerveau. Le tuto Chainlit classique fait
  `graph.stream(...)` dans le handler — on s'en écarte **volontairement**.

> Test mental avant d'ajouter un import : « est-ce que React aurait ce même
> import ? » Si non, il n'a rien à faire ici — la logique remonte dans la couture
> (`packages/support-agent/src/support_agent/api.py`).

## Streaming : le front **affiche**, il ne produit pas

- La production du flux = l'agent (`stream_reply`, forme native = générateur).
- Le front ne fait que **rendre au fil de l'eau** (`msg.stream_token(chunk)`),
  et c'est **la seule couche** qui peut décider d'une cadence (smoothing /
  typewriter) — jamais la couture. Détail : [`docs/streaming.md`](../../docs/streaming.md).
- ⚠️ **Aujourd'hui la couture ne livre qu'UN chunk** (la réponse complète, déjà
  passée par le garde de sortie : garder et streamer sont exclusifs). On continue
  malgré tout à consommer un **flux** — c'est le contrat, et la phase B1.5 en
  yieldera plusieurs sans toucher à ce fichier.
- Ne **jamais** compenser en appelant le graphe directement pour récupérer des
  tokens : ce serait afficher du texte que le garde n'a pas validé.
- `stream_token` est ce qui **crée** le message côté serveur : `update()` sur un
  message jamais envoyé ne s'affiche pas. D'où le repli `send()` si le flux est
  vide — un front ne présume pas d'une garantie faite de l'autre côté d'une couture.

## Lancer / cwd

- **Toujours depuis la racine du repo**, pas depuis ce dossier :
  `uv run chainlit run packages/frontend/src/frontend/app.py -w`.
- Raison : l'agent résout ses données en **chemins relatifs au cwd** (`./data/faq`,
  `./database/…` — cf. [`database/README.md`](../../database/README.md)). Lancer
  d'ailleurs casse ces chemins.
- Chainlit génère `chainlit.md` + `.chainlit/` à la racine au 1er lancement :
  **gitignore** jusqu'à la phase B1.6 (écran d'accueil soigné + thème).
- Détail de la commande : [`README.md`](README.md).

## Identité (démo)

- `thread_id` (mémoire courte) = `cl.context.session.id` — une session = une
  conversation mémorisée.
- `user_id` (mémoire longue) = **simulé** (`"demo-user"`, pas d'auth). Trou n°2
  assumé non-prod, résolu au niveau 2. Formalisation propre : phase B1.4.

## Avant d'écrire du code Chainlit

Vérifier la doc à jour via **Context7** (`/chainlit/docs`) — l'API bouge
(`@cl.on_message`, `cl.Message`, steps, éléments). Même règle que pour lang\*.
