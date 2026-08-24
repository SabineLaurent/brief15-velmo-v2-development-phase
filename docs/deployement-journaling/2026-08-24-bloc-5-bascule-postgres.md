# 2026-08-24 — Bloc 5 : bascule de la mémoire sur PostgreSQL (R2 + R3)

**Objet :** faire passer l'agent déjà en ligne de `PERSISTENCE_BACKEND=memory` à
`postgres`, puis **prouver** que la mémoire survit à un redémarrage (R2) et qu'elle ne
fuit pas d'un utilisateur à l'autre (R3).
**Correspond à :** §9 du tuto `docs/2026-08-24-tuto-deploiement-azure-portail.md`.
**Prérequis :** [bloc 3](2026-08-24-bloc-3-postgresql-pgvector.md) (base + pgvector) et
[bloc 4](2026-08-24-bloc-4-webapp-agent-api.md) (Web App qui répond `200`).
**Statut :** ✅ **terminé** — R2 et R3 prouvés. Un incident d'authentification a occupé
l'essentiel du bloc ; il est journalisé en détail parce que la méthode de diagnostic vaut
plus que la panne.

---

## Les valeurs de ce bloc

| Élément | Valeur |
|---|---|
| Web App | `appwebsablvelmo` |
| URL publique | `https://appwebsablvelmo-fuenh6eag9f8fsc9.westeurope-01.azurewebsites.net` |
| `PERSISTENCE_BACKEND` | `memory` → **`postgres`** |
| `DATABASE_URL` | `postgresql://velmoadmin:MOTDEPASSE@pgsablvelmo.postgres.database.azure.com:5432/velmo-agent?sslmode=require` |
| `DATABASE_SCHEMA` | `agent_state` *(défaut du code — voir décision 2)* |
| Schéma créé | par le code, au premier démarrage |

---

## Ce que le bloc change vraiment — deux variables, quatre effets

`PERSISTENCE_BACKEND` n'est pas un simple interrupteur de stockage : il change **deux**
composants d'un coup, parce que le code les fabrique tous les deux à partir de la même
variable.

| Composant | Rôle | Fabriqué par | Ce que la bascule change |
|---|---|---|---|
| *Checkpointer* (mémoire de travail) | « qu'a-t-on dit **dans ce fil** ? » — clé `thread_id` | `memory/short_term.py:44-53` | `InMemorySaver` → `PostgresSaver` |
| *Store* (mémoire longue) | « que sais-je de **ce client** ? » — clé `user_id` | `memory/long_term.py:108-119` | `InMemoryStore` → `PostgresStore` |

Et au premier démarrage, le pool fait trois gestes **tout seul**
(`memory/postgres_conn.py:30-73`) :

1. `CREATE EXTENSION IF NOT EXISTS vector` — le bloc 3 l'a déjà fait à la main ; cette
   ligne est idempotente et sert de filet.
2. `CREATE SCHEMA IF NOT EXISTS agent_state`, puis `SET search_path` **sur chaque
   connexion** — le `search_path` est une propriété de session, pas de base.
3. `setup()` des deux backends → création des tables, puis démarrage du **sweeper TTL**
   si `MEMORY_TTL_DAYS` n'est pas nul (défaut : 365 jours).

**Aucune migration à lancer à la main.** Une base vide devient une base fonctionnelle au
démarrage. C'est ce qui rend le bloc réversible : repasser à `memory` ne casse rien, les
tables restent.

---

## Décision 1 — pourquoi cette bascule ne peut échouer que sur la base

Les blocs 1 à 4 ont écarté, dans l'ordre, l'architecture de l'image, le service d'IA, la
disponibilité de pgvector et l'ingress de la Web App. Le seul chemin réseau jamais
exercé jusqu'ici est **Web App → PostgreSQL**. Si le conteneur ne démarre plus après la
bascule, la cause est nécessairement là : chaîne de connexion, TLS ou pare-feu.

C'est tout l'intérêt d'avoir attendu : le diagnostic est écrit d'avance.

---

## Décision 2 — `DATABASE_SCHEMA` : l'ajouter ou pas

`config.py:59` déclare `database_schema: str = "agent_state"` et le `Dockerfile` ne le
surcharge pas. La variable est donc **inutile au fonctionnement** — contrairement à
`PERSISTENCE_BACKEND`, que `Dockerfile:58` force à `sqlite` et qu'il faut donc écraser
explicitement.

La règle, énoncée une fois pour toutes : **on explicite ce que l'image surcharge, et ce
dont la valeur est un choix d'exploitation.** Le reste alourdit la liste sans rien
garantir.

Ici, `agent_state` est posé quand même : c'est le nom d'un schéma en base, la valeur
qu'on lit dans `psql` quand on cherche où sont les tables. Le voir dans les app settings
évite d'aller ouvrir `config.py` pour répondre à « elles sont dans quel schéma ? ».

---

## Les gestes au portail

1. Web App `appwebsablvelmo` → **`Paramètres`** → **`Variables d'environnement`** →
   onglet **`Paramètres de l'application`**.
2. **Modifier** `PERSISTENCE_BACKEND` : `memory` → **`postgres`**.
3. **`+ Ajouter`** : `DATABASE_URL` = la chaîne du bloc 3, **en entier**, `?sslmode=require`
   compris.
4. **`+ Ajouter`** : `DATABASE_SCHEMA` = `agent_state`.
5. **`Appliquer`** → **`Confirmer`** → la Web App redémarre seule.

⚠️ **Ne rien juger pendant une à deux minutes.** Leçon du bloc 4 : le premier appel
après un redémarrage paie le démarrage à froid *plus* le lifespan complet (construction
du graphe + sondage des embeddings). Un timeout à ce moment mesure le réveil, pas une
panne.

---

## Le protocole de preuve — et pourquoi l'ordre fait la preuve

Trois vérifications, dans cet ordre. Chacune ne vaut que parce que la précédente a
réussi.

### Preuve 1 — le démarrage : `Supervision` → `Flux de journal`

À voir passer :

```
Postgres memory backend ready (schema=agent_state)
```

C'est `postgres_conn.py:60`, émis **après** `CREATE EXTENSION` et `CREATE SCHEMA`. Cette
ligne seule prouve que la Web App a ouvert une connexion TLS à PostgreSQL, traversé le
pare-feu et obtenu les droits de créer un schéma.

Puis `/ready` doit répondre `{"ready":true}` : le graphe se reconstruit sur le nouveau
backend.

### Preuve 2 — persistance inter-session (R2)

