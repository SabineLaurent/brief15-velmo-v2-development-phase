# 2026-08-24 — Les commandes de test du déploiement, regroupées

**Objet :** rassembler en un seul endroit toutes les commandes de vérification utilisées
depuis le bloc 1, avec **ce que chacune prouve** et **comment lire sa sortie**.
**Pourquoi cette note :** elles sont dispersées dans les journaux de blocs, mêlées au
récit. Ici elles sont utilisables telles quelles, sans relire le contexte.

> **Test ≠ déploiement.** Aucune commande de ce fichier ne modifie quoi que ce soit sur
> Azure. Toutes sont rejouables autant de fois qu'on veut, dans n'importe quel ordre.

---

## Les variables, à poser une fois par session de terminal

```bash
# Bloc 2 — service d'IA
export AZ_ENDPOINT="https://slaurentext-5359-resource.services.ai.azure.com/openai/v1"
export AZ_KEY="…"                       # Foundry → Clés et points de terminaison

# Bloc 3 — base de données
export PGHOST="pgsablvelmo.postgres.database.azure.com"
export PGUSER="velmoadmin"
export PGDATABASE="velmo-agent"

# Bloc 4 — Web App agent
export BASE="https://appwebsablvelmo-fuenh6eag9f8fsc9.westeurope-01.azurewebsites.net"
export API_KEY="…"                      # la valeur de l'app setting API_KEY
```

⚠️ **Ne jamais écrire ces `export` dans un fichier versionné.** Les valeurs de `AZ_KEY`
et `API_KEY` sont des secrets — c'est précisément ce que le bloc 7 (Key Vault) déplacera.

---

## Bloc 1 — les images Docker

### Le démon est-il levé ? (au lieu de deviner)

```bash
until docker version --format '{{.Server.Version}}' >/dev/null 2>&1; do sleep 2; done
echo "Démon prêt : $(docker version --format '{{.Server.Version}}')"
```

Une boucle plutôt qu'un `sleep 30` : elle rend la main dès que c'est vrai, et n'échoue
jamais trop tôt. Ce motif ressert partout (attendre un redémarrage, une création).

### Le smoke test des deux images

```bash
make docker-smoke PLATFORM=linux/amd64
```

| Ce qu'il vérifie | Ce qu'il écarte |
|---|---|
| Le client ne contient ni `langchain` ni `langgraph` | Une régression du découplage : l'UI ne porte pas le cerveau |
| L'agent démarre, s'échauffe, `/health` + `/ready` à `200` | Un défaut d'image, **nommé en clair ici** plutôt que déguisé en « HTTP pings » dans un log Azure |
| `POST /chat` sans clé → `401` | Une porte ouverte sur le crédit LLM |

Sans aucun secret : le script sert lui-même un stub d'embeddings.

### L'architecture de l'image — sans faire confiance

```bash
docker image inspect support-agent:dev  --format '{{.Os}}/{{.Architecture}}'
docker image inspect client-chainlit:dev --format '{{.Os}}/{{.Architecture}}'
```

Attendu : `linux/amd64`. Sur un Mac Apple Silicon (`uname -m` → `arm64`), une image
construite sans `--platform` sort en `arm64` et produit sur Azure un `exec format error`
que le portail affiche sous la forme trompeuse *« Container didn't respond to HTTP pings
on port: 8000 »*. **C'est la vérification la moins chère du déploiement.**

---

## Bloc 2 — le service d'IA

```bash
# a) le chat
curl -s "$AZ_ENDPOINT/chat/completions" \
  -H "Content-Type: application/json" -H "Authorization: Bearer $AZ_KEY" \
  -d '{"model":"<NOM-DU-DEPLOIEMENT-CHAT>","messages":[{"role":"user","content":"dis bonjour"}]}' \
  | head -c 400

# b) les embeddings — celui qui conditionne le DÉMARRAGE du conteneur
curl -s "$AZ_ENDPOINT/embeddings" \
  -H "Content-Type: application/json" -H "Authorization: Bearer $AZ_KEY" \
  -d '{"model":"<NOM-DU-DEPLOIEMENT-EMBEDDINGS>","input":"test"}' \
  | head -c 200
```

