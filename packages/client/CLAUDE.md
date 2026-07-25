# CLAUDE.md — `packages/client` / `client-chainlit` (coquille de démo Chainlit, scope B)

> Complément **local** au `CLAUDE.md` racine (qui, lui, est toujours chargé et
> porte les invariants transverses : agnosticisme LLM, fr/en, `uv`/Python 3.12,
> structure workspace). Ce fichier n'est lu **qu'en travaillant dans ce package**
> et ne fait que **préciser** les règles propres au client. Il ne remplace rien.

## Le rôle de ce package en une phrase

Une **UI de chat jetable** (Chainlit) dont le seul but est de *voir l'agent
parler* (niveau 1, cf. [`docs/roadmap-frontend.md`](../../docs/roadmap-frontend.md)).
**Assumée non-prod.** Le vrai front déployé, c'est le niveau 2 (phase B2).

📛 **Nom** : distribution `client-chainlit`, module `src/client_chainlit/` —
Chainlit est **une** implémentation de client, pas *le* client. Ajouter un second
client ne doit pas obliger à renommer celui-ci.

## Invariant n°1 : le front ne connaît JAMAIS le cerveau (NON NÉGOCIABLE)

Depuis l'**étape 4 du déploiement**, cet invariant est plus fort qu'avant : le
front ne connaît plus le paquet `support_agent` **du tout**. Il connaît une **URL**.

- Ce package importe **uniquement** la couture, désormais en version réseau :
  `from client_chainlit.agent_client import stream_reply`.
- **Interdit** ici : `support_agent`, `langgraph`, `langchain`, un `graph`, un
  `state`, un nœud, `stream_mode=...`. Tout ça vit derrière `AGENT_API_URL`.
- `support-agent` a été **retiré des dépendances** (`pyproject.toml`) : le
  découplage est un fait du graphe de dépendances, pas une règle qu'on se rappelle.
  ⚠️ Le `.venv` **partagé** contient quand même `support_agent` (groupe dev) — ce
  qui attrape une régression, c'est le **build de l'image du client**.
- Pourquoi : c'est ce couplage-zéro qui rend Chainlit **remplaçable** (par du React
  en B2) sans toucher au cerveau. Le tuto Chainlit classique fait `graph.stream(...)`
  dans le handler — on s'en écarte **volontairement**, et maintenant on ne pourrait
  physiquement plus le faire.

> Test mental avant d'ajouter un import : « est-ce que du React aurait cet
> import ? » Si non, il n'a rien à faire ici — la logique remonte dans la couture
> (`packages/support-agent/src/support_agent/api.py`), pas dans le client.

## `agent_client.py` — la couture par-dessus le réseau

- **Même signature** que la couture in-process : `stream_reply(message, *, user_id,
  thread_id) -> AsyncIterator[str]`. C'est ce qui a permis à `app.py` de ne changer
  que d'**une ligne d'import** à l'étape 4. Ne pas la faire diverger.
- **Ce module ne connaît ni l'UI ni l'agent** : pas d'`import chainlit`, pas
  d'`import support_agent`. Pur transport — le même fichier servirait un React.
- **Il doit livrer exactement un chunk non vide, sur TOUT chemin** — y compris les
  pannes que l'appel en mémoire n'avait pas : connexion refusée, 401, timeout, flux
  coupé. Sinon Chainlit affiche une **bulle vide** sur panne réseau. La cause réelle
  va dans les **logs** ; le client voit une phrase.
- **Deux identités, deux canaux** : `AGENT_API_KEY` en **en-tête** (l'APPELANT
  a-t-il le droit ?), `user_id` dans le **corps** (DE QUI parle-t-on ?). Ne jamais
  les mélanger. Prouver le `user_id` est le travail du **serveur** (étape 5) : un
  client ne peut pas être ce qui prouve sa propre identité.
- **Types d'événements SSE inconnus = ignorés**, jamais une erreur : c'est ce qui
  permettra à B1.5 d'ajouter des étapes/sources sans casser ce client.
- Les deux variables se lisent **à l'appel** (`get_agent_api_url()` /
  `get_agent_api_key()`), pas à l'import — sinon un `.env` chargé après l'import du
  module serait ignoré, et les tests ne pourraient rien surcharger.

## Streaming : le front **affiche**, il ne produit pas

- La production du flux = l'agent (`stream_reply`, forme native = générateur) ;
  depuis l'étape 4, il est **transporté** en SSE par `server.py` et **reconstitué**
  en générateur par `agent_client.py`. Trois couches, mêmes rôles qu'avant :
  l'agent produit, l'API transporte, le front affiche.
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

- **Il faut DEUX process depuis l'étape 4** : `make serve` (l'agent, sur `:8000`)
  puis `make ui` (Chainlit, sur `:8001`). Le client seul affiche une UI qui répond
  « service injoignable » — c'est le comportement correct, pas un bug.
- En conteneur : `make docker-up` lance les trois briques, UI sur
  **http://localhost:8101**, agent sur `:8100`.
- ⚠️ **Deux jeux de ports disjoints — `80xx` local, `81xx` conteneur — et ce n'est
  pas cosmétique.** Docker publie sur `0.0.0.0`, uvicorn écoute sur `127.0.0.1` :
  **deux adresses**, donc **aucun `Errno 48`**, et c'est la plus spécifique qui
  reçoit. La collision est silencieuse — on croit tester sa pile locale et on
  interroge le conteneur (vécu le 2026-07-25, un test entier invalidé). À ne pas
  confondre avec deux process sur la même adresse, qui échouent franchement.
  Ne pas réunifier ces ports « pour simplifier ». Corollaire de méthode : une
  sonde `curl` sur un port prouve que **quelque chose** répond, jamais **qui** —
  pour identifier l'interlocuteur, regarder ses logs, pas son port.
- **Toujours depuis la racine du repo**, pas depuis ce dossier. Ce package n'a plus
  besoin du cwd pour lui-même (il ne lit plus ni `./data/` ni `./database/` : c'est
  l'agent qui le fait, dans son process), mais Chainlit résout le chemin de
  `app.py` **relativement au cwd**, et c'est là que `.env` est lu.
- Chainlit génère `chainlit.md` + `.chainlit/` **dans le cwd** au 1er lancement :
  **gitignore** jusqu'à la phase B1.6 (écran d'accueil soigné + thème).
  ⚠️ Il crée ces dossiers **à l'import** de son CLI — d'où le `WORKDIR /home/appuser`
  du `Dockerfile` (voir son commentaire : un cwd non inscriptible = crash muet).
- Détail de la commande : [`README.md`](README.md).

## Identité (démo)

- `thread_id` (mémoire courte) = `cl.context.session.id` — une session = une
  conversation mémorisée.
- `user_id` (mémoire longue) = **simulé** (`"demo-user"`, pas d'auth). Trou n°2
  assumé non-prod, résolu au niveau 2. Formalisation propre : phase B1.4.
- ⚠️ Depuis l'étape 4, ces deux valeurs **traversent le réseau** dans le corps de la
  requête. Le `user_id` y est donc **déclaré**, pas prouvé : n'importe quel appelant
  peut écrire celui d'un autre. Le refermer se fait **côté serveur**
  (`support_agent.server._resolve_user_id`, étape 5), jamais ici — ajouter une
  vérification dans le client ne prouverait rien, un client pouvant mentir sur tout
  ce qu'il envoie.

## Avant d'écrire du code Chainlit

Vérifier la doc à jour via **Context7** (`/chainlit/docs`) — l'API bouge
(`@cl.on_message`, `cl.Message`, steps, éléments). Même règle que pour lang\*.