```bash
BASE=https://appwebsablvelmo-fuenh6eag9f8fsc9.westeurope-01.azurewebsites.net

# 1. Donner un fait — thread t1
curl -N -X POST "$BASE/chat" \
  -H "Content-Type: application/json" -H "X-API-Key: $API_KEY" \
  -d '{"message":"Retiens que ma couleur préférée est le vert.","user_id":"sabine","thread_id":"t1"}'

# 2. Portail → Vue d'ensemble → Redémarrer, puis attendre ~90 s

# 3. Redemander — MÊME user_id, thread_id NEUF
curl -N -X POST "$BASE/chat" \
  -H "Content-Type: application/json" -H "X-API-Key: $API_KEY" \
  -d '{"message":"Quelle est ma couleur préférée ?","user_id":"sabine","thread_id":"t2"}'
```

**`thread_id` neuf, `user_id` identique — les deux conditions comptent.**

- Rejouer sur `t1` interrogerait le *checkpointer* : on prouverait que l'historique du
  fil a survécu, pas que l'agent **sait** quelque chose du client.
- Le `thread_id` neuf force le passage par le *store*, donc par `search_memories` et le
  namespace `("memories", "sabine")`.

C'est la différence entre « la conversation a été rechargée » et « la mémoire longue
existe » — et c'est exactement l'exigence R2.

### Preuve 3 — isolation par utilisateur (R3), la contre-épreuve

```bash
curl -N -X POST "$BASE/chat" \
  -H "Content-Type: application/json" -H "X-API-Key: $API_KEY" \
  -d '{"message":"Quelle est ma couleur préférée ?","user_id":"autre-client","thread_id":"t3"}'
```

Attendu : **il ne sait pas.** `memories_namespace()` (`long_term.py:38-46`) range les
souvenirs sous `("memories", user_id)` : un autre `user_id` lit un namespace vide.

Sans cette troisième requête, la preuve 2 seule est ambiguë — une mémoire globale
partagée entre tous les clients répondrait « le vert » elle aussi. On confondrait « la
mémoire marche » avec « la mémoire fuit ».

### Le piège de lecture : deux causes pour un même symptôme

Si la preuve 2 renvoie « je ne sais pas », **deux explications** sont possibles et il
faut les séparer avant de soupçonner la base :

1. La mémoire ne persiste pas → la bascule a échoué.
2. **Le modèle n'a jamais appelé `save_memory`.** C'est un *outil* que l'agent décide
   d'utiliser (`memory_tools.py:48`), pas un enregistrement automatique de tout ce qui
   passe. Un « retiens que… » explicite le déclenche ; une phrase anodine, pas forcément.

Le départage se fait en base, pas au jugé :

```bash
psql "host=pgsablvelmo.postgres.database.azure.com port=5432 dbname=velmo-agent user=velmoadmin sslmode=require" \
  -c "\dt agent_state.*" -c "SELECT count(*) FROM agent_state.store;"
```

Table absente → la bascule a échoué. Table présente et vide → le modèle n'a rien
enregistré : reformuler la demande, ce n'est pas un problème de déploiement.

---

## Résultats obtenus

### Après `Appliquer` — `503` immédiat, pendant 9 minutes

```
== /health ==  HTTP 503 en 0.114942s
== /ready  ==  HTTP 503 en 0.135207s
```

Sonde lancée avec `--retry 12 --retry-delay 15 --retry-all-errors` : **21 réponses `503`
sur ~9 minutes**, toutes en ~0,1 s.

**Ce n'est pas un démarrage à froid — et c'est le temps de réponse qui le dit.** Un
conteneur qui chauffe fait *attendre* : le bloc 4 avait donné un timeout à 45 s, puis un
`/ready` à 19 s. Ici la réponse est immédiate : le front-end Azure répond tout de suite
qu'il **n'a pas de conteneur** vers qui router. Le processus ne démarre plus.

| Symptôme | Lecture |
|---|---|
| Timeout lent (30-45 s) | Le conteneur chauffe — patienter |
| **`503` immédiat (~0,1 s)** | **Pas de conteneur derrière — panne au démarrage** |

Le cadrage du bloc 4 se vérifie : les blocs 1 à 4 ayant prouvé l'image, le service d'IA,
pgvector et l'ingress, la seule chose neuve est le chemin **Web App → PostgreSQL**. Le
diagnostic était écrit d'avance.

### La distinction qui compte pour lire le log

`require_database_url()` (`postgres_conn.py:76-89`) et le pool échouent **dans le
lifespan**, donc **avant** qu'uvicorn n'ouvre le port 8000. Conséquence : App Service ne
voit jamais le port s'ouvrir et affiche son message générique. La vraie cause est
**au-dessus**, dans le log du conteneur — c'est le même piège qu'au bloc 4.

---

## L'enquête — trois écrans, quatre pistes, dans l'ordre où elles ont été suivies

### Écran 1 — `Instances` : le premier fait dur

Web App → `Instances`. La ligne d'instance affiche :

```
État : Bloqué
Dernière erreur : ContainerStartupFailure
Container exited with exit code 3 during startup after 50.6s.
Please inspect your container logs for more details.
```

**`exit code 3` est le code de sortie d'uvicorn quand le lifespan lève une exception.**
Ce n'est pas un plantage arbitraire : c'est « le démarrage a refusé de finir ». Cet écran
transforme un `503` opaque en un fait daté et chiffré, sans rien activer.

À retenir : **`Instances` avant les journaux.** Il donne le code de sortie et la durée en
un coup d'œil, et il est disponible par défaut.

### Piste 1 (fausse) — l'arithmétique des 50 secondes

Premier raisonnement : `_CONNECT_TIMEOUT_S = 30.0` (`postgres_conn.py:27`), et le bloc 4
avait mesuré ~19 s d'échauffement. 19 + 30 ≈ 50 → « c'est un timeout réseau, donc le
pare-feu ».

**Ce raisonnement est faux, et il faut savoir pourquoi.** `pool.open(wait=True,
timeout=30)` ne fait pas *un* essai de connexion : psycopg **réessaie en boucle** jusqu'à
expiration du délai. Un mot de passe refusé, une base inexistante et un silence réseau
produisent donc **tous** la même durée. Le chronomètre ne discrimine rien.

La leçon dépasse ce bloc : **une durée n'est une preuve que si l'on sait ce qui se passe
pendant.** Ici, la valeur observée était compatible avec l'hypothèse — mais elle l'était
aussi avec toutes les autres.

### Piste 2 (écartée sur pièce) — le pare-feu PostgreSQL

Serveur `pgsablvelmo` → `Paramètres` → `Mise en réseau`. Vérifié :