| Réponse | Signification |
|---|---|
| JSON avec `choices` / `data` | ✅ endpoint, clé et déploiement corrects |
| `401` | Clé fausse, ou `Bearer` refusé → réessayer avec `-H "api-key: $AZ_KEY"` |
| `404` | Le **nom du déploiement** est faux (ce n'est pas le nom du modèle), ou le `/openai/v1` manque dans l'endpoint |

**Le test (b) est le plus important des deux** : le lifespan de l'agent indexe la FAQ au
démarrage. Un endpoint d'embeddings cassé ne donne pas une réponse dégradée — il donne un
conteneur qui ne démarre pas.

---

## Bloc 3 — la base de données

### Les quatre vérifications en une connexion

```bash
psql "host=$PGHOST port=5432 dbname=$PGDATABASE user=$PGUSER sslmode=require" \
  -c "SELECT version();" \
  -c "SHOW azure.extensions;" \
  -c "CREATE EXTENSION IF NOT EXISTS vector;" \
  -c "SELECT extname, extversion FROM pg_extension;"
```

Une seule connexion, donc **un seul prompt de mot de passe**.

| Commande | Ce qu'elle prouve |
|---|---|
| `SELECT version()` | La connexion passe → pare-feu **et** TLS OK |
| `SHOW azure.extensions` | `VECTOR` est en liste blanche (geste portail du bloc 3) |
| `CREATE EXTENSION` | pgvector s'installe réellement |
| `SELECT … pg_extension` | Il est bien là, avec sa version |

| Symptôme | Cause |
|---|---|
| `timeout` / pas de réponse | Pare-feu : la case « services Azure » ou l'IP du client |
| Erreur d'authentification | Mot de passe, ou `velmoadmin@pgsablvelmo` au lieu de `velmoadmin` |
| `extension "vector" is not allow-listed` | Le geste portail `azure.extensions` n'a pas été fait |

### Quelle IP le pare-feu voit-il ?

```bash
curl -s ifconfig.me
```

À comparer à la règle enregistrée dans `Mise en réseau`. L'IP d'une box 5G est
**dynamique** : elle change, et la règle du bloc 3 devient caduque sans prévenir.
Réparation en 10 s : serveur → `Mise en réseau` → `+ Ajouter l'adresse IPv4 actuelle` →
`Enregistrer`.

⚠️ **Piège relevé le 2026-08-24 :** cette commande a renvoyé une adresse **IPv6**
(`2a02:8440:…`). Or la règle ajoutée au bloc 3 est une règle **IPv4**. Si `psql` depuis le
poste échoue en timeout alors que la règle semble bonne, c'est peut-être que la connexion
sort en IPv6 et ne correspond à aucune règle. Pour forcer la comparaison en IPv4 :
`curl -s -4 ifconfig.me`.

---

## Bloc 4 — la Web App, les trois sondes

```bash
# 1. Liveness — le processus est-il vivant ?
curl -s -w '\nHTTP %{http_code} — %{time_total}s\n' "$BASE/health"

# 2. Readiness — le graphe est-il construit et la FAQ indexée ?
curl -s -w '\nHTTP %{http_code} — %{time_total}s\n' "$BASE/ready"

# 3. La porte est-elle fermée ? (doit ÉCHOUER)
curl -s -w '\nHTTP %{http_code}\n' -X POST "$BASE/chat" \
  -H 'Content-Type: application/json' -H 'X-API-Key: mauvaise-cle' \
  -d '{"message":"bonjour"}'
```

| Sonde | Ce qu'elle prouve |
|---|---|
| `/health` → `200` | L'image tourne (donc **amd64**), uvicorn a ouvert le port, `WEBSITES_PORT=8000` route bien |
| `/ready` → `{"ready":true}` | **La preuve qui compte.** Le graphe est construit *dans le lifespan*, ce qui sonde les embeddings : `true` = l'agent en ligne a réellement appelé Foundry et indexé la FAQ |
| `/chat` → `401` | `API_KEY` est posée et `API_ALLOW_UNAUTHENTICATED=false` respecté |

**Les deux ne sont pas interchangeables** (`server.py:149-158`) : *liveness* en échec veut
dire « redémarre-moi », *readiness* en échec veut dire « ne m'envoie pas de trafic ». Les
inverser dans les hooks App Service fait redémarrer en boucle un conteneur qui chauffe.

### Attendre un redémarrage sans le juger trop tôt

```bash
curl -s --retry 12 --retry-delay 15 --retry-all-errors --max-time 60 \
  -w '\nHTTP %{http_code} en %{time_total}s\n' "$BASE/health"
```

`--retry-all-errors` réessaie aussi sur les codes HTTP d'erreur, pas seulement sur les
erreurs réseau : c'est ce qui absorbe le démarrage à froid sans surveiller à la main.

**Comment lire un échec — le temps de réponse tranche :**

| Sortie | Lecture |
|---|---|
| Pas de réponse, timeout après 30-45 s | Le conteneur **chauffe** : on mesure le réveil, pas une panne. Attendre |
| `503` **immédiat** (~0,1 s) | Le front-end Azure répond mais **il n'y a pas de conteneur derrière** : il ne démarre pas. Aller au `Flux de journal` |

C'est la distinction utile : un `503` rapide et un timeout lent ne disent pas du tout la
même chose. Le premier est une panne, le second de la patience à avoir.

---

## Bloc 5 — la mémoire persistante

### R2 — la persistance inter-session

```bash
# 1. Donner un fait — thread t1
curl -N -X POST "$BASE/chat" \
  -H "Content-Type: application/json" -H "X-API-Key: $API_KEY" \
  -d '{"message":"Retiens que ma couleur préférée est le vert.","user_id":"sabine","thread_id":"t1"}'

# 2. Portail → Vue d'ensemble → Redémarrer, puis attendre

# 3. Redemander — MÊME user_id, thread_id NEUF
curl -N -X POST "$BASE/chat" \
  -H "Content-Type: application/json" -H "X-API-Key: $API_KEY" \
  -d '{"message":"Quelle est ma couleur préférée ?","user_id":"sabine","thread_id":"t2"}'
```

`-N` désactive le tampon de `curl` : la réponse arrive en *streaming*, jeton par jeton.
Sans lui, on attend la fin sans rien voir.

**Le `thread_id` neuf n'est pas un détail** : rejouer sur `t1` interrogerait le
*checkpointer* (l'historique du fil). Le fil neuf force le passage par le *store*, donc
par la mémoire longue. C'est la différence entre « la conversation a été rechargée » et
« l'agent sait quelque chose de ce client ».

### R3 — l'isolation, la contre-épreuve

```bash
curl -N -X POST "$BASE/chat" \
  -H "Content-Type: application/json" -H "X-API-Key: $API_KEY" \
  -d '{"message":"Quelle est ma couleur préférée ?","user_id":"autre-client","thread_id":"t3"}'
```

Attendu : **il ne sait pas.** Sans cette requête, R2 seul est ambigu — une mémoire globale
partagée répondrait « le vert » elle aussi. On confondrait « la mémoire marche » avec « la
mémoire fuit ».

### Départager les deux causes d'un « je ne sais pas »

```bash
psql "host=$PGHOST port=5432 dbname=$PGDATABASE user=$PGUSER sslmode=require" \
  -c "\dt agent_state.*" \
  -c "SELECT count(*) FROM agent_state.store;"
```

| Résultat | Cause |
|---|---|
| Table absente | La bascule a échoué — problème de déploiement |
| Table présente, vide | Le modèle n'a **pas appelé** `save_memory` (c'est un outil qu'il décide d'utiliser, pas un enregistrement automatique) — reformuler, ce n'est pas un problème de déploiement |

---

## Lire les journaux d'App Service — les URL Kudu

Les écrans `Supervision` → `Journaux` et `Journaux du serveur` concernent les journaux
**HTTP du serveur web**. Ils ne contiennent pas la sortie du conteneur, et sur App Service
**Linux** la journalisation applicative est **désactivée par défaut** : les deux écrans
restent vides tant qu'on ne l'a pas activée.

### Kudu, ou pourquoi il existe un deuxième site

Chaque App Service en héberge **deux** :

| | Le site | Kudu (SCM) |
|---|---|---|
| Adresse | `appwebsablvelmo-….westeurope-01.azurewebsites.net` | `appwebsablvelmo-….scm.westeurope-01.azurewebsites.net` |
| Qui y accède | Tout le monde | Le propriétaire, via sa session Azure |
| Ce qu'il sert | L'agent | La machine qui l'héberge |
| Vivant quand ? | Seulement si le conteneur démarre | **Toujours** |

Le nom vient du projet open-source `projectkudu/kudu`, à l'origine le moteur de
déploiement d'App Service (celui qui reçoit les `git push` et exécute les scripts de
build). Il a grossi jusqu'à devenir la console de service de la plateforme.

