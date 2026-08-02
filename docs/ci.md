# 🛡️ La CI — la garde du dépôt

**Objet :** ce que vérifie l'intégration continue, quand elle se déclenche, ce
qu'elle provoque — et surtout ce qu'elle **ne** provoque **pas**.
**Date :** 2026-07-26 · **mis à jour le 2026-07-31** (§10 : le job `images`)
**Fichier concerné :** [`../.github/workflows/ci.yml`](../.github/workflows/ci.yml)
**CI-1** (la garde) et **CI-2** (les images, en amd64) sont faites ; **CI-3** (la
publication sur l'ACR) arrive à l'étape 5 du déploiement, avec les secrets Azure.
**Se lit avec :** [`plan-deploiement-2026-07-25.md`](plan-deploiement-2026-07-25.md)
(l'ordre de déploiement, §Étape 5).

---

## 1. Pourquoi maintenant — trois faits mesurés, pas une intuition

La CI n'a pas été montée « parce que ça se fait ». Trois constats l'ont décidée.

**a) Le vert local n'était pas le vert de la CI.** La même commande, `make check`,
sur la même machine, avec et sans le fichier `.env` :

```
avec .env     93 passés                    en 38 s
sans .env     85 passés, 7 skippés, 1 ÉCHEC en  2,5 s
```

Trente-cinq des trente-huit secondes étaient des **appels réseau facturés au
fournisseur de LLM**. Autrement dit : le contrôle qualité local mesurait en partie
les credentials de la développeuse, pas la santé du code.

**b) Un test ne passait que sur une machine équipée.** `test_persistence.py`
affirmait qu'une factory devait refuser de démarrer *avant toute connexion* quand
`DATABASE_URL` manque. En réalité le code faisait un appel réseau **avant** cette
vérification : le test ne pouvait donc réussir que là où une clé valide existait
déjà. Le défaut est détaillé au §9 — c'est la CI qui l'a fait apparaître, avant
même d'exister, simplement en posant la question « et sur une machine nue ? ».

**c) L'étape 5 en a besoin techniquement.** Voir §10 : une image construite sur un
Mac Apple Silicon ne peut pas s'exécuter sur Azure.

---

## 2. Le principe : la CI réagit à un **événement**, pas à une horloge

Rien ne tourne en continu. GitHub reçoit un événement sur le dépôt (« des commits
ont été poussés », « une PR a été ouverte »), regarde s'il correspond au bloc `on:`
d'un workflow, et si oui **crée une exécution** (*run*). Pas d'événement, pas de
CI : un dépôt au repos ne consomme rien.

C'est une différence de nature avec un `cron` : la CI ne se demande jamais « est-ce
l'heure ? », elle se demande « qu'est-ce qui vient de se passer ? ».

---

## 3. Où vit la CI — et le piège qui va avec

Le workflow est un **fichier versionné**, `.github/workflows/ci.yml`. Il voyage
donc avec les branches, exactement comme n'importe quel fichier du projet.

⚠️ **Conséquence contre-intuitive : la CI n'existe pour une branche qu'une fois le
fichier présent dessus.** Elle ne s'applique jamais rétroactivement.

Au moment où ce document est écrit, le fichier n'existe que sur la branche de
travail. Donc :

- push sur la branche de travail → GitHub lit le fichier **dans ce commit** → CI ✅
- push sur `main` → GitHub cherche le fichier dans le commit de `main` → absent →
  **rien ne se passe, et aucun message ne le signale**

`main` n'aura de garde qu'après la première fusion. Pour une PR, GitHub utilise le
fichier du **résultat de la fusion** — donc la version de la branche qui l'apporte.

---

## 4. Quand elle se déclenche — le flux `feature → dev → main`

```
tu codes sur la branche feature
  │
  ├─ git push                      → PUSH                    → CI ✅  ta branche telle quelle
  │
  ├─ PR feature → dev              → PULL_REQUEST            → CI ✅  résultat de la fusion
  ├─ un commit de plus sur la PR   → PULL_REQUEST + PUSH     → CI ✅  les deux
  │
  ├─ PR dev → main                 → PULL_REQUEST            → CI ✅  fusion vers la prod
  │
  └─ la PR est fusionnée           → PUSH sur main           → CI ✅  état déployable
```