| Réglage | État constaté |
|---|---|
| Autoriser l'accès public via une adresse IP publique | ☑️ coché |
| **Autoriser l'accès public à partir d'un service Azure dans Azure** | ☑️ **coché** |
| Règle `ClientIPAddress_2026-8-24_14-56-52` | `81.185.169.38` |

Le bouton `Enregistrer` étant **grisé**, aucune modification n'était en attente : c'est
bien la configuration appliquée. **L'hypothèse tombe.**

⚠️ Détail relevé au passage : `curl -s ifconfig.me` renvoie depuis ce poste une adresse
**IPv6** (`2a02:8440:…`), alors que la règle enregistrée est **IPv4**. Sans effet sur la
Web App (qui passe par la case « services Azure »), mais susceptible de faire échouer un
`psql` de diagnostic depuis le poste — et de faire conclure à tort. Comparer avec
`curl -s -4 ifconfig.me`.

*Ce qui a été refusé :* le bouton `+ Ajouter 0.0.0.0 - 255.255.255.255`, juste à côté. Il
aurait peut-être fait disparaître le symptôme — en ouvrant la base à tout Internet, et
sans jamais nommer la cause.

### Écran 2 — `Supervision` → `Journaux` et `Journaux du serveur` : **vides**

Normal, et instructif :

- Ces deux écrans concernent les **journaux HTTP du serveur web**, pas la sortie du
  conteneur.
- Sur App Service **Linux**, la journalisation applicative est **désactivée par défaut**.

Le bon écran est `Supervision` → **`Flux de journal`** — mais il reste vide lui aussi tant
que la journalisation n'a pas été activée.

### Écran 3 — Kudu (SCM) : le log de plateforme, sans rien activer

Le site de gestion d'App Service expose les fichiers de log directement. Son nom d'hôte se
déduit de l'URL du site en insérant `.scm` :

| | |
|---|---|
| Site | `appwebsablvelmo-fuenh6eag9f8fsc9.westeurope-01.azurewebsites.net` |
| **SCM** | `appwebsablvelmo-fuenh6eag9f8fsc9.scm.westeurope-01.azurewebsites.net` |

⚠️ L'ancienne forme `appwebsablvelmo.scm.azurewebsites.net` **ne résout pas** : avec les
noms d'hôte uniques, le suffixe aléatoire fait partie du nom SCM aussi.

**Lister les logs Docker :**

```
https://appwebsablvelmo-fuenh6eag9f8fsc9.scm.westeurope-01.azurewebsites.net/api/logs/docker
```

Réponse (JSON) :

```json
[{"machineName":"lw0sdlwk000DJX","lastUpdated":"2026-08-24T18:46:20.068456Z",
  "size":83694,
  "href":"https://…/api/vfs/LogFiles/2026_08_24_lw0sdlwk000DJX_docker.log",
  "path":"/home/LogFiles/2026_08_24_lw0sdlwk000DJX_docker.log"}]
```

**Lire le fichier :** suivre le `href`, soit
`…/api/vfs/LogFiles/2026_08_24_lw0sdlwk000DJX_docker.log`.

Depuis un terminal non authentifié, ces URL renvoient `401` — c'est le navigateur déjà
connecté au portail qui porte la session. Vérifiable ainsi :

```bash
curl -s -o /dev/null -m 15 -w 'HTTP %{http_code}\n' \
  "https://appwebsablvelmo-fuenh6eag9f8fsc9.scm.westeurope-01.azurewebsites.net/api/logs/docker"
# → HTTP 401 : l'hôte existe et répond ; il manque la session
```

Autre voie, équivalente et guidée : Web App → `Diagnostiquer et résoudre les problèmes` →
**`Container Crash`**. Ne demande aucune activation non plus.

### Ce que le log de plateforme dit — et ce qu'il ne dit pas

**Il ne contient pas la trace Python.** C'est le journal de l'hôte : pull d'image, montage
des volumes, création du conteneur, sondes. Le `stdout` du conteneur n'y est pas, faute de
journalisation applicative activée.

Ce qu'il établit tout de même :

| Ligne | Ce qu'elle prouve |
|---|---|
| `18:06:33 — Site startup probe succeeded after 34.0463612 seconds` | **Le témoin.** Avant la bascule, en `memory`, l'agent démarrait en 34 s |
| `Image … is pulled from registry` puis `Container is running` — à chaque tentative | L'image et le registre sont **hors de cause** : le conteneur démarre, c'est le processus qui meurt |
| `Container has finished running with exit code: 3` à 18:26, 18:38, 18:39, 18:41, 18:43, 18:46 | **Systématique**, jamais intermittent. Ce n'est pas un aléa réseau |
| `Site startup probe failed after 46.9 / 71.4 / 48.9 / 50.2 / 51.3 / 50.4 seconds` | Durée stable ~50 s, cohérente avec un délai armé côté code |

### Le piège opérationnel : le site se met en `Blocked`

```
Site: appwebsablvelmo will be blocked for 1 minutes till 08/24/2026 18:40:30
State: Blocked … Site is blocked due to multiple, consecutive cold start failures
Start site prohibitted because the site is being blocked.
```

Après plusieurs échecs consécutifs, App Service **refuse les démarrages** pendant un délai
qui croît (1 min, puis 2 min). Les `Redémarrer` cliqués pendant cette fenêtre sont rejetés
sans rien tenter.

**Conséquence pratique :** cliquer `Redémarrer` en boucle ne fait pas qu'être inutile — ça
allonge le blocage et brouille la lecture du log, en y empilant des tentatives qui n'ont
jamais eu lieu. Une tentative, puis on lit.

---

## Point d'étape — ce qu'on savait à cet instant

*(Section conservée telle qu'elle a été écrite pendant l'enquête : elle montre l'état des
connaissances avant que la cause soit connue. La suite du fichier la dépasse.)*

**Bloc 5 non terminé à ce stade.** Cause non encore identifiée : le log applicatif
manque.

### Les deux actions engagées

**1. Activer la journalisation applicative** — sans elle, aucun diagnostic n'est possible.

`Supervision` → `Journaux App Service` → `Journalisation d'application` = **`Système de
fichiers`** → → `Enregistrer`. Puis **une seule** tentative de
redémarrage, ~90 s d'attente, et relecture du fichier Kudu. La ligne cherchée suit
`Application startup failed`.

À faire de toute façon : le bloc 8 demande de relever le taux de blocage des garde-fous
dans le flux de journal. Sans cette activation, il sera vide là aussi.

**2. Tester la chaîne depuis le poste** — indépendant d'Azure :

```bash
psql "postgresql://velmoadmin:MOTDEPASSE@pgsablvelmo.postgres.database.azure.com:5432/velmo-agent?sslmode=require" -c "SELECT 1;"
```