**Pourquoi c'est lui qu'on interroge quand rien ne marche :** Kudu tourne **à côté** du
conteneur, pas dedans. Un conteneur qui meurt au démarrage n'ouvre aucun port — le site
répond `503` et n'a rien à dire. Kudu, lui, vit dans le processus de la plateforme et
répond quand même. **C'est le seul endroit joignable quand l'application ne l'est plus**,
et c'est exactement la situation où on a besoin des logs.

| Chemin | Contenu |
|---|---|
| `/api/logs/docker` | La liste des fichiers de log du conteneur |
| `/api/vfs/LogFiles/…` | Le contenu de n'importe quel fichier sous `/home` |
| `/api/settings` | Les app settings **effectivement appliqués** — de quoi vérifier une faute de frappe sans repasser par l'écran d'édition |
| `/newui` ou `/` | Une interface web : explorateur de fichiers, console, variables d'environnement |

⚠️ **`/api/settings` affiche les secrets en clair** (`DATABASE_URL`, `API_KEY`,
`LLM_INFERENCE_API_KEY`). Utile pour diagnostiquer, à ne jamais capturer en image pour un
livrable ni ouvrir en partage d'écran.

### Le nom d'hôte du site de gestion (SCM)

Il se déduit de l'URL du site en insérant `.scm` **avant la région** :

