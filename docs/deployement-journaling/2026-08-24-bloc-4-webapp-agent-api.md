# 2026-08-24 — Bloc 4 : la Web App `agent-api` (conteneur, mémoire volatile)

**Objet :** mettre l'agent en ligne derrière une URL HTTPS publique, en tirant l'image du
bloc 1, en la branchant au service d'IA du bloc 2 — et **sans** brancher la base du
bloc 3.
**Correspond à :** §8 du tuto `docs/2026-08-24-tuto-deploiement-azure-portail.md`
(compétence 5 du brief — l'agent est joignable en ligne).
**Prérequis :** [bloc 1](2026-08-24-bloc-1-images-docker-acr.md),
[bloc 2](2026-08-24-bloc-2-foundry-service-ia.md) et
[bloc 3](2026-08-24-bloc-3-postgresql-pgvector.md) terminés.
**Statut :** ✅ **terminé** — `/health` et `/ready` répondent `200`, `/chat` refuse un
appel non authentifié. Un geste de robustesse reste optionnel (dernière section).

---

## Les valeurs de ce bloc

| Élément | Valeur |
|---|---|
| Plan App Service | `ASP-sablvelmo` — **Basic B1** (1,75 Gio), Linux, West Europe |
| Web App | `appwebsablvelmo` |
| **URL publique** | `https://appwebsablvelmo-fuenh6eag9f8fsc9.westeurope-01.azurewebsites.net` |
| Publier | **`Conteneur`** |
| Image | `acrsablvelmo.azurecr.io/support-agent:v1` |
| Nom du conteneur | `main` (défaut) |
| Port | `8000` |
| Auth registre | Informations d'identification de l'administrateur |
| Application Insights | **Non** |
| Mémoire | `PERSISTENCE_BACKEND=memory` — **volontairement volatile à ce stade** |

⚠️ **L'URL n'est pas `appwebsablvelmo.azurewebsites.net`.** Voir la section sur le nom
d'hôte unique : c'est cette URL longue qu'il faudra reporter dans `AGENT_API_URL` au
bloc 6, et elle ne se reconstruit pas de tête.

---

## Décision 1 — pourquoi `memory` alors que la base était prête depuis le bloc 3

C'est le cœur du cadrage suivi depuis le début : **un suspect à la fois.**

Si ce bloc échouait avec la base branchée, la cause pourrait être l'image (mauvaise
architecture), l'ingress (mauvais port), le provider d'IA (clé, endpoint, nom de
déploiement), les secrets, **ou** la base. Cinq pistes pour un symptôme unique : « la page
affiche Application Error ». En laissant `memory`, la liste tombe à quatre — et surtout,
si le bloc 5 (bascule sur `postgres`) casse, la cause est **nécessairement** la base,
puisque tout le reste vient d'être prouvé en ligne.

Ce découpage ne coûte rien : le geste du bloc 5 est **une seule variable à changer**. C'est
exactement ce pour quoi `persistence_backend` existe dans `config.py`.

### `PERSISTENCE_BACKEND=memory` n'est pas redondant — il est indispensable

`Dockerfile:58` pose `PERSISTENCE_BACKEND=sqlite` comme défaut de l'image. Sans cette
ligne explicite dans les app settings, l'agent écrirait dans un fichier SQLite posé sur le
système de fichiers **éphémère** du conteneur : perdu au premier redémarrage, avec
l'illusion d'une mémoire qui fonctionne. Un bug silencieux, et précisément celui que le
brief demande de corriger (exigence R2).

`memory` est honnête : la mémoire est volatile et on le sait. `sqlite` sur un conteneur
mentirait.

---

## Décision 2 — l'authentification au registre : admin credentials, pas identité managée

Deux options à l'onglet Conteneur, et la plus propre n'est pas la bonne ici.

| Option | Mécanisme | Pourquoi ce choix |
|---|---|---|
| **Admin credentials** ✅ | Azure lit l'admin user de l'ACR et le range dans `DOCKER_REGISTRY_SERVER_USERNAME` / `_PASSWORD` | L'admin user a été activé au bloc 1 : **aucune inconnue ajoutée** |
| Identité managée | La Web App s'authentifie sans mot de passe | Exige d'attribuer le rôle **AcrPull** à l'identité sur le registre — un geste RBAC non fait, donc un suspect de plus dans ce bloc |