Les deux sont complémentaires : le log dit **ce que le conteneur a vécu**, le `psql` dit
**si la chaîne est valide**.

| `psql` | Log conteneur | Conclusion |
|---|---|---|
| Passe | Échoue | Le chemin Azure → Azure, ou autre chose dans le lifespan |
| Échoue pareil | — | La chaîne est en cause ; Azure n'y est pour rien |

### La cause, enfin nommée

Journalisation activée (`Journal des applications` = `Système de fichiers`, quota 35 Mo,
conservation 7 jours), **un seul** redémarrage, puis relecture du fichier Kudu. La trace
Python était là :

```
19:14:06  INFO:     Started server process [1]
19:14:06  INFO:     Waiting for application startup.
19:14:18  WARNING  psycopg.pool | error connecting in 'pool-1': connection failed:
          connection to server at "23.101.64.194", port 5432 failed:
          FATAL:  password authentication failed for user "velmoadmin"
19:14:21  WARNING  psycopg.pool | … (idem)
19:14:25  WARNING  psycopg.pool | … (idem)
19:14:31  WARNING  psycopg.pool | … (idem)
19:14:42  WARNING  psycopg.pool | … (idem)
19:14:46  ERROR:    Traceback (most recent call last):
            server.py:135 in lifespan     → await asyncio.to_thread(get_agent)
            api.py:40 in get_agent        → build_support_graph()
            builder.py:90                 → checkpointer = get_checkpointer()
            short_term.py:47              → pool = get_postgres_pool(
            postgres_conn.py:50           → _prepare_database(pool, url, schema)
            postgres_conn.py:56           → pool.open(wait=True, timeout=_CONNECT_TIMEOUT_S)
          psycopg_pool.PoolTimeout: pool initialization incomplete after 30.0 sec
19:14:46  ERROR:    Application startup failed. Exiting.
```

**`FATAL: password authentication failed for user "velmoadmin"`.**

### Ce que cette trace prouve, au-delà de la cause

**1. Le chemin réseau fonctionne.** `connection to server at "23.101.64.194", port 5432`
— le serveur a **répondu**, et il a répondu un refus applicatif. TCP, TLS et pare-feu sont
donc traversés. La piste 2 était bien à écarter, et le geste du bloc 3 (case « services
Azure ») était bien fait. Un `FATAL` du serveur est une **meilleure** nouvelle qu'un
silence : il prouve que tout le reste du chemin marche.

**2. La piste 1 était fausse pour la raison annoncée.** Cinq tentatives entre 19:14:18 et
19:14:42 — avec un intervalle qui croît (3 s, 4 s, 6 s, 11 s : le *backoff* de psycopg) —
puis `PoolTimeout … after 30.0 sec`. Le pool réessayait bel et bien, et la durée totale
était donc **insensible à la cause**. Un mot de passe refusé produit exactement le même
chronomètre qu'un pare-feu fermé.

**3. La sonde externe mesurait le même cycle.** En parallèle, depuis le poste :

```
[21:15:05] /health HTTP 000 en 45.0s   ← le conteneur démarre : pas de réponse
[21:15:50] /health HTTP 503 en 14.8s   ← il vient de mourir
[21:16:05] /health HTTP 503 en  0.6s   ← plus rien derrière
```

Le `000` puis le `503` sont les deux faces d'un même cycle, vues de l'extérieur. C'est la
lecture annoncée dans la note des tests : **timeout lent = ça chauffe, `503` immédiat = il
n'y a personne.**

### La leçon de méthode

Trois écrans ont été consultés avant d'obtenir cette trace. Elle n'est apparue qu'après
avoir **activé la journalisation applicative** — le geste le moins spectaculaire, et le
seul qui ait produit une réponse.

Tout ce qui a été fait avant (arithmétique des délais, inspection du pare-feu, log de
plateforme) a permis d'**écarter** des hypothèses, jamais d'en confirmer une. La cause
n'apparaît que là où le code la nomme. `server.py`, `postgres_conn.py` et le commit
`1c9e044` sont écrits pour ça : la trace pointe la ligne exacte, `short_term.py:47`.

### Les trois causes possibles d'un mot de passe refusé

Le message dit que le mot de passe **arrivé jusqu'à PostgreSQL** n'est pas le bon — pas
que celui qu'on a tapé est faux. Nuance : ce qui compte est la valeur après passage par
l'URL et par les app settings.

| Cause | Comment la reconnaître |
|---|---|
| **Un espace ou un saut de ligne** collé en fin de valeur | Invisible à l'écran. La plus fréquente |
| **Un caractère spécial non URL-encodé** (`%`, `+`, `/`, `:`) | Le mot de passe contient autre chose que lettres, chiffres, `-`, `_` |
| Une copie partielle | Le mot de passe ne s'affiche nulle part : il a été retapé de mémoire |

Un `@` en trop est **exclu** : l'hôte a été correctement résolu en `23.101.64.194`, donc
la coupure `mot de passe @ hôte` s'est faite au bon endroit.

### Vérifier la valeur réellement appliquée — Kudu `/api/settings`

```
https://appwebsablvelmo-fuenh6eag9f8fsc9.scm.westeurope-01.azurewebsites.net/api/settings
```

Affiche les app settings **tels que le conteneur les reçoit**, espace final compris — ce
que l'écran d'édition du portail ne montre pas.

⚠️ **Secrets en clair** (`DATABASE_URL`, `API_KEY`, `LLM_INFERENCE_API_KEY`). À ne pas
capturer pour un livrable, à ne pas ouvrir en partage d'écran.

### Le test qui a tranché — et pourquoi il fallait le faire hors d'Azure

La chaîne complète, mot de passe compris, rejouée depuis le poste :

```bash
psql "postgresql://velmoadmin:MOTDEPASSE@pgsablvelmo.postgres.database.azure.com:5432/velmo-agent?sslmode=require" \
  -c "SELECT current_user, current_database(), version();"
```

```
psql: erreur : la connexion au serveur sur « pgsablvelmo.postgres.database.azure.com »
(23.101.64.194), port 5432 a échoué :
FATAL:  password authentication failed for user "velmoadmin"
```

**La même erreur, hors d'Azure.** Dix secondes, et trois hypothèses tombent d'un coup :

| Hypothèse | Verdict |
|---|---|
| Un espace collé dans l'app setting | ❌ Le poste ne passe pas par les app settings |
| Un caractère spécial mal encodé | ❌ Le mot de passe ne contenait que lettres, chiffres, `_` et `-` |
| Un problème propre à App Service | ❌ Le refus vient du serveur, pas d'Azure |

