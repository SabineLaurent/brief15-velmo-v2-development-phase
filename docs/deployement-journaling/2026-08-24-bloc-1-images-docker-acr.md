# 2026-08-24 — Bloc 1 : les images Docker et le registre ACR

**Objet :** fabriquer les deux images en `linux/amd64`, les vérifier en local, les pousser
sur le registre Azure.
**Correspond à :** §6 du tuto `docs/2026-08-24-tuto-deploiement-azure-portail.md`
(compétence 4 du brief, volet « préparation des images »).
**Statut :** ✅ **terminé** — les deux images sont dans `acrsablvelmo`, en `amd64`,
avec le tag `v1`.

> **Comment relire ce journal.** Tout ce qui est dans un bloc `bash` a été **réellement
> tapé**, dans l'ordre. Les sorties sont recopiées telles quelles, tronquées quand elles
> étaient longues (indiqué par `[…]`). Ce qui a été fait **au portail** est décrit par ses
> clics, parce qu'il n'y a pas de commande à rejouer.

---

## Les valeurs de ce déploiement

| Élément | Valeur retenue | Remarque |
|---|---|---|
| Groupe de ressources | `slaurentRG` | existait déjà |
| Registre ACR | `acrsablvelmo` → `acrsablvelmo.azurecr.io` | existait déjà |
| Région de l'ACR | **West Europe** | ≠ France Central du brief — écart assumé, voir « Décisions » |
| Tag des images | `v1` | le **même** sur les deux images |
| Image agent | `acrsablvelmo.azurecr.io/support-agent:v1` | 477 Mo |
| Image client | `acrsablvelmo.azurecr.io/client-chainlit:v1` | 379 Mo |

⚠️ Le tuto et `plan-deploiement-2026-07-25.md` parlent partout de `rg-velmo-prod` et
`acrvelmoprod`. **Ce ne sont pas les vrais noms.** Toute commande recopiée depuis ces
documents doit être traduite avec le tableau ci-dessus.

---

## 0. Pourquoi ce bloc n'est pas « au clic »

Le portail Azure **ne sait pas construire une image Docker**. Il sait la stocker (ACR),
l'exécuter (App Service), la rebâtir depuis un dépôt Git (ACR Tasks) — pas la fabriquer
depuis un `Dockerfile`.

Et il y a le problème d'architecture :

```bash
uname -m
```
```
arm64
```

Le Mac est en **arm64**, App Service Linux exécute du **linux/amd64**. Une image arm64
poussée sur l'ACR démarrerait sur `exec format error`, qu'Azure présente sous la forme
trompeuse *« Container didn't respond to HTTP pings on port: 8000 »*.

D'où la seule sortie du portail de tout le déploiement : `--platform linux/amd64`.

## 1. État de l'outillage, vérifié avant de commencer

```bash
docker version --format '{{.Server.Version}}'
```
```
failed to connect to the docker API at unix:///Users/sabine/.docker/run/docker.sock […]
```
→ **Docker Desktop était arrêté.** Diagnostic immédiat : `docker.sock` absent.

```bash
docker buildx version
```
```
github.com/docker/buildx v0.35.0-desktop.2 b554ce1decd8b509893b1e7c6227eabfb923d094
```
→ buildx présent, donc `--platform` disponible.

```bash
which az
```
```
az not found
```
→ **Pas d'Azure CLI**, et ce n'est pas un problème : tout se fait au portail, et le push
passe par `docker login` (le client Docker, pas Azure).

---

## Étape A — démarrer Docker Desktop

```bash
open -a Docker
```

Puis attendre que le démon réponde, au lieu de deviner :

```bash
until docker version --format '{{.Server.Version}}' >/dev/null 2>&1; do sleep 2; done
echo "Démon prêt : $(docker version --format '{{.Server.Version}}')"
```
```
Démon prêt : 29.6.1
```

*Pourquoi une boucle et pas un simple `sleep 30`* : Docker Desktop met un temps variable à
lever son démon. La boucle rend la main dès que c'est vrai, et n'échoue jamais trop tôt.

---

## Étape B — activer l'admin user sur l'ACR (au portail)

Le registre existait déjà ; il manquait une bascule.