En production réelle, l'identité managée est le bon choix : elle supprime un secret
permanent. Ici, elle aurait ajouté une cause d'échec à un bloc dont l'objet est justement
de les réduire. Le champ **Identité** se grise dès qu'on choisit les admin credentials.

Note : ces deux variables de registre sont lues **par la plateforme**, pas par le
conteneur. Azure ne les transmet jamais à l'application.

---

## Décision 3 — Application Insights sur `Non`

Proposé à `Oui` par défaut à l'onglet « Superviser + sécuriser ». Il donne les traces
distribuées — utile, mais il crée une ressource supplémentaire dans le groupe, avec un
coût d'ingestion, pour des signaux qui ne seront pas exploités.

Les **trois signaux exigés par le brief** (latence, coût indicatif, taux de blocage des
garde-fous) sont couverts au bloc 8 par *Metrics* et *Log stream*, tous deux inclus. Et le
choix n'est pas irréversible : Application Insights s'active après coup en un clic. Aucun
regret possible, donc `Non`.

---

## Décision 4 — `WEBSITE_WARMUP_STATUSES` écarté (correction du tuto)

Le tuto (§8.2) liste `WEBSITE_WARMUP_STATUSES=200` à côté de `WEBSITE_WARMUP_PATH`.
**Ce réglage n'apparaît pas dans la documentation Azure** — c'est l'une des corrections
relevées à la revue du tuto. Il n'a donc pas été posé.

`WEBSITE_WARMUP_PATH=/ready` est conservé : il fait pinguer `/ready` au redémarrage,
ce qui déclenche l'indexation de la FAQ avant la première vraie requête d'un client.

## Décision 5 — `WEBSITES_CONTAINER_START_TIME_LIMIT=600`

Un filet, pas une correction. La limite par défaut est **230 s** ; l'échauffement mesuré
en local est de ~5 s. Mais le **premier** démarrage y ajoute le tirage de l'image (477 Mo)
et le téléchargement de l'encodage `tiktoken`. Une ligne pour ne pas se faire couper au
pire moment — celui où on ne sait pas encore si le reste marche.

---

## Le déroulé, écran par écran

### Écran « Général »

`App Services` → `+ Créer` → `Application web`

| Champ | Valeur |
|---|---|
| Groupe de ressources | `slaurentRG` |
| Nom | `appwebsablvelmo` |
| **Publier** | **`Conteneur`** ⚠️ |
| Système d'exploitation | `Linux` |
| Région | `West Europe` |
| Plan Linux | `Créer nouveau` → `ASP-sablvelmo` |
| Plan tarifaire | **`Basic B1`** |

⚠️ **« Publier = Conteneur » est le clic qui décide de tout.** Avec « Code », l'écran
entier change et il faut recréer la Web App.

**Sur le tier.** `F1` (gratuit) est proposé par Microsoft pour les conteneurs Linux :
1 Gio de RAM pour un conteneur qui charge LangChain, LangGraph et un pool psycopg, c'est
la panne mémoire aléatoire — celle qui n'arrive pas au démarrage mais à la troisième
conversation. **B1** (1,75 Gio) est le premier tier raisonnable, et il portera **les
deux** Web Apps.

**Deux Web Apps, pas une.** Une App Service exécute **un** conteneur applicatif. Les trois
services du Compose deviennent donc deux Web Apps sur le même plan, plus le Flexible
Server. Le Compose ne co-localisait rien : il déclarait trois blocs joignables par le
réseau. Rien n'est perdu.

### Écran « Conteneur »

| Champ | Valeur | Remarque |
|---|---|---|
| Source de l'image | `Azure Container Registry` | |
| **Nom** | `main` — **laisser tel quel** | Voir ci-dessous |
| Registre | `acrsablvelmo` | |
| Authentification | `Informations d'identification de l'administrateur` | Décision 2 |
| Identité | *(grisé)* | |
| Image | `support-agent` | **par la liste déroulante** |
| Étiquette | `v1` | **par la liste déroulante** |
| Port | **`8000`** | |