Reste une seule explication : **le mot de passe n'était pas celui du serveur.**

### L'origine réelle — un secret créé et jamais fixé

Le mot de passe avait pourtant été noté sur papier puis retapé. L'écart tient
vraisemblablement à un caractère — une casse, un séparateur — dans une chaîne de type
`psql_DB_4-3v3R`, où rien ne saute aux yeux. **Invérifiable** : PostgreSQL ne stocke qu'un
hachage, Azure ne peut donc ni afficher le mot de passe ni signaler qu'il s'en fallait
d'une lettre. D'où la réinitialisation plutôt que la chasse.

**Ce n'est pas un écart entre le local et Azure.** Confusion possible, et à écarter :
`compose.yaml:17-19` et `:46` utilisent `agent` / `agent` / base `agent` sur un service
Docker nommé `postgres`. `velmoadmin` n'existe **que** sur le serveur Azure, créé dans le
formulaire du bloc 3. Les deux mondes n'ont aucun compte en commun — et la chaîne locale
ne pourrait pas fonctionner en ligne, `@postgres:5432` étant un nom de service Docker.

La vraie origine est ailleurs : **ce secret n'a jamais existé sous une forme relisible.**
Il a vécu dans un formulaire, dans le hachage du serveur et sur une feuille. Le seul usage
qu'il a eu au bloc 3 était un prompt interactif de `psql` — un endroit où l'on tape sans
rien fixer. Deux heures plus tard, la valeur reconstituée n'était plus la bonne.

**Ce que ça justifie pour le bloc 7.** `API_KEY` et `LLM_INFERENCE_API_KEY` ont le même
statut, à une différence près : eux restent **relisibles** dans les app settings, alors
qu'Azure ne propose pour le mot de passe PostgreSQL que « réinitialiser ». Un coffre de
secrets n'est pas qu'une bonne pratique de confidentialité — c'est l'endroit unique où un
secret **se relit** au lieu de se reconstituer. Cet incident en est la démonstration.

### La correction

