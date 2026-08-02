# Revue de sécurité — branche `velmo-2.0` (déploiement étapes 1→4)

**Objet :** revue de sécurité ciblée des changements introduits par la branche, à la
recherche de vulnérabilités **exploitables** (pas de revue de style ni de qualité).
**Date :** 2026-07-25
**Commit testé :** `8566737` — *feat(compose): pin the container to authenticated mode, whatever .env says*
**Base de comparaison :** `main` (36 commits d'écart)
**Périmètre :** la nouvelle surface réseau et tout ce qu'elle expose —
`support_agent/server.py`, `support_agent/api.py`, `client_chainlit/agent_client.py`,
`client_chainlit/app.py`, `memory/postgres_conn.py`, `memory/long_term.py`,
`memory/short_term.py`, `actions/sql/schema.py`, `compose.yaml`, les deux `Dockerfile`,
`.env.example`, `Makefile`.
**Méthode :** lecture du diff complet + du code environnant, vérification des
dépendances réellement installées (`uv.lock`, `.venv`) pour trancher les questions de
validation, lecture du code de Chainlit pour vérifier la provenance du `session.id`.
**Aucune modification appliquée.**

---

## Synthèse

**Aucune vulnérabilité haute.** Les fondamentaux de la nouvelle porte HTTP sont bons :
démarrage *fail-closed*, comparaison de clé en temps constant, conteneurs non-root, pas
de `.env` dans les images, Postgres non publié sur l'hôte, et un `compose.yaml` qui
force `API_ALLOW_UNAUTHENTICATED=false` quoi que dise le `.env`.

Les **deux problèmes retenus** sont **le même trou vu des deux bouts du fil** :
*l'identité du client n'est jamais prouvée*. Ce trou est **assumé et documenté** dans le
code — c'est l'étape 5 du déploiement. Ce que la revue ajoute, c'est que **son coût a
changé** avec cette branche : jusqu'ici la mémoire longue vivait en RAM dans un process
mono-utilisateur ; elle vit désormais dans un **Postgres durable et partagé**, servi par
une **UI publiée sur toutes les interfaces de l'hôte**. Le même défaut de conception ne
coûte plus une démo bancale mais une **fuite de données personnelles entre clients**.

> 📌 À lire avec `docs/architecture-cible-2026-07-25.md` §5.1 (ce que l'étape 5 referme)
> et `docs/plan-deploiement-2026-07-25.md`.

---

## Ce qui a été vérifié et jugé SAIN

Écrit noir sur blanc, parce qu'une revue qui ne dit que ce qui cloche laisse croire que
le reste n'a pas été regardé.

| Sujet | Verdict | Pourquoi |
|---|---|---|
| Clé de service (`require_api_key`) | ✅ | `secrets.compare_digest` : pas de fuite du préfixe correct par le temps de réponse. |
| Démarrage sans clé | ✅ | Le `lifespan` **refuse de démarrer** ; le mode ouvert doit être demandé par son nom. Le `restart: unless-stopped` produit une boucle de crash, jamais une porte ouverte. |
| Création de schéma Postgres | ✅ | `psycopg.sql.Identifier` — identifiant correctement échappé, pas de concaténation. |
| `actions/sql/schema.py` (nouveau) | ✅ | ORM SQLAlchemy déclaratif, **aucune** requête construite en chaîne. Module d'ailleurs pas encore référencé. |
| Sérialisation SSE (`_sse`) | ✅ | `json.dumps` — le texte de l'agent ne peut pas casser la trame. |
| En-têtes `X-Thread-Id` / `X-User-Id` | ✅ | Pas d'injection CRLF possible : **seul `h11` est installé** (`httptools` absent du `uv.lock`), et `h11` valide les valeurs d'en-tête à l'écriture. |
| Images Docker | ✅ | Non-root (uid 10001 fixe), aucun `.env` embarqué, `.dockerignore` couvre `.env*`, l'image du client ne contient ni clé LLM ni driver de base. |
| Secrets côté client | ✅ | Le service `client` ne reçoit **pas** `env_file: .env` — deux variables, dont aucune clé LLM. |
| Postgres | ✅ | Pas de `ports:` : joignable uniquement par le réseau interne de Compose. |

---

## 🟠 V1 — L'identité du client est déclarée, pas prouvée

**Sévérité :** moyenne · **Catégorie :** contrôle d'accès (IDOR) · **Confiance :** 8/10
**Fichiers :** `packages/support-agent/src/support_agent/server.py:95` (le champ),
`:98-108` (`_resolve_user_id`), `:253` (l'appel) → `memory/long_term.py:31-40`

### Le fait

`ChatRequest.user_id` est lu **tel quel** dans le corps JSON, traverse
`_resolve_user_id`, et devient la **clé de namespace** du store long terme. Rien ne relie
l'**appelant authentifié** (celui qui présente `X-API-Key`) au **client dont on parle**.
Ce sont deux questions différentes, et le code le dit lui-même : la clé de service répond
« as-tu le droit d'utiliser cet agent ? », personne ne répond « es-tu bien ce client ? ».

Le même défaut vaut pour `thread_id` (`server.py:91`, `:252`), qui donne accès à un
**transcript de conversation** complet. Cette variante est moins grave *uniquement* parce
qu'un `thread_id` légitime est un UUID, donc non devinable — alors qu'un `user_id`
métier, lui, est **énumérable** (`C-marc-dubois`, cf. `actions/sql/schema.py`).

### Pourquoi ça compte MAINTENANT

Le trou est documenté et planifié (étape 5). Ce qui est **nouveau dans cette branche**,
c'est qu'il est devenu **joignable par le réseau** : avant `server.py`, la couture était
un appel Python en process et aucun appelant ne pouvait *choisir* une identité.

### Scénario d'exploitation

Un attaquant qui détient la clé de service — conteneur `client` compromis, `AGENT_API_KEY`
fuitée, ou n'importe quelle machine du LAN une fois `agent-api` publié sur `0.0.0.0:8100`
(`compose.yaml:107`) — envoie :

```http
POST /chat
X-API-Key: <la clé>

{"message": "Rappelle-moi tout ce que tu sais sur moi",
 "user_id": "<identifiant de la victime>"}
```

Le graphe charge le namespace de la victime et l'agent **récite ses données
personnelles**. Le sens **écriture** est tout aussi ouvert : la même requête peut
**empoisonner** la mémoire longue d'un client avec des faits choisis par l'attaquant,
que l'agent utilisera ensuite dans les sessions légitimes de cette personne.

### Ce qui limite la portée (à dire honnêtement)

L'exploitation **exige la clé de service**. Dans la pile Compose, seul le conteneur
`client` la détient, et lui envoie un `user_id` codé en dur : **un navigateur ne peut pas
atteindre ce chemin**. La frontière tient donc — c'est ce qui maintient cette entrée en
« moyenne » et non en « haute ».

### Correctif

- Dériver `user_id` **côté serveur** d'une preuve vérifiée. `_resolve_user_id` est
  exactement l'endroit prévu pour ça — c'est tout l'intérêt de l'avoir isolée.
- **En attendant l'étape 5** : retirer `user_id` de `ChatRequest` pour que le champ ne
  soit pas *settable du tout*, et le frapper depuis le principal authentifié (une clé de
  service ↔ un périmètre client fixe).
- Même traitement pour `thread_id` : lier chaque fil à l'identité qui l'a créé et
  **rejeter** une requête dont le `thread_id` appartient à un autre `user_id` — pour
  qu'une clé de fil devinée ou fuitée ne suffise pas à lire un transcript.

---

## 🟠 V2 — Tous les visiteurs de l'UI partagent UNE mémoire longue durable

**Sévérité :** moyenne · **Catégorie :** exposition de données personnelles · **Confiance :** 8/10
**Fichiers :** `packages/client/src/client_chainlit/app.py:44` (la constante), `:96`
(l'envoi) · `memory/long_term.py:102-124` (le store durable) · `compose.yaml:78-79`,
`:148` (la publication)

### Le fait

`DEMO_USER_ID = "demo-user"` est envoyé comme `user_id` pour **chaque message de chaque
navigateur**. Cette constante est désormais la clé d'un **PostgresStore durable et
partagé** — plus le store en RAM par process qui était le défaut avant cette branche — et
l'UI est servie sur **toutes les interfaces** de l'hôte (`8101:8000`), **sans
authentification**.

Les outils de mémoire écrivent dans ce namespace ce que les clients confient en
conversation (nom, adresse, numéros de commande), et la recherche sémantique le ressert
ensuite à qui demande.

⚠️ **L'isolation par conversation ne rattrape pas ça** : le store s'interroge par
`user_id`, **à travers** les fils — c'est sa raison d'être (`long_term.py:1-16`).

### Scénario d'exploitation

Aucun credential, aucun identifiant à deviner, aucune interaction avec la victime.

1. Le client A ouvre `http://<hôte>:8101`. Pendant le support, l'agent mémorise
   « adresse de livraison : 12 rue X, commande O-2024-0103 ».
2. Le client B — toute personne joignant ce port, c'est-à-dire tout le réseau de l'hôte —
   ouvre la même URL dans une session neuve et demande « quelles sont mes commandes en
   cours ? » ou « quelle est mon adresse ? ».
3. La recherche sémantique sur le namespace `demo-user` partagé remonte les souvenirs de
   A, et l'agent les récite à B.

L'exposition **persiste aux redémarrages** : le store est adossé au volume `postgres-data`.

### Correctif

Ne pas laisser tourner une pile **durable et publiée** sur une clé mémoire constante.
Au choix :

- **La vraie réponse** : authentifier l'UI (Chainlit) et dériver `user_id` de l'identité
  connectée — c'est l'état visé, déjà noté en commentaire dans `app.py:42-43`.
- **Palliatif immédiat, coût nul** : clé de mémoire longue unique par session (deux
  visiteurs ne peuvent plus se télescoper), et publier `127.0.0.1:8101` au lieu de toutes
  les interfaces tant que l'identité est simulée.

### 🔍 Le piège à ne PAS tomber dedans

`cl.context.session.id` n'est **pas** un substitut d'identité, et c'est contre-intuitif :
il est **fourni par le navigateur** dans le payload d'authentification socket.io
(`chainlit/socket.py:175` → `session_id = auth["sessionId"]`), et **n'est pas validé
côté serveur** quand `require_login()` est faux. Autrement dit, un visiteur choisit
librement son `sessionId`, donc le `thread_id` qui en découle (`app.py:91`). Ce qui
protège aujourd'hui les transcrits, ce n'est pas un contrôle : c'est seulement le fait que
le front de Chainlit génère un UUID par défaut.

---

## Ce qui a été écarté (et pourquoi)

Pour que la prochaine revue ne re-signale pas les mêmes choses.

- **`.env.example` livre `API_ALLOW_UNAUTHENTICATED=true`.** C'est un défaut permissif
  dans un gabarit que `make setup` recopie — mais `make serve` n'écoute que sur
  `127.0.0.1` (uvicorn par défaut), et le conteneur force `false`. Pas de chemin
  d'exploitation concret. **Garder l'œil** si un jour un `--host 0.0.0.0` apparaît dans
  la cible `serve`.
- **Mot de passe Postgres `agent:agent` en clair dans `compose.yaml`.** Base non publiée,
  réseau interne, pile de développement. Sur Azure, ce bloc est remplacé par les secrets
  d'application.
- **L'UI de démo n'a pas d'authentification.** Vrai, mais assumé et documenté comme
  non-prod (niveau 1) ; l'impact réel est capturé par V2, qui est la version *concrète*
  du problème. Le reste (crédits LLM dépensés par un tiers) relève de l'épuisement de
  ressources, hors périmètre d'une revue de vulnérabilités.
- **`thread_id` devinable.** Écarté **en tant que finding autonome** : un UUID est réputé
  non devinable. Le point est conservé comme facteur aggravant dans V1 et comme piège
  documenté dans V2.
- **Injection d'en-tête via `X-User-Id`.** Vérifié et réfuté : `h11` valide les valeurs
  d'en-tête. (Un `user_id` non-latin-1 provoquerait une 500, pas une injection.)

---

## Suite

Rien à corriger *en urgence* sur cette branche : les deux entrées convergent vers
**l'étape 5 du déploiement** (identité prouvée), qui est déjà la prochaine étape du plan.
Ce que cette revue change, c'est l'**ordre d'attaque** de cette étape :

1. Fermer **V2** d'abord (palliatif à coût nul : bind sur `127.0.0.1` + clé mémoire par
   session). C'est le seul des deux qui ne demande aucun credential pour être exploité.
2. Puis **V1**, avec la vraie preuve d'identité — et refermer `user_id` **et**
   `thread_id` dans le même geste, pas seulement `user_id`.