**Le champ `Nom` = `main`.** Ce n'est ni le nom de l'image, ni l'étiquette, ni le nom de la
Web App : c'est l'identifiant du **conteneur** dans la configuration. Azure permet
d'ajouter des conteneurs *sidecar* (cache, agent de télémétrie…) et chacun porte un
identifiant ; le principal s'appelle `main`. Avec un seul conteneur, cet identifiant ne
sert qu'à lui-même.

**Le champ `Port` = 8000.** Celui-là compte. L'image fait `EXPOSE 8000`
(`Dockerfile:79`) et lance `uvicorn --host 0.0.0.0 --port 8000` (`Dockerfile:88`), alors
qu'App Service cherche par défaut sur 80 et 8080. Sans ce réglage, le routeur pousserait
le trafic vers un port fermé, et le message serait le fameux *« Container didn't respond
to HTTP pings on port: 8080 »*. Ce champ est la version « au clic » de l'app setting
`WEBSITES_PORT` — il l'a bien produit, vérifié après création.

### Les autres onglets

| Onglet | Réglage | Pourquoi |
|---|---|---|
| Mise en réseau | **tel quel** — accès public, pas de VNet | L'agent doit être joignable, et un VNet ranimerait le piège `tiktoken` (sortie réseau restreinte = conteneur qui ne démarre pas) |
| Superviser + sécuriser | App Insights → **`Non`** | Décision 3 |
| Balises | facultatif | Voir la note sur `slaurentRG` ci-dessous |

**Sur les balises.** Le brief demande un groupe de ressources « facile à retrouver et à
supprimer », et le geste de fin est `Supprimer le groupe de ressources`. Si `slaurentRG`
héberge d'autres travaux de formation, ce geste devient impossible et il faudra supprimer
ressource par ressource. Dans ce cas, poser une balise `projet = velmo2` sur chaque
ressource permet de les filtrer et de les sélectionner d'un coup.

---

## Les deux pièges du formulaire

### Piège 1 — `Image:Étiquette → acrsablvelmo.azurecr.io/null:null`

**Rencontré.** La validation a échoué sur l'onglet Conteneur, avec cette ligne au
récapitulatif.

Les champs **Image** et **Étiquette** ressemblent à des zones de texte, mais le portail ne
construit `image:étiquette` qu'à partir d'une **sélection dans la liste déroulante**. Ce
qui est tapé à la main n'est pas retenu — d'où `null:null`. **Le message d'erreur ne le dit
à aucun moment.**

Ces listes sont peuplées par un appel du portail à l'ACR ; elles mettent deux ou trois
secondes à se remplir, et passer à l'onglet suivant trop vite les laisse nulles.

**Correctif :** dérouler `Image` → `support-agent`, `Étiquette` → `v1`, puis vérifier au
récapitulatif que la ligne affiche exactement :

```
Image:Étiquette    acrsablvelmo.azurecr.io/support-agent:v1
```

C'est le seul endroit du formulaire où la sélection est visible. La disparition de la croix
rouge sur l'onglet ne suffit pas à le prouver.

### Piège 2 — le nom d'hôte par défaut unique

Le récapitulatif affiche **« Sécuriser le nom d'hôte par défaut unique : Activé »** — une
option récente, laissée activée. Elle ajoute un identifiant au nom d'hôte pour empêcher
qu'un tiers récupère le sous-domaine après suppression (*subdomain takeover*).

Conséquence concrète, mesurée ici :

```
attendu naïvement : appwebsablvelmo.azurewebsites.net
réel              : appwebsablvelmo-fuenh6eag9f8fsc9.westeurope-01.azurewebsites.net
```

**Ne jamais reconstruire cette URL de tête.** Elle se relève sur `Vue d'ensemble` →
**`Domaine par défaut`**, et c'est elle qui ira dans `AGENT_API_URL` au bloc 6.

---

## Le message bleu à la création : l'authentification de base SCM/FTP

Le portail avertit que l'authentification de base est désactivée et que cela « peut avoir
un impact sur les déploiements ». Il parle des **identifiants de publication** — les mots
de passe statiques du FTP, de Web Deploy et de Git local. Azure les désactive par défaut
depuis 2024 : ce sont des secrets permanents, difficiles à faire tourner.