1. https://portal.azure.com → registre **`acrsablvelmo`**
2. Menu de gauche → **`Settings`** → **`Access keys`**
3. **`Admin user`** → **`Enabled`**
4. Relevé : **Login server** = `acrsablvelmo.azurecr.io`, **Username** = `acrsablvelmo`,
   **password** = *(gardé hors de ce dépôt)*

**À quoi ça sert :** c'est ce qui permettra à App Service de **tirer** l'image sans qu'on
configure une identité managée (laquelle demanderait du RBAC à la main). App Service
rangera ce mot de passe dans `DOCKER_REGISTRY_SERVER_PASSWORD`, une variable que Microsoft
documente comme **jamais transmise au conteneur** — le critère « aucun secret exposé » du
brief tient donc, même avec l'admin user.

---

## Étape C — construire en amd64 et vérifier AVANT de pousser

### La commande

```bash
make docker-smoke PLATFORM=linux/amd64
```

Une seule commande, parce que le dépôt l'avait déjà prévu — rien n'a été écrit pour ce
bloc :

- `Makefile:87-88` : `PLATFORM ?=` → `DOCKER_PLATFORM := --platform $(PLATFORM)`
- `Makefile:90-92` : construit les deux images
- `Makefile:94-95` : enchaîne sur `scripts/docker_smoke.py`

### La sortie (fin)

```
#18 naming to docker.io/library/client-chainlit:dev done
#18 DONE 2.5s

uv run --no-project python scripts/docker_smoke.py
Stub embeddings sur le port 51392 (dim 8, aucun secret).
→ client-chainlit:dev: aucun module du cerveau ?
  ✅ absents : support_agent, langchain, langchain_core, langgraph
→ support-agent:dev: démarre, s'échauffe, et répond ?
  ✅ /health et /ready répondent (échauffement 5.3 s, 2 appels embeddings / 17 vecteurs)
  ✅ POST /chat sans X-API-Key : 401
→ Les deux images tiennent leurs promesses.
```

### Ce que ces trois ✅ prouvent, et pourquoi ça valait le détour