Le déclencheur `pull_request` couvre par défaut trois moments : **ouverture**,
**chaque nouveau commit poussé sur la PR** (`synchronize`), et **réouverture**. Une
PR ne peut donc pas devenir périmée sans que ça se voie.

### Pourquoi garder `push` **et** `pull_request` — ce n'est pas un doublon

| Déclencheur | Ce qui est réellement testé |
|---|---|
| `push` | **ta branche telle quelle** |
| `pull_request` | **le résultat de la fusion** (GitHub construit un merge commit head + base) |

Si la branche de destination a avancé pendant ton travail, la fusion peut casser
alors que les deux branches sont vertes **séparément**. C'est un **conflit
sémantique** : git fusionne sans broncher parce que les lignes ne se chevauchent
pas, mais le code résultant est incohérent — quelqu'un a renommé une fonction que
ta branche appelle encore. Aucun outil de merge ne le voit ; **seul le test du
merge commit l'attrape.**

Si tu fusionnes en local sans passer par une PR, ce déclencheur reste simplement
dormant : il ne coûte rien. S'il se déclenche, c'est qu'il vérifie quelque chose
que le `push` ne vérifiait pas.

---

## 5. Comment elle s'exécute — une machine neuve qui ne sait rien de toi

GitHub alloue une **machine virtuelle vierge** (le *runner* `ubuntu-latest`, chez
GitHub, pas chez toi). Elle n'a jamais vu le projet. Puis, dans l'ordre du fichier :

| Étape | Ce qui se passe | Durée |
|---|---|---|
| `actions/checkout` | clone le dépôt au commit exact de l'événement | ~2 s |
| `setup-uv` | installe `uv`, fixe **Python 3.12**, restaure le cache des dépendances | ~5 s |
| `uv sync --locked` | installe l'environnement — **échoue si `uv.lock` a divergé** | ~5-30 s |
| `make check` | `ruff check .` puis `pytest` | ~5 s |

Total : environ **une minute**, dont l'essentiel en installation. La suite de tests
elle-même prend **0,73 s** (mesuré, sans credentials). La VM est ensuite détruite ;
rien ne persiste, sauf le cache de `uv`.

**C'est la nudité de cette machine qui donne sa valeur au vert.** Elle n'a pas ton
`.env`, pas tes clés, pas ton `.venv`, pas ton historique de commandes. Elle ne peut
vérifier que ce qui est vrai **pour tout le monde** — et c'est exactement la
propriété qu'on veut garantir.

### Trois choix du fichier qui méritent d'être compris

**Comment les actions sont référencées — et pourquoi les deux ne le sont pas pareil.**
Le **tout premier run de cette CI a échoué** là-dessus : `astral-sh/setup-uv@v9` →
*« unable to find version v9 »*. La release `v9.0.0` existait pourtant. Ce qui
manquait, c'est le **tag majeur mobile** `v9` — ces tags courts (`v9`, `v7`) sont des
étiquettes que le mainteneur repointe à la main, et **rien ne garantit qu'elles
existent** parce qu'une release porte ce numéro.

La règle retenue : **suivre la doc de chaque action, pas une habitude uniforme.**

| Action | Référence | Pourquoi |
|---|---|---|
| `actions/checkout` | tag `v7` | C'est l'exemple de son README. Action *first-party*, tags majeurs mobiles maintenus |
| `astral-sh/setup-uv` | SHA complet + `# v8.3.2` | Ce que son README fait dans **tous** ses exemples. Un SHA est **immuable** : ni disparition, ni repointage par son auteur |

⚠️ La contrepartie du SHA est réelle : **aucune mise à jour automatique**. D'où le
commentaire de version, obligatoire — sans lui, personne ne sait plus à quoi ce SHA
correspond. C'est aussi la version *durcie* recommandée dès qu'un workflow touche des
secrets de production, ce qui arrivera à l'étape 5.

**`uv sync --locked` n'est pas une précaution, c'est un contrôle.** Il échoue si
`uv.lock` n'est plus cohérent avec les `pyproject.toml`. En local, `uv run`
re-résout en silence : un `uv add` non commité passe donc inaperçu — et casserait
le build de l'image, où `uv sync --frozen` n'a pas cette indulgence.