**Laissé désactivé, aucun impact ici.** Le déploiement se fait **par conteneur** : la Web
App tire l'image depuis l'ACR avec les identifiants du registre, un mécanisme qui n'a rien
à voir avec celui-là.

**Une seule conséquence pour la suite :** le tuto mentionne la lecture des logs du
conteneur sur `https://<app>.scm.azurewebsites.net/api/logs/docker` — cet accès passe par
le basic auth, donc il ne fonctionnera pas. Sans importance : le **Log stream** du portail
s'authentifie par le compte Azure et donne la même chose. C'est ce qui sera utilisé au
bloc 8.

*(Si le déploiement était un jour automatisé par GitHub Actions, la bonne réponse ne serait
pas de réactiver ce réglage mais d'utiliser une identité fédérée OIDC — pas de secret du
tout.)*

---

## Les app settings — en « Modification avancée »

Après création, Azure a déjà posé quatre entrées : `DOCKER_REGISTRY_SERVER_URL`,
`_USERNAME`, `_PASSWORD` et `WEBSITES_PORT`. **Ne pas y toucher** : les trois premières
font vivre le tirage de l'image.

`Paramètres` → `Variables d'environnement` → onglet `Paramètres de l'application` →
bouton **`Modification avancée`** : le contenu se colle en JSON d'un coup, au lieu de
quinze `+ Ajouter`.

⚠️ **Le piège de ce mode : il remplace tout le contenu du tableau.** Les entrées se
collent **à l'intérieur** du tableau existant, juste après le `[`, sans supprimer les
lignes déjà présentes.

```json
  {"name":"LLM_PROVIDER","value":"openai_compatible","slotSetting":false},
  {"name":"LLM_MODEL","value":"gpt-5.6-sol","slotSetting":false},
  {"name":"LLM_FAST_MODEL","value":"gpt-5.6-luna","slotSetting":false},
  {"name":"LLM_INFERENCE_ENDPOINT","value":"https://slaurentext-5359-resource.openai.azure.com/openai/v1","slotSetting":false},
  {"name":"LLM_INFERENCE_API_KEY","value":"<clé Foundry>","slotSetting":false},
  {"name":"EMBEDDINGS_PROVIDER","value":"openai_compatible","slotSetting":false},
  {"name":"EMBEDDINGS_MODEL","value":"text-embedding-3-small","slotSetting":false},
  {"name":"KNOWLEDGE_DIR","value":"/app/data/kb-velmo","slotSetting":false},
  {"name":"PERSISTENCE_BACKEND","value":"memory","slotSetting":false},
  {"name":"SUPPORT_BACKEND","value":"memory","slotSetting":false},
  {"name":"API_KEY","value":"<clé de service>","slotSetting":false},
  {"name":"API_ALLOW_UNAUTHENTICATED","value":"false","slotSetting":false},
  {"name":"GUARDRAILS_ENABLED","value":"true","slotSetting":false},
  {"name":"WEBSITE_WARMUP_PATH","value":"/ready","slotSetting":false},
  {"name":"WEBSITES_CONTAINER_START_TIME_LIMIT","value":"600","slotSetting":false},
  {"name":"WEBSITES_ENABLE_APP_SERVICE_STORAGE","value":"false","slotSetting":false},
```

**Les deux valeurs à ne pas versionner** sont notées ici en `<placeholder>` : elles n'ont
leur place que dans Azure (puis dans le Key Vault au bloc 7), jamais dans ce dépôt.

`Appliquer` → `Confirmer`. La Web App redémarre : c'est normal, **tout** changement d'app
setting la redémarre.

### Les deux clés — ne pas les confondre

| Variable | Ce que c'est | Où la trouver |
|---|---|---|
| `LLM_INFERENCE_API_KEY` | **Existe déjà.** La clé du service d'IA | Portail → `slaurentext-5359-resource` → `Clés et point de terminaison` → Clé 1 |
| `API_KEY` | **N'existe nulle part.** Le mot de passe inventé pour protéger l'API de l'agent | À générer soi-même |

```bash
python3 -c "import secrets; print(secrets.token_urlsafe(32))"
```