Serveur `pgsablvelmo` → **`Réinitialiser le mot de passe`** (barre du haut de la Vue
d'ensemble). Règles Azure : 8 à 128 caractères, au moins **trois** des quatre catégories
(majuscules, minuscules, chiffres, spéciaux), et interdiction de contenir le nom du
compte.

Choix retenu : **lettres et chiffres uniquement**. Trois catégories, règle satisfaite, et
plus rien à URL-encoder ni à confondre à la relecture — le conseil du bloc 3, appliqué
cette fois.

Aucun effet de bord : la base, le schéma, l'extension pgvector et les règles de pare-feu
restent en place. Seul le compte change de secret.

---

## ✅ Résolution — la bascule fonctionne

Nouveau mot de passe, `DATABASE_URL` recomposée, **un seul** redémarrage.

### Sondes externes

```
[21:25:32] /health HTTP 200 en 47.47s   ← démarrage à froid, complet
[21:26:19] /health HTTP 200 en  0.34s   ← à chaud
[21:26:21] /health HTTP 200 en  0.27s
/ready → {"ready":true}   HTTP 200 en 0.29s
```

**Les 47 s sont les mêmes qu'avant l'incident** — c'est le lifespan qui prend ce temps.
La différence n'est pas la durée : c'est qu'il se termine par un port ouvert au lieu d'un
`exit code 3`.

`{"ready":true}` est la preuve forte : `server.py:135` a construit le graphe **en
entier**, ce qui inclut `get_checkpointer()` → `get_postgres_pool()` → `pool.open()`.

### Journal du conteneur

```
19:25:56 INFO:     Started server process [1]
19:25:56 INFO:     Waiting for application startup.
19:26:10 INFO  support_agent.memory.postgres_conn | Postgres memory backend ready (schema=agent_state)
19:26:15 INFO  langgraph.store.postgres.base | Starting store TTL sweeper with interval 60.0 minutes
19:26:17 INFO  support_agent.server | Agent warmed up in 21.04 s; ready to serve.
19:26:17 INFO:     Application startup complete.
19:26:17 INFO:     Uvicorn running on http://0.0.0.0:8000 (Press CTRL+C to quit)
19:26:18 INFO:     169.254.129.1:25665 - "GET /ready HTTP/1.1" 200 OK
19:26:19 INFO:     169.254.129.1:25669 - "GET /health HTTP/1.1" 200 OK
```

| Ligne | Ce qu'elle prouve |
|---|---|
| `Postgres memory backend ready (schema=agent_state)` | **Preuve 1 du protocole, obtenue.** `postgres_conn.py:60` n'est émis qu'**après** `CREATE EXTENSION IF NOT EXISTS vector` et `CREATE SCHEMA IF NOT EXISTS agent_state` : connexion TLS établie, pare-feu traversé, droits de création confirmés |
| `Starting store TTL sweeper` | **Le `PostgresStore` est actif.** `long_term.py:117-119` ne démarre ce balayeur que dans la branche `postgres`, et uniquement après `store.setup()` — donc les tables existent. `MEMORY_TTL_DAYS=365` est armé |
| `Agent warmed up in 21.04 s` | Le lifespan est allé au bout : embeddings, FAQ, checkpointer **et** store |
| `Uvicorn running on 0.0.0.0:8000` | Le port s'ouvre — exactement la ligne qui manquait à chaque `exit code 3` |
| `GET /health … 200 OK` à 19:26:19 | Ce sont **les sondes lancées depuis le poste** à 21:26:19 (UTC+2). La corrélation entre la mesure externe et le vécu du conteneur est directe |

### Les trois cycles, superposés

| Heure (UTC) | Issue |
|---|---|
| 19:14:06 → 19:14:46 | ❌ Échec — 5 refus d'authentification, `PoolTimeout` après 30 s |
| 19:15:21 → 19:15:59 | ❌ Échec — **identique à la seconde près** |
| **19:25:56 → 19:26:17** | ✅ **Succès** — `backend ready` à +14 s, sweeper à +19 s, `warmed up in 21.04 s` |

Les deux échecs sont superposables : même nombre de tentatives, même *backoff* (3, 4, 6,
11 s), même durée totale. **Un échec déterministe, pas un aléa réseau** — ce qui, en soi,
désignait déjà une cause de configuration plutôt qu'un problème d'infrastructure.

**La bascule est faite.** Restent les preuves R2 et R3, qui sont l'objet même du bloc.

---

## ✅ Preuve 1 — la structure en base

```bash
psql "postgresql://velmoadmin:MOTDEPASSE@pgsablvelmo.postgres.database.azure.com:5432/velmo-agent?sslmode=require" \
  -c "SELECT current_user, current_database();" -c "\dt agent_state.*"
```

```
 current_user | current_database
--------------+------------------
 velmoadmin   | velmo-agent

   Schéma    |          Nom          | Type  | Propriétaire
-------------+-----------------------+-------+--------------
 agent_state | checkpoint_blobs      | table | velmoadmin
 agent_state | checkpoint_migrations | table | velmoadmin
 agent_state | checkpoint_writes     | table | velmoadmin
 agent_state | checkpoints           | table | velmoadmin
 agent_state | store                 | table | velmoadmin
 agent_state | store_migrations      | table | velmoadmin
 agent_state | store_vectors         | table | velmoadmin
 agent_state | vector_migrations     | table | velmoadmin
```

**Huit tables, créées par le code**, sans migration lancée à la main :

| Tables | Mémoire | Clé |
|---|---|---|
| `checkpoints`, `checkpoint_blobs`, `checkpoint_writes`, `checkpoint_migrations` | Mémoire de travail | `thread_id` |
| `store`, `store_vectors`, `store_migrations`, `vector_migrations` | Mémoire longue + index pgvector | `user_id` |

`store_vectors` est ce qui rend la recherche sémantique possible en base : c'est là que
vivent les embeddings, d'où le `CREATE EXTENSION vector` du bloc 3.

---

## ✅ Preuve 2 — l'écriture, et ce qu'elle range où

```bash
curl -sN -X POST "$BASE/chat" -H "Content-Type: application/json" -H "X-API-Key: $API_KEY" \
  -d '{"message":"Retiens que ma couleur préférée est le vert.","user_id":"sabine","thread_id":"t1"}'
```

```
data: {"type": "chunk", "text": "C'est noté : votre couleur préférée est le vert."}
data: {"type": "done"}
--- HTTP 200 en 6.36s ---
```

Contrôle immédiat en base :

```sql
SELECT prefix, key, value FROM agent_state.store;
```

```
       prefix       |                 key                  |                      value
--------------------+--------------------------------------+--------------------------------------------------
 memories.sabine    | 76cf7b3d-86da-4043-a872-7572b637ceaa | {"text": "La couleur préférée du client est le vert."}
 episode_candidates | t1                                   | {"resolved": true, "thread_id": "t1", "updated_at": "…"}
```

**Deux enseignements dans ces deux lignes :**

1. **`prefix = memories.sabine`** — le namespace `("memories", user_id)` de
   `long_term.py:46` est **visible dans la donnée**, pas seulement dans le code.
   L'isolation R3 n'est pas une promesse du modèle : c'est une clé de rangement.
2. **`episode_candidates / t1`** — la mémoire **épisodique** a flagué le fil pour
   distillation ultérieure (`builder.py:16`). Elle ne se déclenchera qu'après 30 minutes
   de silence (`episodic_idle_minutes`). Une base vide de la table `episodes` juste après
   un test n'est donc **pas** une anomalie.

⚠️ Le modèle a reformulé : « ma couleur préférée » → « La couleur préférée **du client** ».
`save_memory` stocke ce que le modèle décide d'écrire, pas la phrase brute.

---

## ✅ Preuve 3 — l'isolation (R3)

```bash
curl -sN -X POST "$BASE/chat" … \
  -d '{"message":"Quelle est ma couleur préférée ?","user_id":"autre-client","thread_id":"t3"}'
```

```
data: {"type": "chunk", "text": "Je n'ai aucune information enregistrée sur votre couleur préférée."}
--- HTTP 200 en 5.51s ---
```

Même question, même instant, **même base** — mais `user_id=autre-client` lit le namespace
`("memories", "autre-client")`, qui est vide. Le fait de `sabine` est physiquement présent
dans la même table et reste invisible.

**C'est cette requête qui donne son sens à la suivante.** Sans elle, une mémoire globale
partagée entre tous les clients passerait le test R2 sans qu'on le voie.

---

## ✅ Preuve 4 — la persistance (R2)

Redémarrage de la Web App, puis :

```bash
curl -sN -X POST "$BASE/chat" … \
  -d '{"message":"Quelle est ma couleur préférée ?","user_id":"sabine","thread_id":"t2"}'
```

```
data: {"type": "chunk", "text": "Votre couleur préférée est le **vert**. 💚"}
--- HTTP 200 en 5.16s ---
```

`thread_id=t2` n'a **jamais existé** : aucun historique de conversation à relire. La
réponse ne peut venir que du *store*, donc de la base.

### La chronologie qui fait la preuve

Une réserve avait été posée : la sonde avait trouvé `/ready` en 0,78 s, donc sans capturer
le redémarrage. Le journal la lève.

| Heure (UTC) | Événement |
|---|---|
| 19:36:37 | `POST /chat` — écriture du fait (`t1`) |
| 19:37:10 | `POST /chat` — R3 (`autre-client`) |
| **19:39:50** | **`Started server process [1]`** — processus neuf |
| 19:40:09 | `Postgres memory backend ready (schema=agent_state)` |
| 19:40:17 | `Agent warmed up in 26.94 s; ready to serve.` |
| **19:40:40** | **`[Previous Container] Finished server process [1]`** — l'ancien s'arrête |
| 19:41:31 | `POST /chat` → **« le vert »** |

La réponse a été servie par un processus démarré **1 min 41 s plus tôt**, alors que
l'ancien était arrêté depuis **51 secondes**. Aucune mémoire vive n'a traversé.

### Le mécanisme qui explique l'absence de coupure

**App Service ne fait pas arrêt-puis-démarrage : il fait un chevauchement.** Le nouveau
conteneur démarre, atteint `ready` (19:40:17), et **seulement ensuite** l'ancien est
arrêté (19:40:40).

C'est pourquoi la sonde externe n'a jamais vu de `503` : il n'y a pas eu d'interruption de
service. Le préfixe **`[Previous Container]`** dans le journal est la signature de ce
recouvrement — et c'est lui qui atteste qu'il y a bien eu deux processus distincts.

Conséquence pratique : **on ne peut pas prouver un redémarrage par une sonde HTTP.** Il
faut le journal. Une mesure externe qui ne montre aucune coupure ne prouve pas qu'il ne
s'est rien passé.

---

## ✅ Bloc 5 terminé

| Preuve attendue | Obtenue |
|---|---|
| Le backend Postgres s'initialise | ✅ `Postgres memory backend ready (schema=agent_state)` |
| Les tables existent | ✅ 8 tables dans `agent_state`, créées par le code |
| **R2 — persistance inter-session** | ✅ `thread_id` neuf après redémarrage → « le vert » |
| **R3 — isolation par utilisateur** | ✅ autre `user_id` → « aucune information enregistrée » |
| Le cloisonnement est structurel | ✅ `prefix = memories.sabine` en base |

**Livrables du brief obtenus** : R2 (mémoire persistante) et R3 (mémoire isolée par
client) — les deux exigences que le bloc 4 laissait explicitement ouvertes.

---

## Ce que ce bloc laisse en dette — la sécurité réseau de la base

Constat de fin de session : le serveur PostgreSQL est en **accès public**, et le mot de
passe est la seule barrière.

**Et la case du bloc 3 est plus large qu'elle n'en a l'air.** « Autoriser l'accès public à
partir d'un service Azure » se traduit par une règle `0.0.0.0 - 0.0.0.0` qui autorise
**tout service Azure — y compris ceux d'autres abonnements que le sien.** N'importe quelle
VM de n'importe quel client Azure peut tenter de s'authentifier. Ce n'est pas « mes
services Azure ».

### Les quatre niveaux, par coût croissant

| # | Mesure | Coût | Ce que ça change |
|---|---|---|---|
| 1 | **Restreindre aux IP sortantes de la Web App** (`Réseau` → `Adresses IP sortantes`), puis **décocher** « services Azure » | 5 min, gratuit | De « tout Azure » à 4-5 adresses. ⚠️ Ces IP changent si on change de tier de plan |
| 2 | **Key Vault** (bloc 7) | Prévu au plan | Protège le **secret**, pas le réseau : l'accès se donne par identité au lieu d'être lisible dans les app settings |
| 3 | **Private Endpoint / VNet** | Refaire le bloc 1 | Plus d'adresse publique du tout. Mais restreindre la sortie réseau ranime le piège `tiktoken` (encodage téléchargé au premier appel) — il faudrait le pré-embarquer dans l'image |
| 4 | **Authentification Microsoft Entra + identité managée** | Modifier le code | **Plus de mot de passe du tout.** `postgres_conn.py` passe aujourd'hui une URL avec mot de passe à `ConnectionPool` ; il faudrait obtenir et rafraîchir un jeton |

### ✅ Le geste 1, fait — et l'aller-retour qu'il a coûté

**Étape A — relever les adresses.** Web App → `Réseau` → `Configuration du trafic
sortant` → **`Adresses IPv4 sortantes`** : **32 adresses**.

Elles ne sont pas un signe d'intrusion, et la question mérite d'être posée. Une Web App ne
possède pas d'adresse sortante propre : elle tourne sur une **unité d'échelle** — un parc
de machines mutualisé — et sort par l'une quelconque des adresses de ce parc, selon
l'instance qui l'exécute à cet instant. Les 32 sont le pool entier.

Deux conséquences :

- Il faut autoriser **toutes** ces adresses, pas celle du moment. Sinon ça marche, puis ça
  casse le jour où Azure déplace l'instance — avec un timeout muet, plusieurs jours après
  la cause.
- Elles sont **partagées avec les autres applications de la même unité**. On ne passe donc
  pas de « tout Azure » à « moi seule », mais à « les App Services de cette unité en West
  Europe ». Trois ordres de grandeur de moins, pas un cloisonnement.

**Étape B — trois règles au lieu de trente-deux.** Les 32 adresses tombent dans trois
plages contiguës :

| Nom | Début | Fin |
|---|---|---|
| `AppService-WestEurope-1` | `20.31.120.0` | `20.31.127.255` |
| `AppService-WestEurope-2` | `20.126.201.0` | `20.126.207.255` |
| `AppService-WestEurope-3` | `20.105.216.0` | `20.105.243.255` |

Plus large que le strict nécessaire — et c'est voulu : la marge absorbe les variations du
pool, donc le piège décrit à l'étape A.

⚠️ **Conserver la règle `ClientIPAddress_…`** : c'est le canal de diagnostic depuis le
poste, indépendant de l'application. Il a servi le soir même.

**Étape C** — décocher « Autoriser l'accès public à partir d'un service Azure ».
**Étape D** — `Enregistrer`, redémarrer la Web App, **et vérifier dans le journal**.

### Le premier essai a échoué — et l'erreur de raisonnement qui a suivi

```
20:04:13 (19:56:13 UTC)  Started server process [1]
19:57:06  psycopg_pool.PoolTimeout: pool initialization incomplete after 30.0 sec
19:57:06  ERROR:    Application startup failed. Exiting.
```

**Aucun `password authentication failed` cette fois.** Un timeout *muet* : le serveur n'a
pas répondu du tout. Le contraste avec l'incident de 19:14 est exactement la distinction
qui a servi toute la soirée — **un refus d'authentification prouve qu'on atteint le
serveur, un silence prouve qu'on ne l'atteint pas.**

**L'erreur commise ensuite :** conclure que « filtrer par IP ne peut pas fonctionner pour
App Service → PaaS de la même région, le trafic empruntant le réseau interne d'Azure ».
Explication structurelle, plausible — et **non vérifiée**.

**Ce qui l'a corrigée :** relire l'écran de pare-feu. La règle
`AppService-WestEurope-2` **n'y était pas**. Or cinq des trente-deux adresses
(`20.126.201.182`, `20.126.205.191`, `20.126.207.22`, `20.126.207.109`,
`20.126.207.205`) tombaient précisément dans la plage manquante.

C'est le même travers que la piste 1 (l'arithmétique des 50 s), à quelques heures
d'intervalle : **préférer une explication savante à une vérification simple.** La règle
manquante était visible à l'écran ; l'hypothèse du routage interne ne l'était nulle part.