| | |
|---|---|
| Site | `appwebsablvelmo-fuenh6eag9f8fsc9.westeurope-01.azurewebsites.net` |
| SCM | `appwebsablvelmo-fuenh6eag9f8fsc9.scm.westeurope-01.azurewebsites.net` |

⚠️ La forme courte `appwebsablvelmo.scm.azurewebsites.net` **ne résout pas** : avec les
noms d'hôte uniques, le suffixe aléatoire fait partie du nom SCM aussi.

### Les deux URL

```
# 1. Lister les fichiers de log Docker (JSON : taille, date, href)
https://<SCM>/api/logs/docker

# 2. Lire un fichier — suivre le href renvoyé
https://<SCM>/api/vfs/LogFiles/<AAAA_MM_JJ>_<machine>_docker.log
```

À ouvrir **dans le navigateur déjà connecté au portail** : c'est lui qui porte la session.
Depuis un terminal, la réponse est `401` — ce qui sert quand même à vérifier que l'hôte
existe :

```bash
curl -s -o /dev/null -m 15 -w 'HTTP %{http_code}\n' "https://<SCM>/api/logs/docker"
# HTTP 401 → l'hôte répond, il manque la session
# HTTP 000 → le nom d'hôte est faux
```

### Activer les journaux applicatifs (à faire une fois)

`Supervision` → `Journaux App Service` → `Journalisation d'application` = **`Système de
fichiers`**, niveau `Verbose` → `Enregistrer`.

Sans elle, le `*_docker.log` ne contient que les gestes de la **plateforme** (pull,
montage, sondes) — pas la trace Python. À activer de toute façon : le relevé du taux de
blocage des garde-fous (bloc 8) la suppose.

### Les deux écrans qui donnent un fait sans rien activer

| Écran | Ce qu'il donne |
|---|---|
| Web App → **`Instances`** | État de l'instance, **code de sortie** et durée : `Container exited with exit code 3 during startup after 50.6s` |
| Web App → **`Diagnostiquer et résoudre les problèmes`** → `Container Crash` | Les journaux de l'échec de démarrage, présentés |

**Réflexe : `Instances` d'abord.** Il transforme un `503` opaque en un fait daté et
chiffré, sans configuration préalable.

### Deux lignes à savoir reconnaître

| Ligne | Sens |
|---|---|
| `Site startup probe succeeded after N seconds` | Le démarrage a réussi — et **N est le temps d'échauffement de référence** |
| `Site is blocked due to multiple, consecutive cold start failures` | Après plusieurs échecs, App Service **refuse** les démarrages pendant un délai croissant (1 min, 2 min…). Les `Redémarrer` cliqués pendant cette fenêtre sont rejetés sans rien tenter — une tentative, puis on lit |

---

## Réveiller l'agent avant de le mesurer — `make wake`

`Always On` étant laissé désactivé (choix de sobriété, bloc 5), App Service **endort le
conteneur** après ~20 minutes sans trafic. Le premier appel paie alors le démarrage à
froid complet — ~45 s — et un `curl` ordinaire abandonne bien avant.