Pourquoi cette commande précisément :

- **`secrets` et pas `random`** : `random` est *pseudo*-aléatoire — quelques sorties
  observées suffisent à prédire les suivantes. `secrets` puise dans le générateur d'aléa du
  système, celui des clés TLS.
- **`urlsafe`** : le résultat n'utilise que lettres, chiffres, `-` et `_`. Rien à échapper
  dans une URL, un en-tête HTTP ou une variable d'environnement. Même raisonnement que
  pour le mot de passe Postgres au bloc 3 : supprimer le problème d'encodage au lieu de le
  gérer.
- **32** : 256 bits d'entropie, ~43 caractères. Hors d'atteinte d'une force brute — et
  c'est ce qui protège le crédit Foundry, puisqu'un endpoint public mal protégé se fait
  vider en une nuit.

La commande **n'écrit rien nulle part**. Perdre la valeur oblige à en générer une autre et
à la changer **des deux côtés** : `API_KEY` ici, `AGENT_API_KEY` sur le client au bloc 6.
Deux noms, une seule valeur — celle du serveur qui la vérifie
(`server.py:83-106`) et celle de l'appelant qui la présente.

> **Réflexe pris en cours de route.** La clé a été générée via `!` dans Claude Code, donc
> sa valeur figure dans l'historique de la conversation. Sans conséquence pour un exercice,
> mais le bon geste est de générer un secret dans un terminal ordinaire et de le coller
> directement dans Azure, sans passer par un canal qui le conserve. C'est pour cette raison
> que les mots de passe du registre et de Foundry ont été saisis en mode masqué aux blocs
> 1 et 2. Et c'est exactement ce que le Key Vault du bloc 7 rend rattrapable : faire
> tourner la clé sans toucher au code.

---

## La vérification — trois preuves, dans cet ordre

```bash
H=appwebsablvelmo-fuenh6eag9f8fsc9.westeurope-01.azurewebsites.net

# 1. Liveness — le processus est-il vivant ?
curl -s -w '\nHTTP %{http_code} — %{time_total}s\n' "https://$H/health"

# 2. Readiness — le graphe est-il construit et la FAQ indexée ?
curl -s -w '\nHTTP %{http_code} — %{time_total}s\n' "https://$H/ready"

# 3. La porte est-elle fermée ? (doit échouer)
curl -s -w '\nHTTP %{http_code}\n' -X POST "https://$H/chat" \
  -H 'Content-Type: application/json' -H 'X-API-Key: mauvaise-cle' \
  -d '{"message":"bonjour"}'
```

**Sorties réelles obtenues :**

```
/health  (1er appel, conteneur endormi) → pas de réponse après 45 s
/ready                                  → {"ready":true}      HTTP 200 — 19,46 s
/health  (à chaud)                      → {"status":"ok"}     HTTP 200 —  0,29 s
/health  (à chaud, 2e)                  → {"status":"ok"}     HTTP 200 —  0,24 s
/ready   (à chaud)                      → {"ready":true}      HTTP 200 —  0,27 s

POST /chat sans en-tête    → HTTP 401
POST /chat avec clé fausse → {"detail":"Missing or invalid X-API-Key header."} HTTP 401
```

### Ce que chaque ligne prouve

| Preuve | Ce qu'elle établit |
|---|---|
| `/health` → `200` | L'image tourne (donc bien en **amd64**), uvicorn a ouvert le port, et `WEBSITES_PORT=8000` route correctement |
| `/ready` → `{"ready":true}` | **La preuve qui compte.** `server.py:111-139` construit le graphe *dans le lifespan*, ce qui sonde les embeddings : `true` signifie que l'agent en ligne a réellement appelé Foundry et indexé la FAQ |
| `/chat` → `401` | `API_KEY` est bien posée et `API_ALLOW_UNAUTHENTICATED=false` est respecté : l'endpoint public ne dépense pas le crédit Foundry de n'importe qui |

**Pourquoi le premier `/health` a expiré, et pourquoi ce n'est pas un échec.** Le premier
appel est tombé sur un conteneur endormi : il a payé le démarrage à froid (processus à
relancer, plus le lifespan complet). Le `/ready` suivant, arrivé 45 s plus tard, a trouvé
le port ouvert et a répondu en 19 s — puis tout est descendu sous les 300 ms.