### Le second essai — le filtrage fonctionne

Règle 2 rétablie, case décochée, redémarrage :

```
20:04:49 INFO:     Started server process [1]
20:05:09 INFO  support_agent.memory.postgres_conn | Postgres memory backend ready (schema=agent_state)
20:05:14 INFO  langgraph.store.postgres.base | Starting store TTL sweeper …
20:05:16 INFO  support_agent.server | Agent warmed up in 26.55 s; ready to serve.
20:05:39 [Previous Container] INFO:     Finished server process [1]
20:05:59 INFO:     169.254.129.1:35429 - "GET /ready HTTP/1.1" 200 OK
```

**La ligne de 20:05:09 est décisive :** un pool **neuf**, ouvert par un processus
**neuf**, a traversé le pare-feu restreint. Et les sondes après 20:05:39 le confirment de
l'extérieur, puisqu'il ne reste plus qu'un conteneur.

**Résultat : la base n'est plus joignable depuis « tout Azure », mais uniquement depuis
les plages App Service West Europe et le poste de travail.**

### Le piège qui a failli fausser les deux essais — le chevauchement

App Service **ne fait pas arrêt-puis-démarrage** : le nouveau conteneur démarre, atteint
`ready`, et **seulement ensuite** l'ancien est arrêté.

Or **un changement de règles de pare-feu ne coupe pas les connexions déjà établies** — il
ne filtre que les nouvelles. Un ancien conteneur au pool ouvert continue donc de servir
des requêtes correctes, pare-feu restreint ou non.