```bash
make wake ARGS=https://appwebsablvelmo-….azurewebsites.net
# ou, si AGENT_API_URL est déjà exportée :
make wake
```

```
→ https://appwebsablvelmo-fuenh6eag9f8fsc9.westeurope-01.azurewebsites.net

  Réveil — /ready (jusqu'à ~90 s à froid)
    ready   HTTP 200 — 0.349384s

  Trois mesures à chaud — /health
    health  HTTP 200 — 0.256012s
    health  HTTP 200 — 0.249974s
    health  HTTP 200 — 0.258813s
```

`scripts/wake.sh` fait deux choses en une, et c'est ce qui le rend utile :

| Appel | Rôle |
|---|---|
| `/ready` avec `--retry-all-errors` | **Réveille** — les retries absorbent le démarrage à froid sans surveiller à la main — et prouve que le graphe est construit et la FAQ indexée |
| Trois `/health` ensuite | **Mesurent** la latence réelle, à chaud : celle qu'un client verrait |

**À lancer une minute avant toute démonstration.** Sinon le premier visiteur attend
quarante-cinq secondes devant une page vide, et croira à une panne.

L'URL se donne en argument, ou via `AGENT_API_URL` — le script ne code aucune adresse en
dur, puisque le dépôt est livré.

---

## Le piège du redémarrage — pourquoi une sonde ne prouve pas un redémarrage

**App Service ne fait pas arrêt-puis-démarrage : il fait un chevauchement.** Le nouveau
conteneur démarre, atteint `ready`, et **seulement ensuite** l'ancien est arrêté.

```
20:04:49  Started server process [1]                 ← le nouveau démarre
20:05:16  Application startup complete.              ← il est prêt
20:05:39  [Previous Container] Finished server …     ← l'ancien meurt, 50 s plus tard
```

Pendant ces cinquante secondes, **les deux tournent**. Une sonde externe reçoit `200`
sans savoir lequel lui répond, et ne voit aucune coupure de service.

Deux conséquences qui ont failli fausser des conclusions le 2026-08-24 :

| Situation | Ce qu'on croit mesurer | Ce qu'on mesure vraiment |
|---|---|---|
| `/health` ou `/ready` à `200` juste après un `Redémarrer` | Le nouveau processus fonctionne | Peut-être encore l'ancien |
| Une requête qui réussit après un changement de **pare-feu** | Les nouvelles règles laissent passer | **Rien** : un pool déjà ouvert n'est pas coupé par un changement de règles, qui ne filtre que les connexions neuves |

**Règle : on valide un changement d'infrastructure par le journal, pas par une sonde.**
Ce qu'il faut y trouver, dans cet ordre :

```
INFO:     Started server process [1]                          ← postérieur au changement
INFO  …postgres_conn | Postgres memory backend ready (…)      ← un pool NEUF s'est ouvert
```

Le préfixe **`[Previous Container]`** dans le journal est la signature du recouvrement :
sa présence atteste qu'il y a bien eu deux processus distincts, et son `Finished server
process` donne l'instant à partir duquel les réponses ne peuvent plus venir que du neuf.

---

## Le réflexe transversal

Trois habitudes qui reviennent dans toutes ces commandes :

1. **Mesurer le temps** (`-w '%{time_total}'`). Il distingue une panne d'une attente, et
   fournit gratuitement la latence demandée par le brief.
2. **Tester ce qui doit échouer** (`/chat` sans clé, un autre `user_id`). Une preuve qui
   ne peut pas rater ne prouve rien : c'est la contre-épreuve qui fait la différence entre
   « ça marche » et « ça marche pour la bonne raison ».
3. **Attendre une condition, pas une durée** (`until … do sleep 2; done`,
   `--retry-all-errors`). Un `sleep` fixe est soit trop court, soit du temps perdu.

Et un contre-réflexe, appris le 2026-08-24 au bloc 5 : **une durée n'est une preuve que si
l'on sait ce qui se passe pendant.** Les ~50 s avant l'arrêt du conteneur semblaient
désigner un timeout réseau (`_CONNECT_TIMEOUT_S = 30.0`). En réalité
`pool.open(wait=True, timeout=30)` **réessaie en boucle** jusqu'à expiration : un mot de
passe refusé, une base absente et un silence réseau donnent tous les trois la même durée.
Le chronomètre ne discriminait rien — seule la ligne de log le fait.