**`make check`, et non `uv run pytest`.** La CI lance **exactement** la commande
que tu lances en local (porte d'entrée unique, cf. `CLAUDE.md`). Si les deux
divergent, son vert ne veut plus rien dire pour toi, et le tien ne veut plus rien
dire pour elle.

---

## 6. Ce que ça provoque

**Un *check* attaché au commit.** C'est l'effet principal, et la mécanique mérite
d'être comprise : le résultat se colle à un **SHA de commit**, pas à une branche.
C'est pourquoi il apparaît partout où ce commit apparaît — liste des commits, en
tête de la PR, à côté du nom de la branche. Rond orange pendant, coche verte ou
croix rouge après.

**Une notification** par mail en cas d'échec sur tes propres commits.

**Des logs consultables**, onglet `Actions`, étape par étape, avec la sortie exacte
de `ruff` et de `pytest`. C'est là qu'on lit *pourquoi* c'est rouge.

**L'annulation des runs devenus inutiles.** Le bloc `concurrency` : trois pushes en
deux minutes, et les deux premiers runs sont tués en cours de route. Seul le
dernier état compte.

**Un cache écrit** pour la fois suivante, invalidé quand `uv.lock` change.

---

## 7. Ce que ça ne provoque PAS — aussi important

- **Ça ne bloque rien.** Une PR rouge se fusionne d'un clic tant qu'aucune règle ne
  l'exige. **La CI informe ; c'est la protection de branche qui interdit.**
- **Ça ne déploie rien.** Aucune image construite, rien poussé sur un registre,
  rien touché sur Azure.
- **Ça ne modifie pas le dépôt.** `permissions: contents: read` — le job lit et
  n'écrit pas. Aucun commit, aucun tag, aucune release.
- **Ça ne juge pas la qualité des réponses de l'agent** (§8).

### Transformer l'information en barrière

Pour que « sur `main`, seules les PR sont autorisées » devienne vrai, il faut un
**ruleset** côté GitHub (`Settings` → `Rules`), ciblant `main` :

1. ☑️ **Require a pull request before merging** — interdit le push direct
2. ☑️ **Require status checks to pass** → sélectionner **`lint + tests`** (le `name:`
   du job, c'est sous ce libellé exact qu'il apparaît)
3. ☑️ **Require branches to be up to date before merging** — force la PR à intégrer
   `main` avant fusion, donc le check porte sur un merge à jour

⚠️ Le point 2 n'apparaît dans la liste **qu'après au moins une exécution** du
workflow : GitHub ne propose que les checks qu'il a déjà vus. Il faut donc pousser
d'abord, configurer ensuite.

---

## 8. Ce qu'elle garde — et ce qu'on lui refuse délibérément

**Elle garde la mécanique déterministe** : factory LLM, guardrails, couture,
serveur HTTP, persistance. Tout ce qui doit se comporter à l'identique à chaque
exécution.

**Elle ne garde pas la qualité des réponses du modèle.** Les 7 cas de
`test_eval.py` se skippent d'eux-mêmes faute de credentials sur le runner
(`_has_llm_credentials`), et c'est le comportement voulu.

Pourquoi ce refus est un choix et non un renoncement : ces cas interrogent un
**vrai LLM**. Même à `LLM_TEMPERATURE=0`, un modèle n'est pas strictement
déterministe, et l'assertion porte sur un jugement (« l'agent refuse-t-il
honnêtement ? »), pas sur un calcul. Ce sont des tests **flaky** par nature — ils
passent puis échouent sans qu'une ligne bouge. Constaté pendant la mise en place :
`out-of-faq-honest-refusal` a échoué dans une passe complète, puis réussi relancé
seul, sur un code identique.

Or un test flaky ne casse pas la CI : **il casse la confiance dans la CI**. Le
réflexe appris devient « c'est encore lui, relance » — et ce réflexe s'applique
aussi le jour où le rouge est vrai. Brancher une clé LLM en secret GitHub
importerait donc cette instabilité **dans la garde**.

Ces cas ne sont pas des tests unitaires, ce sont des **évaluations**. Leur place
est dans LangSmith via `make eval`, où on lit des **taux**, pas dans une porte
binaire.

### `make score` — une seconde porte, qui ne trahit pas la première

Depuis le chantier MLOps (`TODO_priorities.md` §Chantier 7), la CI lance une
deuxième commande après `make check` :

| Porte | Ce qu'elle dit | Ce qu'elle fait tourner |
|---|---|---|
| `make check` | « le code fait ce qu'on a écrit » | ruff + les 250 tests |
| `make score` | « et il le fait **assez bien pour être livré** » | note les corpus, applique le seuil |