Au premier essai, une sonde a répondu « le vert » à 19:56:50 : c'était l'ancien conteneur,
qui ne s'est arrêté qu'à 19:57:34. **Cette réponse ne prouvait rien.**

**Règle à retenir : on ne valide pas un changement de réseau par une sonde HTTP.** Il faut
la ligne `Postgres memory backend ready` d'un processus dont le `Started server process`
est postérieur au changement. Le préfixe `[Previous Container]` dans le journal est la
signature du recouvrement.

### Ce qui est retenu

Le **1** est **fait et vérifié**. Le **2** reste le bloc 7 comme prévu. Les **3** et **4**
sont hors scope de ce déploiement et doivent figurer au dossier comme choix assumés, avec
leur raison.

Montrer qu'un compromis a été vu et tranché sciemment vaut mieux qu'un déploiement
parfait sans justification.

### La dette qui reste, et qui est plus proche des vraies fuites

Le pare-feu limite **qui peut se connecter à la base**. Il ne dit rien de **qui peut lire
la mémoire de qui** via l'API — et c'est ce second point qui correspond au motif le plus
courant des fuites de données par API.

`agent_client.py:11-12` le nomme déjà :

> *« the body-borne `user_id` is still unproven, and it is the SERVER that must close »*

Concrètement : le `user_id` arrive **dans le corps de la requête**. `X-API-Key` authentifie
le **service appelant**, pas le client final. Les tests R3 de ce soir en sont la
démonstration : il a suffi d'écrire `"user_id":"autre-client"` puis `"user_id":"sabine"`
pour lire l'une ou l'autre mémoire.

**Ce que R3 prouve donc exactement :** les mémoires ne se mélangent pas **par accident**.
Pas qu'un appelant détenant la clé ne puisse pas lire celle d'un autre.

La direction de résolution est écrite dans le code — « c'est au serveur de fermer », soit
dériver le `user_id` d'une session authentifiée plutôt que de le lire dans le corps. Hors
scope ici, mais à qualifier ainsi au dossier : dire où s'arrête ce qu'on a prouvé vaut
mieux qu'un « R3 ✅ » sans réserve.

---

## Décision de fin de bloc — `Always On` laissé désactivé

Sans `Always On`, App Service **endort le conteneur** après une vingtaine de minutes sans
trafic. La requête suivante paie le réveil complet — ~45 s, comme observé au bloc 4 et
plusieurs fois ce soir.

**Choix : laisser l'extinction**, par sobriété. Nuance à connaître pour le dossier : le
plan B1 est une instance **réservée**, facturée à l'heure, endormie ou non — le gain n'est
donc pas financier, et le gain énergétique se limite au CPU et à la RAM que le conteneur
ne consomme pas pendant sa sieste. Réel, mais modeste.

**Conséquence à anticiper avant toute démonstration :** réveiller l'agent par un
`curl …/ready` une minute avant, sinon le premier visiteur attend 45 secondes devant une
page vide.

**Et une incompatibilité à connaître** : toute surveillance périodique (contrôle
d'intégrité, test de disponibilité, cron externe) **empêche l'extinction**, puisqu'elle
génère du trafic. Surveiller, c'est solliciter. Les deux objectifs — sobriété et
surveillance continue — ne se cumulent pas.

---

## Pour reproduire ce bloc

1. Web App → `Variables d'environnement` → `Paramètres de l'application`.
2. `PERSISTENCE_BACKEND` : `memory` → **`postgres`**.
3. Ajouter `DATABASE_URL` (chaîne du bloc 3, `?sslmode=require` compris).
4. *(Facultatif)* Ajouter `DATABASE_SCHEMA=agent_state` — défaut du code, posé pour la
   lisibilité.
5. `Appliquer` → `Confirmer`. **Une seule** tentative, puis attendre.
6. **Activer la journalisation applicative avant de diagnostiquer quoi que ce soit** :
   `Supervision` → `Journaux App Service` → `Journal des applications` = `Système de
   fichiers`, quota `35`, conservation `7` → `Enregistrer`. *(Pas de sélecteur de niveau
   sur Linux : c'est un réglage Windows.)*
7. Vérifier dans le `Flux de journal` : `Postgres memory backend ready (schema=…)`, puis
   `Agent warmed up in … s`.
8. Écrire un fait (`user_id=X`, `thread_id=t1`), **redémarrer**, relire avec le **même
   `user_id` et un `thread_id` neuf** → R2.
9. Reposer la question avec un **autre `user_id`** → R3.
10. Confirmer dans le journal la ligne `Started server process [1]` **postérieure** à
    l'écriture — sans elle, R2 n'est pas prouvé.

**Bloc suivant :** la Web App `client` (Chainlit), §10 du tuto. À savoir avant de
commencer : `app.py:57` envoie `user_id=DEMO_USER_ID`, une constante. Le front permettra
donc de **rejouer R2** dans le navigateur (recharger la page ouvre une session, donc un
`thread_id`, neuf) mais **jamais R3** — et c'est voulu : `agent_client.py:11-12` rappelle
qu'un client capable de choisir son `user_id` serait exactement la faille que R3 écarte.


### Le tableau des suspects, à la clôture de l'enquête

| Suspect | Verdict |
|---|---|
| **Mot de passe / URL-encodage** | ✅ **CONFIRMÉ** — `FATAL: password authentication failed` |
| Chemin réseau Web App → base | ❌ Écarté : le serveur a répondu depuis `23.101.64.194:5432` |
| Pare-feu PostgreSQL | ❌ Écarté sur pièce (case « services Azure » cochée) |
| Nom de la base | ❌ Écarté : on n'atteint jamais le choix de base, l'auth échoue avant |
| Droits sur `CREATE EXTENSION` / `CREATE SCHEMA` | ❌ Écarté : ces requêtes ne sont jamais exécutées |
| Image, ingress, service d'IA | ❌ Écartés dès les blocs 1 à 4 |

Le commit `1c9e044` (configuration validée avant tout I/O) a tenu sa promesse : l'erreur
est nommée en clair, avec la ligne exacte — `short_term.py:47`.