| Preuve | Ce qu'elle écarte |
|---|---|
| Le client ne contient ni `langchain` ni `langgraph` | Le découplage n'a pas régressé : si l'UI est compromise, elle ne porte pas le cerveau |
| L'agent **démarre** : lifespan, index FAQ, `/health` + `/ready` à 200 | Un défaut d'image se voit **ici**, en clair, au lieu d'un « HTTP pings » illisible dans un Log stream Azure |
| `POST /chat` sans clé → **401** | La porte est fermée avant tout appel au LLM (protection d'accès **et** budgétaire) |

Le smoke test n'a besoin **d'aucun secret** : il sert lui-même un stub qui parle l'API
embeddings d'OpenAI, sur le rail `openai_compatible` — celui-là même qu'Azure utilisera.

**Échauffement : 5,3 s sous émulation amd64** (le tuto annonçait 4,4 s en natif). Très
loin de la limite de démarrage d'App Service (230 s).

### Vérifier l'architecture, sans faire confiance

```bash
docker image inspect support-agent:dev --format '{{.Os}}/{{.Architecture}}'
docker image inspect client-chainlit:dev --format '{{.Os}}/{{.Architecture}}'
```
```
linux/amd64
linux/amd64
```

### Deux pièges rencontrés, à connaître

1. **`docker image inspect --format '{{.Size}}'` a annoncé 105 Mo et 87 Mo** — j'ai cru un
   instant que le tuto surestimait les images à ~500 Mo. Faux : `docker images` donne
   **477 Mo et 379 Mo**, et c'est la bonne mesure. Ne pas conclure sur `.Size`.
2. **buildx a produit un *manifest list* avec attestation**, visible dans le log
   (`exporting attestation manifest`, `exporting manifest list`). Sans conséquence pour
   App Service, mais le portail ACR peut afficher une ligne `unknown/unknown` à côté de
   `linux/amd64` : c'est le manifeste de provenance, pas une erreur.

### Effet de bord à ne pas oublier

Ces images amd64 **écrasent** les `:dev` natives arm64. La pile locale (`make docker-up`)
tournera désormais en émulation, donc plus lentement, jusqu'à un rebuild sans `PLATFORM` :

```bash
make docker-build          # revient en natif arm64
```

---

## Étape D — taguer, s'authentifier, pousser

### Taguer

```bash
docker tag support-agent:dev   acrsablvelmo.azurecr.io/support-agent:v1
docker tag client-chainlit:dev acrsablvelmo.azurecr.io/client-chainlit:v1
docker images --filter=reference='acrsablvelmo.azurecr.io/*' --format 'table {{.Repository}}\t{{.Tag}}\t{{.Size}}'
```
```
REPOSITORY                                TAG       SIZE
acrsablvelmo.azurecr.io/client-chainlit   v1        379MB
acrsablvelmo.azurecr.io/support-agent     v1        477MB
```

**Deux règles de nommage, et leurs raisons :**
- **`v1`, pas `latest`** : avec `latest`, on ne sait jamais quelle image tourne, et App
  Service ne redéploie pas de façon prévisible. Un tag par publication.
- **le même tag sur les deux images** : un seul `uv.lock`, donc un seul jeu de dépendances
  testé ensemble. On publie par jeu, pas par bloc.

### S'authentifier — commande tapée par moi, pas par l'assistant

```bash
docker login acrsablvelmo.azurecr.io -u acrsablvelmo
```

| Morceau | Rôle |
|---|---|
| `docker login` | Ouvre une session auprès d'un registre |
| `acrsablvelmo.azurecr.io` | **Quel** registre. Sans cet argument, Docker viserait Docker Hub |
| `-u acrsablvelmo` | L'utilisateur admin activé à l'étape B (même nom que le registre) |

Le mot de passe **n'est pas dans la commande** : Docker le demande en interactif juste
après (rien ne s'affiche à la frappe). Attendu : `Login Succeeded`. Le jeton est ensuite
gardé dans le trousseau macOS par Docker Desktop, pas en clair dans un fichier.

Sans cette étape, le push s'arrête sur `unauthorized: authentication required`.

> **Pourquoi je la tape moi-même :** ce mot de passe est un des secrets du projet. Saisi
> en interactif, il ne passe ni par le contexte de l'assistant, ni par un fichier, ni par
> l'historique du shell. C'est la règle du brief sur les secrets, appliquée à la méthode
> de travail.

Résultat obtenu :

```
Password:
Login Succeeded
```

### Pousser

```bash
docker push acrsablvelmo.azurecr.io/support-agent:v1
docker push acrsablvelmo.azurecr.io/client-chainlit:v1
```

```
7a22f3ecc1e8: Pushed
f96ec48bde94: Pushed
[…]
85e834da20c4: Pushed
v1: digest: sha256:783ac058c4b521a58bbefbd56e27bb01b85d90fa0e22f4566ae78be932764bc5 size: 856
4f4fb700ef54: Pushed
[…]
5c0984ef541a: Pushed
v1: digest: sha256:cb31849fe1d3ee7af98d8f5969fc62ec16c4c44343e5ff2e1a12a52a9223b992 size: 856
```

Docker envoie **couche par couche**, pas l'image d'un bloc : un rebuild qui ne change que
le code ne réenvoie que la couche du code, pas les 400 Mo de dépendances.

### 🎯 La preuve la plus utile de tout ce bloc

Compare les digests reçus par Azure aux `IMAGE ID` locaux :

| | Local (`docker images`) | Digest poussé |
|---|---|---|
| `support-agent` | `783ac058c4b5` | `sha256:783ac058c4b5…` |
| `client-chainlit` | `cb31849fe1d3` | `sha256:cb31849fe1d3…` |

**Ils sont identiques.** L'image qui tournera sur Azure est donc, bit pour bit, celle que
le smoke test a validée à l'étape C. C'est exactement ce que la voie
« construire → vérifier → pousser » achète, et ce que le `buildx --push` du tuto ne
pouvait pas donner : là, l'image partait sans jamais avoir existé — donc sans avoir pu
être testée — sur la machine.

À retenir comme méthode : **un digest est une identité, pas un numéro de version.** Deux
images de même digest sont la même image ; c'est ce qui permet d'affirmer « ce qui marchait
en local marche en ligne » au lieu de l'espérer.

### Question qu'on se repose forcément : pourquoi `dev` **et** `v1` ?

Après le tag, `docker images` et le dashboard de Docker Desktop montrent **quatre**
lignes. Ce ne sont pas quatre images — c'en est **deux**, portant deux étiquettes chacune.
La colonne de l'identifiant le dit :

```
acrsablvelmo.azurecr.io/support-agent   v1    783ac058c4b5   476.68 MB
support-agent                           dev   783ac058c4b5   476.68 MB
                                              ↑ le même digest
```

| Étiquette | Rôle |
|---|---|
| `support-agent:dev` | Le nom **local**, donné par `Makefile:91`. C'est celui que lisent `compose.yaml` et `docker_smoke.py:48` |
| `acrsablvelmo.azurecr.io/support-agent:v1` | Le nom **de destination** : le préfixe est une adresse, lue par `docker push` |

Le second nom est une adresse postale collée sur le même colis. **Ne pas supprimer
`:dev`** : la pile locale et le smoke test le cherchent par ce nom.

Corollaire utile : au **portail** ACR, il n'y a que **deux** dépôts et **aucun** tag
`dev` — les étiquettes locales n'ont jamais quitté la machine. C'est ce qui rend la
capture du portail parlante : elle montre ce qui est *sur Azure*, pas ce qui est sur le
Mac. (Le dashboard Docker Desktop, lui, affiche les quatre étiquettes avec un badge
`AMD64` : utile pour se rassurer, mais ce n'est pas le justificatif demandé.)

### Note sur les emplacements

`docker tag` **ne copie rien** : les quatre lignes de `docker images` ne sont que deux
images portant deux noms chacune (même `IMAGE ID`). Physiquement, elles vivent dans le
disque virtuel de Docker Desktop (`~/Library/Containers/com.docker.docker/`) — **rien
n'entre dans le dépôt Git**. C'est le préfixe du tag (`acrsablvelmo.azurecr.io/`) qui a
dit à `docker push` où aller ; un tag sans préfixe aurait visé Docker Hub.

Une fois les images dans l'ACR, le lien avec le Mac est coupé : App Service tirera
**depuis le registre**. La machine peut être éteinte, l'agent en ligne continue.

---

## Étape E — la preuve, au portail ✅

Chemin (portail en français) : registre `acrsablvelmo` → **`Dépôts`** (= *Repositories*).

Obtenu :

```
Dépôts
  client-chainlit
  support-agent
```

**Deux dépôts, et aucun tag `dev`** — ce qui confirme d'un coup d'œil que seules les
étiquettes préfixées `acrsablvelmo.azurecr.io/` ont voyagé.

Pour l'architecture : cliquer un dépôt → étiquette **`v1`** → le panneau affiche
**`Architecture`** = **`amd64`**. *(Si `arm64` s'affichait, il faudrait s'arrêter et
refaire le build : la Web App ne démarrerait jamais, et Azure ne nommerait pas la cause.)*

Ici, l'architecture était déjà établie **trois fois** avant d'arriver au portail :

1. `docker image inspect … --format '{{.Os}}/{{.Architecture}}'` → `linux/amd64`
2. badges `AMD64` sur les quatre étiquettes dans le dashboard Docker Desktop
3. digests poussés identiques aux `IMAGE ID` locaux

C'est la bonne façon de travailler avec Azure : **ne jamais utiliser le portail comme
première source de vérité**, seulement comme confirmation. Le portail est lent à
interroger et avare en messages d'erreur ; le terminal répond en une seconde et dit
pourquoi.

📸 **Captures gardées** : la vue *Dépôts* du portail (justificatif n°6 du §13 du tuto) et
le dashboard Docker Desktop avec les badges `AMD64`.

---

## ✅ Bloc 1 terminé

| Preuve attendue | Obtenue |
|---|---|
| `→ Les deux images tiennent leurs promesses.` | ✅ étape C |
| `linux/amd64` sur les deux images | ✅ trois fois (inspect, dashboard, portail) |
| 2 dépôts avec le tag `v1` dans l'ACR | ✅ étape E |
| Digest poussé == digest vérifié en local | ✅ `783ac058c4b5` / `cb31849fe1d3` |

**Durée réelle : ~25 min**, dont l'essentiel en attente du build et du push. Le tuto
prévoyait 30 min pour ce bloc — l'estimation était juste.

---

## Décisions prises dans ce bloc

1. **ACR laissé en West Europe** alors que le brief demande France Central / Sweden
   Central. Justification : un registre ne stocke que des **images**, aucune donnée
   personnelle, et West Europe est dans l'UE. Un pull cross-région coûte quelques secondes
   au premier démarrage, une fois. Le recréer imposerait un nouveau nom (celui-ci reste
   réservé un temps après suppression) pour un bénéfice nul.
   → **En contrepartie, Postgres et Foundry iront en France Central** : c'est là que se
   trouveront les données personnelles (mémoire long terme des clients). À dire ainsi dans
   le dossier de déploiement : *« registre en West Europe, données en France Central »*.

2. **Build + smoke test en local, puis tag/push** — au lieu du
   `docker buildx build --push` du tuto §6.3, qui envoie l'image sur l'ACR **sans jamais
   la faire exister en local**, donc sans pouvoir la tester. La voie retenue fait tourner
   sur Azure exactement l'image qui a été vérifiée ici. C'est la garantie centrale du plan
   de déploiement (« sans que l'image change entre ma machine et le cloud »).
   → **Le §6.3 du tuto doit être mis à jour** pour refléter cette voie.

3. **Admin user plutôt qu'identité managée** pour le pull de l'image. Assumé : l'identité
   managée demanderait du RBAC en ligne de commande. Le secret reste hors du conteneur.

---

## Ce qu'il reste à faire, dans l'ordre

- [x] `docker login` + les deux `docker push`
- [x] Vérifier au portail et prendre la capture
- [ ] **Bloc 2** : la ressource Foundry (§5 du tuto) — elle passe avant Postgres bien que
      Postgres soit plus lent à créer, parce que `long_term.py` sonde les embeddings **au
      démarrage** du conteneur. Sans déploiement d'embeddings, la Web App ne démarre pas
      du tout, et le message d'Azure ne le dira pas.
- [ ] **Bloc 3** : PostgreSQL Flexible Server (§7), à lancer tôt parce qu'il est lent
      (5–10 min d'attente passive)

## Dettes ouvertes par ce bloc

1. Reporter les vrais noms (`slaurentRG`, `acrsablvelmo`, West Europe) dans
   `docs/2026-08-24-tuto-deploiement-azure-portail.md` et
   `docs/plan-deploiement-2026-07-25.md`.
2. Réécrire le **§6.3 du tuto** : la voie retenue est Makefile + smoke test + tag/push,
   pas `buildx --push`.
3. Le dossier de déploiement (compétences 1 à 3 du brief) n'a toujours **ni schéma cible
   ni liste des secrets externalisés**. Le brief en fait la *porte d'entrée* de
   l'évaluation, avant toute mise en ligne — à produire avant le bloc 4 (App Service).

---

## Pour reproduire ce bloc d'un bout à l'autre

```bash
# 0. Docker Desktop
open -a Docker
until docker version >/dev/null 2>&1; do sleep 2; done

# 1. Construire en amd64 ET vérifier (une seule commande)
make docker-smoke PLATFORM=linux/amd64

# 2. Contrôler l'architecture
docker image inspect support-agent:dev   --format '{{.Os}}/{{.Architecture}}'
docker image inspect client-chainlit:dev --format '{{.Os}}/{{.Architecture}}'

# 3. Taguer pour le registre
docker tag support-agent:dev   acrsablvelmo.azurecr.io/support-agent:v1
docker tag client-chainlit:dev acrsablvelmo.azurecr.io/client-chainlit:v1

# 4. S'authentifier (mot de passe = Access keys du registre, saisi en interactif)
docker login acrsablvelmo.azurecr.io -u acrsablvelmo

# 5. Pousser
docker push acrsablvelmo.azurecr.io/support-agent:v1
docker push acrsablvelmo.azurecr.io/client-chainlit:v1

# 6. Revenir en natif pour retravailler en local
make docker-build
```

Au portail, deux gestes seulement : activer l'**admin user** (avant l'étape 4) et
vérifier **Repositories** (après l'étape 5).