Le brief exige un « blocage de livraison sous seuil de qualité ». La tentation
serait d'y brancher les évaluations live — c'est-à-dire d'importer dans la garde
exactement l'instabilité que la section ci-dessus refuse. **`make score` ne le
fait pas** : par défaut il ne note que les deux dimensions **déterministes**
(mémoire 12/12, garde-fous 35/35), calculées sans LLM, sans clé et sans réseau.
La dimension qualité exige `--live`, donc une clé, donc elle reste au second
étage — visible dans le **rapport**, jamais dans le blocage.

Ce que ça bloque concrètement :

- un **plancher dur** franchi : une dimension déterministe qui n'est pas parfaite.
  Pas de tolérance ici, et c'est justifié — il n'y a aucune variance d'un run à
  l'autre à absorber. Un jailbreak qui passe n'est pas « acceptable à 0,8 » ;
- une **régression contre la baseline** (`data/eval-baseline.json`) de plus d'un
  cas. La tolérance est exprimée en **cas** et non en points, parce qu'un point
  sur un corpus de 7 cas serait un nombre inventé, et parce que le chiffre vient
  d'une mesure : 2 attentes sur 7 varient en surface à fait constant.

Le point à retenir : la note globale n'est **pas** la porte. Ce sont les
planchers durs par dimension et la baseline, précisément parce qu'une moyenne
peut masquer une chute de sécurité.

### Un Postgres jetable dans le job — et une porte qui ne lit pas un chiffre

Depuis le chantier 8, le job `check` démarre un **service** `pgvector/pgvector:pg17`
(la même image que `compose.yaml` — pas `postgres`, qui n'embarque pas l'extension)
et expose `EVAL_DATABASE_URL`. Effet : les 12 cas du corpus mémoire tournent
**deux fois**, une par moteur de persistance.

Pourquoi ça valait un conteneur de plus dans la garde : **R3, c'est l'isolation
entre clients**, une propriété de *sécurité*. Ce qui sépare deux clients —
`memories_namespace(user_id)` — est notre code, commun aux deux moteurs. Mais la
**requête de similarité** qui pourrait ramener la ligne du voisin appartient au
store : sqlite-vec ici, pgvector là. C'est la moitié non partagée, et elle n'était
vérifiée nulle part automatiquement — une fois à la main, à l'étape 3 du
déploiement, et plus jamais. Tout le reste de ce dépôt a converti ses
vérifications manuelles en invariants exécutables ; celle-ci ne l'était pas.

⭐ Et une **quatrième porte**, la seule qui ne regarde pas une note :
`make score ARGS=--require-postgres`. Les trois autres lisent un **chiffre**, et
un chiffre ne peut pas s'apercevoir qu'il a été calculé sur la moitié des
moteurs. Sans ce drapeau, un service qui ne démarre pas — ou un nom de variable
mal orthographié — donnerait un **12/12 vert** couvrant deux fois moins. C'est la
règle que ce dépôt avait déjà écrite ailleurs (`baseline_status`) : *une porte
silencieusement sautée est une porte qu'on croit avoir*.

En local, sans base, rien n'est cassé : `make score` note SQLite, et le rapport
**dit** que Postgres n'a pas été exercé. La différence entre un trou connu et un
angle mort tient entièrement dans cette phrase imprimée.

⚠️ Le `options: --health-cmd` du service n'est pas décoratif : sans lui, GitHub
lance les steps dès que le **conteneur** tourne, pas quand Postgres **accepte les
connexions**. C'est le piège classique de la pièce mobile, et il se paie en
`connection refused` sur une base qui aurait été prête deux secondes plus tard.

---

## 9. Ce que la CI a rapporté avant même d'exister

Simuler la CI (« et si on lançait les tests sans `.env` ? ») a fait tomber un
défaut réel dans `memory/long_term.py`. `get_store()` faisait, dans cet ordre :

```
1. sonder le modèle d'embeddings pour connaître la dimension  ← appel réseau
2. seulement ensuite, vérifier que DATABASE_URL existe
```

Un `DATABASE_URL` manquant n'était donc pas signalé comme tel : il fallait d'abord
**réussir** un appel au fournisseur pour **atteindre** le contrôle censé le
détecter. Sans clé, on mourait avant, sur une erreur de protocole HTTP pointant
vers le mauvais coupable.