Autrement dit : **le timeout mesurait le réveil, pas une panne.** Le réflexe à garder est
celui du tuto — ne rien juger pendant une à deux minutes après un redémarrage, et relancer
l'appel avant de partir lire les logs.

### Lecture des échecs, si ça avait raté

| Ce que dit Azure | Ce que c'est en vrai |
|---|---|
| *« Container didn't respond to HTTP pings on port: 8000 »* | Message **inutile** : le port ne s'est jamais ouvert. `uvicorn` n'ouvre le sien qu'**après** le lifespan, qui construit le graphe et sonde les embeddings. La vraie cause est **au-dessus** dans le log du conteneur |
| `exec format error` | Image **arm64** → retour au bloc 1 |
| `401` / `403` du provider | `LLM_INFERENCE_API_KEY`, ou le `/openai/v1` oublié dans l'endpoint |
| `RuntimeError: Refusing to start without authentication` | `API_KEY` absente et `API_ALLOW_UNAUTHENTICATED` pas à `true` — `server.py:122` refuse de démarrer, en clair |
| `GET /robots933456.txt 404` | **Normal, ignorer.** C'est la sonde de disponibilité d'Azure ; un 404 lui suffit |

Où regarder : `Supervision` → **`Log stream`**. Le commit `1c9e044` (config validée avant
tout I/O) est précisément ce qui garantit qu'une erreur de configuration soit **nommée en
clair** dans ce log, plutôt que déguisée en « le port ne répond pas ».

---

## ✅ Bloc 4 terminé

| Preuve attendue | Obtenue |
|---|---|
| Une URL publique HTTPS | ✅ `https://appwebsablvelmo-fuenh6eag9f8fsc9.westeurope-01.azurewebsites.net` |
| `/health` répond `200` | ✅ `{"status":"ok"}` |
| `/ready` répond `200` | ✅ `{"ready":true}` — l'agent parle à Foundry en ligne |
| L'API refuse un appel non authentifié | ✅ `401` sans clé **et** avec clé fausse |

**Livrable du brief obtenu** (compétence 5 — l'agent est déployé et joignable).

**Quatre inconnues écartées sur quatre blocs :** l'image tourne en amd64, le service d'IA
répond, la base accepte pgvector, la Web App sert l'agent en ligne. Un échec au bloc 5 ne
pourra venir **que** de la connexion à la base.

### Le geste restant — optionnel, non fait

**Le contrôle d'intégrité** (§8.3 du tuto) : `Supervision` → `Contrôle d'intégrité` →
`Activer` → chemin **`/health`** → `Enregistrer`.

Il n'est pas nécessaire à la preuve du bloc — l'agent répond sans lui. Il sert à ce
qu'Azure retire de la rotation une instance qui ne répond plus, ce qui n'a d'effet réel
qu'avec plusieurs instances. Sur un plan B1 à une instance, son apport se limite au
redémarrage automatique.

⚠️ **Si activé, ne pas inverser les deux endpoints.** `server.py` en expose deux, et App
Service a deux hooks qui leur correspondent exactement :

| Hook Azure | Endpoint | Question posée |
|---|---|---|
| Échauffement (`WEBSITE_WARMUP_PATH`) | **`/ready`** | « Le graphe est-il construit et la FAQ indexée ? » |
| Contrôle d'intégrité | **`/health`** | « Suis-je vivant ? » (ne touche aucune dépendance) |

Mettre `/ready` en contrôle d'intégrité ferait sortir de la rotation un conteneur
simplement en train de chauffer — et il redémarrerait en boucle. C'est le bug classique, et
il est silencieux. Le docstring de `/health` dans `server.py:149-158` dit exactement
pourquoi les deux sont séparés : *« a failing liveness probe means RESTART me, a failing
readiness probe means STOP SENDING me traffic »*.

**Bloc suivant :** basculer `PERSISTENCE_BACKEND` sur `postgres`, ajouter `DATABASE_URL`
(la chaîne composée au bloc 3), et prouver la persistance (R2) et l'isolation par
utilisateur (R3). Le geste coûte **deux variables**.