**Le correctif :** valider la configuration **avant** toute I/O — nom de backend
inconnu, puis `DATABASE_URL`. Par symétrie avec `get_checkpointer`, qui rejetait
déjà un nom invalide sans rien toucher : une **seule** variable
(`PERSISTENCE_BACKEND`) pilote les deux factories, l'une ne doit pas accepter ce
que l'autre refuse.

Le bénéfice dépasse la CI. Le jour de la bascule `PERSISTENCE_BACKEND=postgres` sur
Azure, un `DATABASE_URL` oublié dira `DATABASE_URL is required…` au lieu d'une
erreur HTTP désignant le fournisseur de LLM. C'est la différence entre un
diagnostic de trente secondes et une fausse piste.

> 🧭 **La leçon de méthode :** une bonne question de CI — « qu'est-ce qui est vrai
> sur une machine qui n'a rien de moi ? » — trouve des défauts avant d'écrire une
> ligne de YAML.

---

## 10. La moitié « image » — CI-2 est faite, seule la publication attend

**Une confusion corrigée le 2026-07-31.** Ce document (et l'en-tête de `ci.yml`)
affirmaient que toute la moitié « image » attendait l'étape 5 « parce qu'elle a
besoin d'un ACR qui n'existe pas encore ». C'était confondre **construire** et
**publier** :

| | Besoin d'Azure ? | État |
|---|---|---|
| **CI-2** — construire en amd64, et vérifier ce qu'on a construit | **non** | ✅ job `images` |
| **CI-3** — pousser sur l'ACR, mettre à jour les Web Apps | oui (registre + secrets) | ⬜ étape 5 |

Le coût de cette confusion aurait été de découvrir un build cassé **au
déploiement**, le jour où le droit Azure tombe — c'est-à-dire au moment où on a
le moins envie de déboguer un `exec format error`.

**Le fait technique qui impose la CI.** Mesuré sur la machine de développement :

```
uname -m                  → arm64        (Apple Silicon)
support-agent:dev         → linux/arm64
client-chainlit:dev       → linux/arm64
```

Les images sont **déjà** Linux (`python:3.12-slim-bookworm`, c'est Debian) — l'OS
n'a jamais été le sujet. Le sujet est l'**architecture CPU**. Azure App Service for
Containers (Linux) exécute du **linux/amd64** ; un binaire compilé pour ARM
(l'interpréteur CPython, le `libpq` de `psycopg[binary]`, l'extension Rust de
`pydantic-core`) ne peut pas s'exécuter sur un x86-64.

> 🔁 Ce raisonnement a été écrit quand la cible d'exécution était Azure Container
> Apps ; elle est devenue **App Service** le 2026-07-28. **Le prérequis ne bouge pas
> d'un iota** — les deux exécutent de l'amd64. C'est plutôt une confirmation : le
> besoin d'un constructeur amd64 ne venait pas du service choisi, il vient de l'écart
> entre un Mac Apple Silicon et le x86-64 des offres Linux managées d'Azure.

⚠️ **Et un registre ne convertit rien.** L'ACR acceptera sans broncher une image
arm64 : le push réussit, le tag s'affiche, tout paraît normal. L'échec n'apparaît
qu'au démarrage de la Web App — `exec format error`, qu'App Service présentera sous
la forme d'un conteneur qui ne répond pas sur son port. Trois étapes vertes auront dit
que tout allait bien. **Le format vient d'où la construction s'exécute, jamais d'où
l'image atterrit.**

Un runner `ubuntu-latest` étant nativement amd64, il produit le binaire exact qui
tournera en production. Rien dans le projet ne s'y oppose : `uv sync` s'exécute
**dans** l'image (donc installe les wheels de la plateforme cible), `uv.lock` est
résolu de façon universelle, et `.dockerignore` exclut déjà le `.venv` de l'hôte.
**La bascule ne demandera aucun changement de Dockerfile** — seulement *où* et
*avec quel `--platform`* on construit.

> 📌 **Règle à tenir quand CI-3 arrivera : la garde tourne partout (toute
> branche), la publication ne tourne que sur `main`.** Vérifier est gratuit et doit
> être omniprésent ; pousser une image sur le registre de production est un effet
> de bord, et il n'a rien à faire depuis une branche de feature.

Le build local (`make docker-up`) reste inchangé : arm64, natif, rapide. Deux
chemins de build, deux buts — **local pour itérer, CI pour produire l'artefact de
production** — et le même Dockerfile dans les deux cas.

### Ce que le job `images` vérifie, au-delà du « ça construit »

Une image qui se construit n'est pas une image qui marche. `make docker-smoke`
(le même en local et en CI) construit les deux images puis leur pose **deux
questions qui n'avaient plus de réponse automatique depuis l'étape 4 du
déploiement** — elles y avaient été vérifiées à la main, une fois :

1. **L'image du client ne contient ni `support_agent`, ni `langchain`, ni
   `langchain_core`, ni `langgraph`.** C'est la preuve du découplage : le client
   parle à l'agent en HTTP, il n'embarque pas son cerveau. `langchain_core` est
   le nom qui compte — c'est lui que traînerait une dépendance transitive
   réintroduite par mégarde.
2. **L'image de l'agent démarre vraiment** : elle refuse de servir sans clé
   (démarrage *fail-closed*), s'échauffe, puis `/health` et `/ready` répondent, et
   un `POST /chat` sans `X-API-Key` reçoit **401**.

**Le point délicat, et pourquoi ce n'est pas de la triche.** Le *lifespan* de
l'agent construit l'index FAQ **avant** d'ouvrir son port : sans modèle
d'embeddings joignable, le conteneur ne démarre pas du tout, et le test
n'assèrerait plus que l'absence d'une clé. Le script sert donc l'**unique
dépendance réseau du démarrage** depuis un **stub local** parlant l'API
d'embeddings OpenAI, branché par le rail `openai_compatible` **qui existe déjà**
dans le projet. Rien d'autre n'est simulé : même image, même entrée, même
démarrage fail-closed, vraie FAQ, vrai vector store, vrai HTTP. Et **aucun
secret** — c'est ce qui permet à cette garde de tourner sur chaque branche, comme
le reste de la CI. Ce qu'elle ne fait volontairement **pas** : envoyer un message.
Y répondre demande un vrai modèle, et la qualité est le métier de `make score`.

> 🔎 **Trouvé en route, et ça vaut pour l'étape 5.** L'échauffement mesuré sur ce
> rail (15 à 25 s selon les runs — et cette dispersion est déjà un indice) contient
> **7,3 s de `tiktoken` qui télécharge `cl100k_base` depuis
> un CDN OpenAI** — un appel que `langchain-openai` fait pour découper le texte,
> vers un hôte qui n'a **rien à voir** avec l'endpoint configuré. Vérifié, pas
> déduit : le même import sous `docker run --network none` échoue sur
> `openaipublic.blob.core.windows.net`. Sans conséquence aujourd'hui (le rail par
> défaut est `mistral`, qui n'utilise pas tiktoken), mais un déploiement **Azure
> OpenAI derrière un VNet fermé ne démarrerait pas** tant que ce CDN n'est pas
> joignable ou l'encodage embarqué dans l'image. Le ~4,4 s de démarrage à froid du
> plan de déploiement reste juste : il a été mesuré sur le rail `mistral`.

---

## 11. Le coût

Gratuit et illimité pour un dépôt **public**. Pour un dépôt **privé**, le plan Free
donne 2 000 minutes/mois. À une ou deux minutes par run, il faudrait plusieurs
dizaines de pushes par jour pour s'en approcher.

Le service Postgres du chantier 8 est la première dépense assumée : quelques dizaines
de secondes de plus (démarrage de l'image + le corpus mémoire joué deux fois),
contre une propriété de sécurité vérifiée sur le moteur de production au lieu de
l'autre. Le marché était vite fait.

La seconde est le job `images` : il construit **deux images sans cache de build**.
C'est délibéré — utiliser le cache GitHub imposerait un `docker buildx build
--cache-from …`, donc une commande **différente** de celle du `Makefile`, et le
vert de la CI cesserait de dire quoi que ce soit de `make docker-smoke` en local.
Quelques minutes de runner contre une porte d'entrée unique (CLAUDE.md) : le même
marché. Il tourne **en parallèle** de `check`, pas après — les deux répondent à
des questions indépendantes, et aucune n'est le prérequis de l'autre.

---

## En une phrase

**À chaque push et à chaque PR, une machine neuve qui n'a rien de toi vérifie que
le dépôt tient debout tout seul, et colle le verdict sur le commit.** Ce qu'on fait
de ce verdict — le lire, ou en faire une barrière — est une décision séparée, qui se
prend dans les réglages du dépôt.
