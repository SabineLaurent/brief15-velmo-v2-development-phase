# Déployer Velmo 2.0 sur Azure — tutoriel pas à pas au portail

**Objet :** exécuter la partie **« Développement »** du brief (compétences 4 à 9) en mode
**conteneurs Docker**, avec le **portail web Azure** comme seul outil de déploiement.
**Date :** 2026-08-24
**Public :** moi, qui reprends le projet après quinze jours et qui n'ai jamais provisionné
sur Azure.
**Se lit avec :** [`plan-deploiement-2026-07-25.md`](plan-deploiement-2026-07-25.md) — ce
document-ci est l'**exécution** de son étape 5. Le *pourquoi* des choix est là-bas ; ici,
c'est **où cliquer**.

> Toutes les affirmations sur les écrans Azure de ce document ont été vérifiées sur la
> documentation Microsoft Learn le 2026-08-24 (liens en §14). Azure renomme ses écrans
> souvent : si un libellé diffère, le **chemin** reste juste, cherche le mot-clé dans la
> barre de recherche du portail.

---

## 1. La seule mauvaise nouvelle, dite tout de suite

**Le portail Azure ne sait pas construire une image Docker.** Aucun bouton, nulle part.
Il sait *stocker* une image (ACR), *l'exécuter* (App Service), la *rebâtir depuis un
dépôt Git* (ACR Tasks) — mais fabriquer l'image à partir de ton `Dockerfile`, non.

Et il y a un second obstacle, propre à ta machine :

| Fait | Conséquence |
|---|---|
| Ton Mac est **arm64** (vérifié : `uname -m` → `arm64`) | Un `docker build` local produit une image **arm64** |
| App Service Linux exécute du **linux/amd64** | Cette image **ne démarrera pas** sur Azure |

Le message d'erreur que tu obtiendrais est celui décrit dans le plan de déploiement
(§5, piège 3) : *« Container didn't respond to HTTP pings on port: 8000 »* — un message
qui ne nomme jamais la vraie cause.

**Donc : la consigne « au clic » s'applique intégralement au déploiement Azure
(étapes 2 à 10 ci-dessous, 100 % portail). La fabrication des deux images demande, elle,
un choix entre trois voies — c'est l'étape 3, et j'y recommande explicitement l'une
d'elles.** Ce n'est pas contourner la consigne : construire une image n'est pas déployer
sur Azure, c'est produire l'artefact **avant** Azure. C'est exactement la frontière que
le brief nomme « préparation des images ».

---

## 2. Le plan d'action — 10 étapes

Une étape = une ressource ou une bascule, **et une preuve**. On n'ouvre jamais deux
inconnues à la fois : c'est toute la logique de l'ordre ci-dessous (justifié au §6 du plan
de déploiement, « ordre B »).

| # | Action | Où | Durée | Preuve que c'est fait |
|---|---|---|---|---|
| 0 | Fixer les noms et la région | tableur / ce doc | 10 min | Le tableau §3 est rempli |
| 1 | Groupe de ressources `rg-velmo-prod` | Portail | 2 min | Il apparaît dans « Resource groups » |
| 2 | Ressource **Foundry** + 2 déploiements de modèles | Portail + Foundry | 20 min | Le playground répond |
| 3 | **ACR** + les 2 images poussées en **amd64** | Portail (+ build) | 30 min | 2 dépôts visibles dans « Repositories » |
| 4 | **PostgreSQL Flexible Server** + `pgvector` autorisé | Portail | 15 min (attente) | `azure.extensions` contient `VECTOR` |
| 5 | **App Service Plan** + Web App `agent-api` en `memory` | Portail | 20 min | `GET /health` répond `200` |
| 6 | Bascule `PERSISTENCE_BACKEND=postgres` | Portail | 5 min | La mémoire survit à un redémarrage |
| 7 | Web App `client` (Chainlit) | Portail | 15 min | Conversation complète dans le navigateur |
| 8 | **Key Vault** + secrets référencés | Portail | 20 min | Les app settings affichent la pastille verte |
| 9 | Journaux + signaux de suivi | Portail | 15 min | Log stream lisible, latences relevées |
| 10 | Tests d'acceptance en ligne | Navigateur | 20 min | R2, R3, garde-fous : les 5 cas passent |

**Durée réaliste : une demi-journée**, dont ~15 min d'attente passive (création du
serveur Postgres) qu'on met à profit pour l'étape 3.

**Ordre non négociable :** l'étape 4 (Postgres) se **lance** tôt parce qu'elle est lente,
mais l'agent ne s'y **branche** qu'à l'étape 6. L'étape 5 déploie l'agent en
`PERSISTENCE_BACKEND=memory` : si elle échoue, la cause est l'image, l'ingress, le
provider LLM ou les secrets — **jamais** la base. Un échec à l'étape 6 ne peut plus être
que la base. Un suspect à la fois.

---

## 3. Avant de toucher au portail — fige tes noms

Remplis cette colonne maintenant. Tu vas retaper ces valeurs vingt fois, et une
incohérence de nom est la première cause de perte de temps.

| Élément | Contrainte Azure | Ma valeur |
|---|---|---|
| Groupe de ressources | libre | `rg-velmo-prod` |
| Région principale | France Central ou Sweden Central | `………` |
| Registre ACR | 5–50 car., **alphanumérique, sans tiret**, unique dans Azure | `acrvelmoprod………` |
| Serveur Postgres | minuscules/chiffres/tirets, unique dans Azure | `pg-velmo-prod-………` |
| Admin Postgres | 1–63 car., pas de `pg_` | `velmoadmin` |
| Mot de passe Postgres | 8–128 car., 3 classes | *(coffre / gestionnaire de mots de passe)* |
| Web App agent | unique dans Azure → `https://<nom>.azurewebsites.net` | `velmo-agent-………` |
| Web App client | idem | `velmo-client-………` |
| Ressource Foundry | unique | `foundry-velmo-………` |
| Key Vault | 3–24 car., unique | `kv-velmo-………` |
| Clé de service `API_KEY` | ta valeur, longue et aléatoire | *(à générer)* |

**Sur la région — un piège qui coûte une heure.** Le brief demande France Central ou
Sweden Central. Mais **tous les modèles ne sont pas disponibles dans toutes les régions.**
Fais l'étape 2 **en premier** : si ton modèle n'est pas proposé en France Central, crée la
ressource Foundry en **Sweden Central** et laisse le reste en France Central. Ce n'est pas
un problème : l'agent appelle le modèle en HTTPS, la région n'a aucune importance
fonctionnelle (juste quelques dizaines de millisecondes de latence).

**Générer la clé de service** (dans ton terminal, ce n'est pas du déploiement) :

```bash
python -c "import secrets; print(secrets.token_urlsafe(32))"
```

Note-la : elle servira **deux fois**, comme `API_KEY` sur l'agent et comme
`AGENT_API_KEY` sur le client. Deux variables, une valeur — c'est la même clé vue du
serveur qui la vérifie et de l'appelant qui la présente.

**Ordre de grandeur du coût** (à vérifier sur le calculateur Azure, les prix bougent) :
plan App Service **B1** ≈ 13 €/mois, Postgres **B1ms** ≈ 12–15 €/mois, ACR **Basic**
≈ 4 €/mois, Foundry à l'usage (quelques centimes pour ce projet). **Le plan et la base se
paient même quand personne ne parle à l'agent** — d'où l'étape 11 (supprimer le groupe).

---

## 4. Étape 1 — Le groupe de ressources

Un seul groupe pour tout : c'est un critère de performance du brief (« facile à retrouver
et à supprimer »), et c'est ce qui te permettra de tout effacer d'un clic à la fin.

1. https://portal.azure.com → connecte-toi.
2. Barre de recherche du haut → tape **`Resource groups`** → ouvre-le.
3. **`+ Create`**.
4. Remplis :
   - **Subscription** : ton abonnement de formation.
   - **Resource group** : `rg-velmo-prod`.
   - **Region** : ta région principale.
5. **`Review + create`** → **`Create`**.

✅ **Preuve :** il apparaît dans la liste « Resource groups ». Garde cet onglet ouvert :
c'est ton tableau de bord pour toute la suite, et c'est **la capture d'écran** demandée
dans les livrables (à refaire à la fin, quand les cinq ressources y seront).

---

## 5. Étape 2 — Le service d'IA : ressource Foundry + deux déploiements

C'est la brique que ton code attend, et il l'attend **sans modification** : `config.py`
porte déjà `llm_provider = "openai_compatible"` et `llm_inference_endpoint`. Le bénéfice
annoncé au §5 du plan de déploiement (« le LLM passe chez Azure sans une ligne de code »)
s'encaisse ici.

⚠️ **Vocabulaire, parce que Microsoft a tout renommé en 2025-2026 :** « Azure OpenAI »,
« Azure AI Services », « Azure AI Foundry » et « Microsoft Foundry » désignent des couches
qui se recouvrent. Ce qu'il te faut : **une ressource qui expose un endpoint parlant
l'API OpenAI**, et **deux déploiements** dessus (un modèle de chat, un modèle
d'embeddings).

### 5.1 Créer la ressource

1. Portail → **`Create a resource`** → cherche **`Azure AI Foundry`** (ou
   `Azure OpenAI` — les deux mènent à une ressource utilisable).
2. **`Create`**, puis onglet **Basics** :
   - **Subscription** / **Resource group** → `rg-velmo-prod`.
   - **Region** → ta région, **ou celle où ton modèle existe** (voir l'avertissement §3).
   - **Name** → `foundry-velmo-…`.
   - **Pricing tier** → `Standard S0`.
3. **`Next`** jusqu'à **`Review + create`** → **`Create`**. Compte 1 à 3 minutes.

### 5.2 Déployer les deux modèles

1. Sur la ressource → **`Go to Azure AI Foundry portal`** (ou **`Go to Foundry portal`**),
   un site distinct qui s'ouvre dans un nouvel onglet.
2. Menu de gauche → **`Deployments`** (ou **`Model deployments`**).
3. **`+ Deploy model`** → **`Deploy base model`**.
4. **Premier déploiement — le chat.** Choisis un modèle de chat disponible dans ta région
   (`gpt-4o-mini` est le bon compromis coût/qualité pour du support). **`Confirm`**.
   - **Deployment name** : retiens-le **exactement**. C'est lui qui ira dans `LLM_MODEL`,
     **pas** le nom du modèle. Ton `.env.example` le dit déjà : *« Pour Azure : le NOM DU
     DÉPLOIEMENT créé dans Foundry »*.
5. **Second déploiement — les embeddings.** Répète avec un modèle d'embeddings
   (`text-embedding-3-small`). Son nom de déploiement ira dans `EMBEDDINGS_MODEL`.

> **Pourquoi deux.** L'agent fait du RAG sur la FAQ Velmo **et** de la mémoire long terme
> vectorielle : `long_term.py` sonde les embeddings **au démarrage**. Sans déploiement
> d'embeddings, la Web App ne démarrera pas du tout — et le message d'Azure ne le dira
> pas. C'est le maillon inconditionnel identifié au §6 du plan de déploiement.

### 5.3 Relever endpoint et clé

Dans le portail Foundry, page de la ressource ou du déploiement, cherche
**`Endpoint`** / **`Keys and Endpoint`**. Note :

| Ce que tu relèves | Ce que tu en fais |
|---|---|
| L'URL de base, `https://<ressource>.openai.azure.com` ou `https://<ressource>.services.ai.azure.com` | Tu y **ajoutes `/openai/v1`** → c'est `LLM_INFERENCE_ENDPOINT` |
| **Key 1** | `LLM_INFERENCE_API_KEY` (ira au Key Vault, étape 8) |
| Le **nom du déploiement** chat | `LLM_MODEL` |
| Le **nom du déploiement** embeddings | `EMBEDDINGS_MODEL` |

La valeur finale ressemble donc à :

```
https://foundry-velmo-abc.openai.azure.com/openai/v1
```

Les deux formes d'hôte sont acceptées sur la route `/openai/v1/` (documenté par
Microsoft). Ce suffixe **`/openai/v1`** n'est pas décoratif : c'est la route à
versionnement implicite, celle qui rend l'endpoint compatible avec le SDK OpenAI standard
— donc avec `init_chat_model(model_provider="openai", base_url=…)` de ta `factory.py`.
Sans lui, tu obtiendrais des 404 dans les logs du conteneur.

✅ **Preuve :** dans le portail Foundry, ouvre le **playground** (`Chat`) sur ton
déploiement et envoie « bonjour ». S'il répond, l'IA est prête. **Fais-le maintenant** :
c'est cinq secondes ici, contre vingt minutes de diagnostic si tu ne découvres le problème
qu'au démarrage du conteneur.

---

## 6. Étape 3 — Le registre ACR et la préparation des images

### 6.1 Créer le registre

1. Portail → **`Create a resource`** → **`Containers`** → **`Container Registry`** →
   **`Create`**.
2. Onglet **Basics** :
   - **Resource group** → `rg-velmo-prod`.
   - **Registry name** → `acrvelmoprod…` — ⚠️ **pas de tiret**, 5 à 50 caractères
     alphanumériques.
   - **Location** → ta région.
   - **Pricing plan** → **`Basic`** (suffisant : deux images de ~500 Mo).
   - **Domain name label scope** → **`Unsecure`**. Ce n'est pas un mauvais choix de
     sécurité ici, c'est le choix *lisible* : ton serveur de connexion sera exactement
     `acrvelmoprod.azurecr.io`, sans hash ajouté. Les autres options (`Tenant Reuse`…)
     collent un hash au nom DNS, **définitivement** — et tu devras le reporter partout.
3. **`Review + create`** → **`Create`**.

### 6.2 Activer l'utilisateur admin

C'est ce qui permettra à App Service de tirer l'image sans que tu configures d'identité
managée (laquelle exige de la ligne de commande ou du RBAC à la main).

1. Ouvre le registre → menu de gauche, **`Settings`** → **`Access keys`**.
2. Bascule **`Admin user`** sur **`Enabled`**.
3. Note **`Login server`**, **`Username`**, **`password`**.

> ⚠️ Ce mot de passe **est un secret**. App Service le rangera automatiquement dans
> `DOCKER_REGISTRY_SERVER_PASSWORD`, une variable qu'il **n'expose jamais à
> l'application** (documenté par Microsoft) — le brief est donc respecté sur ce point.

### 6.3 Fabriquer les deux images en amd64 — trois voies

Rappel du §1 : c'est ici, et **seulement** ici, que le portail ne suffit pas.

| Voie | Ligne de commande ? | Fiabilité pour **ton** projet | Verdict |
|---|---|---|---|
| **A. `docker buildx` local** | 3 commandes | ✅ Élevée : ton `Dockerfile` est déjà écrit pour BuildKit, et `--platform` règle l'arm64 | ✅ **Recommandé** |
| **B. ACR Task depuis GitHub** | 0 (100 % portail) | ⚠️ **Risquée ici** : tes deux `Dockerfile` utilisent `RUN --mount=type=cache` et `--mount=type=bind`, or **BuildKit dans ACR Tasks est en préversion** et demande `DOCKER_BUILDKIT=1` dans une tâche YAML multi-étapes. Un build simple échouera probablement | ⚠️ Plan B |
| **C. GitHub Actions → ACR** | 0 après écriture du YAML | ✅ Élevée (ta CI construit **déjà** les images en amd64) mais demande d'écrire un workflow et un secret | 🔵 La bonne cible plus tard |

**Pourquoi A malgré la consigne.** Trois raisons, dans cet ordre. Ce sont **trois
commandes, exécutées une fois**, contre un débogage de préversion Azure. Elles ne
*déploient* rien : elles produisent l'artefact, et tout Azure reste au clic. Et
surtout : c'est la voie où l'image qui tourne en production est **exactement** celle que
tu as vérifiée en local — la garantie centrale du plan de déploiement (« sans que l'image
change entre ma machine et le cloud »).

**Voie A, dans ton terminal, à la racine du dépôt :**

```bash
# 1. S'authentifier auprès du registre (le mot de passe est celui d'Access keys)
docker login acrvelmoprod.azurecr.io -u acrvelmoprod

# 2. L'agent — noter --platform linux/amd64, c'est TOUT l'enjeu
docker buildx build --platform linux/amd64 \
  -f packages/support-agent/Dockerfile \
  -t acrvelmoprod.azurecr.io/support-agent:v1 --push .

# 3. Le client
docker buildx build --platform linux/amd64 \
  -f packages/client/Dockerfile \
  -t acrvelmoprod.azurecr.io/client-chainlit:v1 --push .
```

Trois remarques qui t'éviteront de chercher :

- **Le `.` final est le contexte de build, et c'est la racine du dépôt.** Les deux
  `Dockerfile` l'exigent (ils ont besoin de `uv.lock` et du `pyproject.toml` racine) —
  c'est écrit en commentaire en tête de chacun.
- **Le tag `v1`, pas `latest`.** Avec `latest`, tu ne sauras jamais quelle image tourne, et
  App Service ne redéploiera pas de façon prévisible. Un tag par publication.
- **Le même tag sur les deux images.** C'est la règle « monter bloc par bloc, publier par
  jeu » du plan de déploiement : un seul `uv.lock`, donc un seul jeu de dépendances testé
  ensemble.

Le premier build prend plusieurs minutes (émulation amd64 sur un Mac ARM). Les suivants
sont rapides.

✅ **Preuve, au portail :** registre → **`Services`** → **`Repositories`**. Tu dois voir
**`support-agent`** et **`client-chainlit`**, chacun avec le tag `v1`. Clique sur un tag :
la colonne **`Architecture`** doit afficher **`amd64`**. Si elle affiche `arm64`, tu as
oublié `--platform` — refais le build, ne va pas plus loin, la Web App ne démarrerait
jamais.

---

## 7. Étape 4 — PostgreSQL Flexible Server et pgvector

On la lance **maintenant** (c'est long : 5 à 10 minutes) même si l'agent ne s'y branchera
qu'à l'étape 6.

### 7.1 Créer le serveur

1. Portail → **`Create a resource`** → **`Databases`** →
   **`Azure Database for PostgreSQL flexible server`** → **`Create`**.
2. Onglet **Basics** :

| Champ | Valeur | Pourquoi |
|---|---|---|
| **Resource group** | `rg-velmo-prod` | |
| **Server name** | `pg-velmo-prod-…` | Devient `….postgres.database.azure.com` |
| **Region** | ta région | |
| **PostgreSQL version** | `16` ou `17` | Ta pile locale est testée sur **pg17** (`pgvector/pgvector:pg17`) |
| **Workload type** | **`Development`** | Donne les SKU *Burstable* — le moins cher |
| **Compute + storage** | `Configure server` → **Burstable**, `B1ms` | Suffisant pour une démo |
| **Availability zone** | `No preference` | |
| **High availability** | **décoché** | Doublerait la facture pour rien ici |
| **Authentication method** | **`PostgreSQL authentication only`** | Ton code se connecte par chaîne de connexion, pas par Entra ID |
| **Admin username** | `velmoadmin` | |
| **Password** | *(le tien)* | ⚠️ lis l'avertissement §7.4 avant de choisir |

3. Onglet **Networking** — **le choix décisif, non modifiable après création** :
   - **Connectivity method** → **`Public access (allowed IP addresses)`**.
   - Coche **`Allow public access to this resource through the internet using a public IP address`**.
   - Coche **`Allow public access from any Azure service within Azure to this server`**
     → **c'est cette case qui laissera passer ta Web App.** Sans elle, l'étape 6 échouera
     sur un timeout de connexion.
   - Clique **`+ Add current client IP address`** → te permettra de te connecter pour
     vérifier.

   > *Private access (VNet)* serait plus propre en vraie production. Ici, ce serait
   > s'infliger une intégration VNet **et** ranimer le piège `tiktoken` du plan de
   > déploiement (§5, note du 2026-07-31 : sortie réseau restreinte = Web App qui ne
   > démarre pas). Choix assumé pour un projet de formation.

4. **`Review + create`** → **`Create`**. Puis va faire l'étape 5 pendant l'attente.

### 7.2 Autoriser pgvector — l'étape qu'on oublie

C'est **la** chausse-trappe annoncée au §6 du plan de déploiement. Sur Azure, une
extension doit être mise en liste blanche **avant** que `CREATE EXTENSION` fonctionne.
Sans ça, `setup()` du store long terme échoue — au pire moment, au démarrage du conteneur.

1. Serveur créé → menu de gauche, **`Settings`** → **`Server parameters`**
   (parfois **`Parameters`**).
2. Cherche **`azure.extensions`**.
3. Dans la liste déroulante, coche **`VECTOR`**.
4. **`Save`**. Un déploiement se lance ; attends **`Go to resource`**.

✅ **Preuve :** recharge la page des paramètres — `azure.extensions` doit afficher
`VECTOR`.

### 7.3 Créer la base `agent`

1. Menu de gauche → **`Settings`** → **`Databases`**.
2. **`+ Add`** → **Name** : `agent` → **`Save`**.

Le schéma `agent_state` et ses huit tables, eux, seront créés **par ton code** au premier
démarrage (`setup()`). Tu n'as rien à faire de plus : l'utilisateur admin en a le droit.

### 7.4 Composer la chaîne de connexion

C'est la valeur de `DATABASE_URL`. Forme exacte :

```
postgresql://velmoadmin:LEMOTDEPASSE@pg-velmo-prod-xxx.postgres.database.azure.com:5432/agent?sslmode=require
```

Quatre pièges, tous vécus par d'autres avant toi :

1. **`?sslmode=require` est obligatoire.** Azure impose TLS. Ton `.env.example` le note
   déjà.
2. **L'utilisateur est `velmoadmin`, pas `velmoadmin@pg-velmo-prod-xxx`.** La syntaxe
   `user@serveur` était celle de l'ancien *Single Server*. Sur *Flexible Server*, elle
   échoue.
3. **Un mot de passe à caractères spéciaux doit être URL-encodé** (`@` → `%40`,
   `#` → `%23`, `/` → `%2F`…). Le plus simple : **choisis un mot de passe long sans
   caractère spécial autre que `-` et `_`**. Tu t'épargnes une panne dont le message ne
   parlera que d'authentification.
4. **`:5432`, pas `:6432`.** Le port 6432 est celui du PgBouncer intégré. Le plan de
   déploiement note que le pooling appellera `prepare_threshold=0` (finding P1 de la revue
   d'escalade) — un sujet non traité dans le code aujourd'hui. Reste sur 5432.

---

## 8. Étape 5 — Le plan App Service et la Web App `agent-api`

### 8.1 Créer la Web App (le plan se crée au passage)

1. Portail → recherche **`App Services`** → **`+ Create`** → **`Web App`**.
2. Onglet **Basics** :

| Champ | Valeur |
|---|---|
| **Resource group** | `rg-velmo-prod` |
| **Name** | `velmo-agent-…` → donne `https://velmo-agent-….azurewebsites.net` |
| **Publish** | **`Container`** ← *le clic qui décide de tout* |
| **Operating System** | **`Linux`** |
| **Region** | ta région |
| **Linux Plan** | **`Create new`** → `plan-velmo-prod` |
| **Pricing plan** | **`Basic B1`** |

> **Sur le tier.** La documentation Microsoft propose `F1` (gratuit) pour les conteneurs
> Linux. Évite-le ici : 1 Go de RAM pour un conteneur qui charge LangChain, LangGraph et
> un pool psycopg, c'est la panne mémoire aléatoire — celle qui n'apparaît pas au
> démarrage mais à la troisième conversation. **B1** (1,75 Go) est le premier tier
> raisonnable. Il porte **les deux** Web Apps.

3. **Onglet `Container`** (en haut) :
   - **Image Source** → **`Azure Container Registry`**.
   - **Registry** → `acrvelmoprod`.
   - **Image** → `support-agent`.
   - **Tag** → `v1`.
4. **`Review + create`** → **`Create`**.

> **Deux Web Apps, pas une.** Une App Service exécute **un** conteneur applicatif. Tes
> trois services Compose deviennent donc **deux Web Apps sur le même plan** plus le
> Flexible Server. Le Compose ne co-localisait rien : il déclarait trois blocs joignables
> par le réseau. Rien n'est perdu.

### 8.2 Renseigner les app settings

**`Settings`** → **`Environment variables`** → onglet **`App settings`** →
**`+ Add`** pour chaque ligne. (Selon la version du portail : **`Configuration`** →
**`Application settings`**.)

| Nom | Valeur | Rôle |
|---|---|---|
| `WEBSITES_PORT` | `8000` | **Indispensable.** Dit au routeur d'Azure vers quel port pousser le trafic |
| `LLM_PROVIDER` | `openai_compatible` | Le rail Azure de ta factory |
| `LLM_MODEL` | *nom du déploiement chat* | Pas le nom du modèle |
| `LLM_INFERENCE_ENDPOINT` | `https://….openai.azure.com/openai/v1` | Étape 2.3 |
| `LLM_INFERENCE_API_KEY` | *la clé Foundry* | → Key Vault à l'étape 8 |
| `EMBEDDINGS_PROVIDER` | `openai_compatible` | Réutilise endpoint + clé ci-dessus |
| `EMBEDDINGS_MODEL` | *nom du déploiement embeddings* | |
| `KNOWLEDGE_DIR` | `/app/data/kb-velmo` | Chemin **absolu** dans l'image |
| `PERSISTENCE_BACKEND` | **`memory`** | ← on bascule à l'étape 6, pas maintenant |
| `SUPPORT_BACKEND` | `memory` | Le double métier reste en mémoire |
| `API_KEY` | *ta clé de service* | → Key Vault |
| `API_ALLOW_UNAUTHENTICATED` | `false` | ⚠️ **jamais `true` en ligne** |
| `GUARDRAILS_ENABLED` | `true` | Le brief l'exige (critère de performance) |
| `WEBSITE_WARMUP_PATH` | `/ready` | Azure attend la fin de l'indexation FAQ |
| `WEBSITE_WARMUP_STATUSES` | `200` | |

**`Apply`** → **`Confirm`**. La Web App redémarre : c'est normal, **tout** changement
d'app setting redémarre l'application.

⚠️ **`WEBSITES_PORT` n'existe pas dans le conteneur.** Ce n'est pas une variable que ton
code peut lire — c'est une instruction au routeur d'Azure. Ton image fait déjà
`EXPOSE 8000` et `uvicorn --host 0.0.0.0 --port 8000` : on déclare `WEBSITES_PORT`
explicitement plutôt que de compter sur la lecture de l'`EXPOSE`.

### 8.3 Brancher le health check sur le bon endpoint

1. **`Monitoring`** → **`Health check`**.
2. **`Enable`**, et **Path** → **`/health`**.
3. **`Save`**.

> **Ne les inverse pas.** `server.py` expose **deux** endpoints distincts, et App Service
> a **deux** hooks qui leur correspondent exactement :
>
> | Hook Azure | Endpoint | Question posée |
> |---|---|---|
> | **Warm-up** (`WEBSITE_WARMUP_PATH`) | **`/ready`** | « Le graphe est-il construit et la FAQ indexée ? » |
> | **Health check** (cet écran) | **`/health`** | « Suis-je vivant ? » (ne touche aucune dépendance) |
>
> Mettre `/ready` en *health check* ferait sortir de la rotation un conteneur simplement
> en train de chauffer. C'est le bug classique, et il est silencieux.

### 8.4 Vérifier — et savoir où regarder quand ça casse

1. **`Overview`** → **`Default domain`** → ouvre `https://velmo-agent-….azurewebsites.net/health`.
   Attendu : `{"status":"ok"}`.
2. Puis `/ready` → `{"ready":true}`.

Le premier démarrage prend une à deux minutes (tirage de l'image, ~500 Mo) plus le
warm-up (**4,4 s mesurés en conteneur**, très loin des 230 s de la limite de démarrage).

❌ **Si tu obtiens une page « Application Error »**, ne touche à rien avant d'avoir lu les
logs. **`Monitoring`** → **`Log stream`**, ou
**`Deployment`** → **`Deployment Center`** → **`Logs`** (les logs du conteneur sont aussi
sur `https://<app>.scm.azurewebsites.net/api/logs/docker`).

| Ce que dit Azure | Ce que c'est en vrai |
|---|---|
| *« Container didn't respond to HTTP pings on port: 8000 »* | Message **inutile** : le port ne s'est jamais ouvert. `uvicorn` n'ouvre son port qu'**après** le `lifespan`, qui construit le graphe et sonde les embeddings. La vraie cause est **au-dessus**, dans le log du conteneur : provider injoignable, clé fausse, nom de déploiement erroné |
| `exec format error` | Image **arm64**. Retour à l'étape 3 |
| `401` / `403` du provider | `LLM_INFERENCE_API_KEY` ou l'endpoint (le `/openai/v1` oublié) |
| `GET /robots933456.txt 404` | **Normal, ignore.** C'est la sonde de disponibilité d'Azure ; un 404 lui suffit |

Le commit `1c9e044` (config validée avant tout I/O) est précisément ce qui garantit
qu'une erreur de configuration soit **nommée en clair** dans ce log.

✅ **Preuve de l'étape :** `/health` et `/ready` répondent `200`. Tu as une URL publique
HTTPS. **Livrable du brief obtenu** (compétence 5).

---

## 9. Étape 6 — Rendre la mémoire persistante (R2) et isolée (R3)

Le geste coûte **une variable**. C'est précisément le bénéfice pour lequel
`persistence_backend` existe.

1. Web App `agent-api` → **`Environment variables`** → **`App settings`**.
2. **Modifie** `PERSISTENCE_BACKEND` : `memory` → **`postgres`**.
3. **Ajoute** :
   - `DATABASE_URL` = la chaîne composée au §7.4.
   - `DATABASE_SCHEMA` = `agent_state`.
4. **`Apply`** → **`Confirm`**. Redémarrage.

⚠️ **Si tu utilises des slots de déploiement plus tard**, coche **`Deployment slot
setting`** (*sticky*) sur `PERSISTENCE_BACKEND` et `DATABASE_URL`. Un swap échange les app
settings par défaut : un slot de test sur `memory` swappé en production emporterait
`memory` avec lui, et l'agent perdrait sa mémoire **sans une seule erreur**.

✅ **Preuve, en trois temps — et c'est l'ordre qui fait la preuve :**

1. **Log stream** : tu dois voir la création du schéma `agent_state` et le lancement du
   *sweeper* TTL. Aucune erreur `CREATE EXTENSION vector` → le §7.2 a bien été fait.
2. **Persistance inter-session (R2)** : parle à l'agent (étape 7 ou `curl` ci-dessous),
   donne-lui un fait (« ma couleur préférée est le vert »). Puis
   **`Overview`** → **`Restart`**. Redemande avec le **même `user_id`** et un
   **`thread_id` neuf** : il doit répondre « le vert ».
3. **Isolation (R3)** — *la vérification qui manque le plus souvent* : même question avec
   un **autre `user_id`**. Il doit répondre qu'il ne connaît pas ta couleur. Sans cette
   contre-épreuve, tu confondrais « la mémoire marche » avec « la mémoire fuit ».

```bash
# Test direct de l'API, sans passer par l'UI (c'est du test, pas du déploiement)
curl -N -X POST https://velmo-agent-xxx.azurewebsites.net/chat \
  -H "Content-Type: application/json" \
  -H "X-API-Key: TA_CLE_DE_SERVICE" \
  -d '{"message":"ma couleur préférée est le vert","user_id":"sabine","thread_id":"t1"}'
```

*(Vérifie le nom exact du header d'authentification dans `server.py:83` `require_api_key`
si tu obtiens un `401`.)*

---

## 10. Étape 7 — La Web App `client` (Chainlit)

Le client passe **en dernier**, toujours : il ne connaît que deux variables, donc il n'a
rien à prouver que les étapes précédentes n'aient déjà prouvé. Le tester avant l'agent
reviendrait à diagnostiquer une UI pour un problème de cerveau.

1. **`App Services`** → **`+ Create`** → **`Web App`**.
2. **Basics** : `rg-velmo-prod`, **Name** `velmo-client-…`, **Publish** = **`Container`**,
   **Linux**, et surtout **Linux Plan** → **sélectionne le plan existant
   `plan-velmo-prod`** (ne crée pas un second plan : tu paierais deux fois).
3. **Container** : ACR → **Image** `client-chainlit` → **Tag** `v1`.
4. **`Review + create`** → **`Create`**.
5. **App settings** — trois lignes, c'est tout :

| Nom | Valeur |
|---|---|
| `WEBSITES_PORT` | `8000` |
| `AGENT_API_URL` | `https://velmo-agent-….azurewebsites.net` *(sans slash final)* |
| `AGENT_API_KEY` | *la même valeur que `API_KEY` de l'agent* |

**Ce que le client ne reçoit PAS est le vrai bénéfice de sécurité :** pas de clé LLM, pas
de `DATABASE_URL`, pas de `KNOWLEDGE_DIR`. Si l'UI est compromise, elle ne divulgue ni le
modèle ni la base.

6. **WebSockets.** Chainlit en dépend entièrement. Sur **App Service Linux**, les
   WebSockets sont **actifs par défaut**. Vérifie tout de même :
   **`Settings`** → **`Configuration`** → **`General settings`** → **`Web sockets`** =
   **`On`** (si le réglage est présent — il ne l'est pas toujours sur Linux, ce qui est
   normal).
7. Laisse **`ARR affinity`** sur **`On`** (défaut). À une instance, ça ne change rien ; si
   tu montes en instances, c'est la *session affinity* que Chainlit exige.

✅ **Preuve :** ouvre `https://velmo-client-….azurewebsites.net`. L'UI Chainlit s'affiche,
tu poses une question de la FAQ Velmo (« quels sont les délais de livraison ? »), et tu
obtiens une réponse **sourcée**. Puis un second tour qui s'appuie sur le premier
(mémoire courte à travers le réseau). **Le parcours complet est en ligne.**

❌ **Bulle vide ou « le service a refusé la demande »** → l'invariant du client tient
(un chunk non vide dans tous les cas), et le message te dit lequel des deux problèmes tu
as :

| Message affiché | Cause |
|---|---|
| « le service a refusé la demande » | `AGENT_API_KEY` ≠ `API_KEY`. Le serveur a répondu **401** |
| « momentanément injoignable » | `AGENT_API_URL` fausse. **Aucune ligne** dans les logs de l'agent — c'est un échec de *transport*, pas un refus |

Cette table est celle de la contre-épreuve du plan de déploiement (§6, étape 4) : elle
avait été établie en local, elle vaut identiquement en ligne.

---

## 11. Étape 8 — Key Vault : sortir les secrets des app settings

Les app settings sont chiffrés au repos, et **aucun secret n'est dans ton dépôt Git** — le
critère du brief est déjà rempli. Le Key Vault ajoute ce que les app settings ne donnent
pas : **rotation, audit, et un secret qui n'est plus lisible par quiconque ouvre le
portail.** C'est ce que la compétence 2 du brief appelle « coffre de secrets ».

### 11.1 Créer le coffre et y mettre les secrets

1. **`Create a resource`** → **`Key Vault`** → **`Create`**.
2. **Basics** : `rg-velmo-prod`, **Name** `kv-velmo-…`, ta région,
   **Pricing tier** `Standard`.
3. Onglet **Access configuration** → **Permission model** →
   **`Azure role-based access control`** (recommandé par Microsoft).
4. **`Review + create`** → **`Create`**.
5. Coffre → **`Objects`** → **`Secrets`** → **`+ Generate/Import`**, trois fois :

| Secret name | Valeur |
|---|---|
| `llm-inference-api-key` | la clé Foundry |
| `database-url` | la chaîne de connexion complète |
| `service-api-key` | ta clé de service |

### 11.2 Donner une identité à la Web App

1. Web App `agent-api` → **`Settings`** → **`Identity`** → onglet
   **`System assigned`** → **`Status`** = **`On`** → **`Save`**.
2. Key Vault → **`Access control (IAM)`** → **`+ Add`** → **`Add role assignment`** :
   - **Role** → **`Key Vault Secrets User`**.
   - **Assign access to** → **`Managed identity`** → **`+ Select members`** →
     **`App Service`** → choisis `velmo-agent-…`.
   - **`Review + assign`**.
3. Répète pour la Web App `client` (elle a besoin de `service-api-key`).

### 11.3 Remplacer les valeurs par des références

Retourne dans les app settings et remplace la **valeur** (le nom reste identique) :

```
@Microsoft.KeyVault(VaultName=kv-velmo-xxx;SecretName=llm-inference-api-key)
@Microsoft.KeyVault(VaultName=kv-velmo-xxx;SecretName=database-url)
@Microsoft.KeyVault(VaultName=kv-velmo-xxx;SecretName=service-api-key)
```

✅ **Preuve :** dans la liste des app settings, chaque référence affiche l'icône
**`Key vault reference`** avec un statut **résolu**. Si tu vois la chaîne
`@Microsoft.KeyVault(...)` littérale dans les logs ou une erreur applicative, la référence
**n'a pas** été résolue : rôle RBAC manquant, ou faute de frappe. Le portail te le dit —
clique **`Edit`** sur la ligne, la boîte affiche l'erreur exacte.

> Fais ça **après** que tout fonctionne, jamais avant. Sinon tu ajoutes une inconnue
> (« est-ce le secret ou la référence ? ») à un système que tu n'as pas encore vu marcher.

---

## 12. Étape 9 — Journaux et signaux de suivi

### 12.1 Activer les journaux

1. Web App `agent-api` → **`Monitoring`** → **`App Service logs`**.
2. **`Application logging`** → **`File System`**. **`Save`**.
3. **`Monitoring`** → **`Log stream`** → tu vois les logs en direct.

### 12.2 Les métriques, au clic

**`Monitoring`** → **`Metrics`**. Ajoute ces métriques (bouton **`+ Add metric`**) :

| Métrique | Ce qu'elle te dit |
|---|---|
| **Response Time** (moy. et max) | La **latence par conversation** demandée par le brief |
| **Http 5xx** | Les erreurs serveur |
| **Http 401** | Les appels refusés — donc aussi la protection budgétaire qui joue |
| **Requests** | Le volume, base du coût indicatif |
| **CPU / Memory working set** | Le B1 tient-il ? |

**`Save to dashboard`** te donne le « court relevé des signaux de suivi » du livrable.

### 12.3 Les trois signaux exigés par le brief

| Signal | Où le prendre |
|---|---|
| **Latence par conversation** | Metrics → *Response Time*. Compare au **4,4 s** de warm-up mesuré en conteneur, et à la latence de tes tests locaux |
| **Coût indicatif** | Foundry → **`Metrics`** de la ressource (tokens consommés) + `EVAL_PRICE_PER_1M_*` de ton `.env.example`. Et groupe de ressources → **`Cost analysis`** pour l'infra |
| **Taux de blocage des garde-fous** | **Log stream** : compte les lignes de blocage de `guard_input` / `guard_output` sur ton échantillon de test (étape 10). C'est un relevé manuel — le brief demande « des signaux simples », pas une stack d'observabilité |

> **Application Insights** (bouton **`Application Insights`** → **`Turn on`**) te donnerait
> les traces distribuées. Utile, mais hors du strict nécessaire du brief, et ça ajoute une
> ressource à comprendre. À faire seulement si le reste tourne et qu'il te reste du temps.
> Note que sa clé de connexion **n'est pas considérée comme un secret** par Microsoft : ne
> la mets pas au Key Vault, tu perdrais l'affichage de la télémétrie dans le portail.

---

## 13. Étape 10 — Les tests d'acceptance en ligne

C'est la porte d'entrée de l'évaluation. Rejoue **en ligne** ce qui passait en local, et
**note le résultat** : c'est ce tableau que tu montreras.

| # | Cas | Comment | Attendu | ✅/❌ |
|---|---|---|---|---|
| 1 | **Réponse sourcée** | UI : « quels sont les frais de port ? » | Réponse issue de la FAQ Velmo | |
| 2 | **Mémoire courte** | Deuxième tour qui s'appuie sur le premier | L'agent se souvient | |
| 3 | **Persistance R2** | Fait donné, **`Restart`** de la Web App, `thread_id` neuf, même `user_id` | Le fait est retrouvé | |
| 4 | **Isolation R3** | Même question, **autre** `user_id` | L'agent ne sait pas | |
| 5 | **Garde-fou entrée** | Message à bloquer / injection de prompt (`data/eval/guardrail_cases.jsonl`) | Blocage **+ ligne dans le Log stream** | |
| 6 | **Garde-fou sortie** | Provoquer une PII en sortie | Caviardage **+ journalisation** | |
| 7 | **Clé de service** | `curl` sans header, puis avec une mauvaise clé | `401` **en 0,0 s** — donc *avant* tout appel au LLM | |
| 8 | **Aucune fuite de secret** | Ouvre `/health`, `/ready`, l'UI ; provoque une erreur | Aucune clé, aucune chaîne de connexion, aucun `@Microsoft.KeyVault(...)` dans une réponse ou une page | |

**Le cas 5 et le cas 6 demandent la double preuve** que le brief exige : le blocage **et**
la journalisation. Un blocage non journalisé ne compte pas — ce serait un garde-fou qu'on
ne peut pas auditer.

**Le cas 7 est aussi un test budgétaire**, pas seulement un contrôle d'accès : un refus en
0,0 s prouve qu'un endpoint public branché sur une clé LLM ne se fait pas vider son crédit
en une nuit.

### Les captures d'écran à prendre pour les livrables

1. **`rg-velmo-prod`** → vue **`Overview`** listant les 5 ressources (ACR, plan, 2 Web
   Apps, Postgres, Foundry, Key Vault). ← explicitement demandé par le brief.
2. Les **app settings** de `agent-api`, avec les références Key Vault visibles.
3. `azure.extensions` = `VECTOR` sur le serveur Postgres.
4. Le **Log stream** montrant un blocage de garde-fou.
5. Le **dashboard Metrics** avec latence et erreurs.
6. Les deux **Repositories** de l'ACR avec l'architecture `amd64`.

---

## 14. Après la démo — supprimer, vraiment

Le plan App Service et le Flexible Server se paient **à l'heure, trafic ou pas**. C'est le
prix assumé du choix App Service (pas de *scale-to-zero*, donc pas de ré-indexation de la
FAQ à chaque réveil).

1. **`Resource groups`** → `rg-velmo-prod`.
2. **`Delete resource group`** → tape `rg-velmo-prod` pour confirmer → **`Delete`**.

Tout part d'un coup : c'est pour ça que le groupe unique était un critère de performance.
Garde tes captures et ce document **avant** de supprimer.

---

## 15. Ce que ce tutoriel ne fait pas — à dire à la CTO

L'honnêteté sur les limites fait partie du livrable. Trois points :

1. **L'identité n'est pas prouvée.** Le `user_id` vient toujours du corps de la requête
   (`server.py:60` `_resolve_user_id`). C'est le trou 🔴 §5.1 de l'architecture cible.
   L'isolation R3 est donc **vraie au niveau du stockage** et **déclarative au niveau de
   l'appelant** : n'importe qui connaissant la clé de service peut se déclarer un autre
   `user_id`. La parade est un JWT vérifié côté serveur (Entra ID, App Service
   Authentication). **À dire explicitement en soutenance** — c'est plus solide que de
   laisser croire l'inverse.
2. **L'invariant n°5 reste violé** : l'application indexe la FAQ au démarrage. Le passage
   à App Service l'a rendu **indolore** (pas de scale-to-zero), pas résolu. Il redeviendra
   un vrai sujet à plus d'une instance : chacune indexerait la même FAQ pour son compte.
3. **Le déploiement n'est pas automatisé.** Une nouvelle version = rebuild + push + mise à
   jour du tag dans l'onglet Container. La suite naturelle est la voie C de l'étape 3
   (GitHub Actions → ACR), qui fermerait le CD annoncé dans `ci.md`.

---

## 16. Sources vérifiées le 2026-08-24

- [Quickstart : conteneur personnalisé sur App Service (pivot portail Linux)](https://learn.microsoft.com/en-us/azure/app-service/quickstart-custom-container?pivots=container-linux-azure-portal)
- [Configurer un conteneur personnalisé — `WEBSITES_PORT`, logs, registre privé](https://learn.microsoft.com/en-us/azure/app-service/configure-custom-container?pivots=container-linux)
- [Créer un Azure Container Registry au portail (Admin user, DNL scope)](https://learn.microsoft.com/en-us/azure/container-registry/container-registry-get-started-portal)
- [ACR Tasks — quick task](https://learn.microsoft.com/en-us/azure/container-registry/container-registry-tutorial-quick-task) et [build sur commit](https://learn.microsoft.com/en-us/azure/container-registry/container-registry-tutorial-build-task)
- [ACR Tasks + buildx/BuildKit — préversion](https://github.com/Azure/acr/blob/main/docs/tasks/buildx/README.md)
- [Créer un PostgreSQL Flexible Server au portail](https://learn.microsoft.com/en-us/azure/postgresql/flexible-server/quickstart-create-server)
- [Autoriser des extensions (`azure.extensions`, pgvector)](https://learn.microsoft.com/en-us/azure/postgresql/extensions/how-to-allow-extensions)
- [Endpoints Microsoft Foundry Models — route `/openai/v1/`](https://learn.microsoft.com/en-us/azure/foundry/foundry-models/concepts/endpoints)
- [Créer et déployer une ressource Azure OpenAI / Foundry](https://learn.microsoft.com/en-us/azure/foundry-classic/openai/how-to/create-resource)
- [Références Key Vault dans les app settings](https://learn.microsoft.com/en-us/azure/app-service/app-service-key-vault-references)
- [Déploiement de Chainlit — WebSockets et sticky sessions](https://docs.chainlit.io/deploy/overview) *(via Context7)*
- [LangGraph — `PostgresSaver` / `PostgresStore` en production](https://docs.langchain.com/oss/python/langgraph/add-memory) *(via Context7)*
- [WebSockets sur App Service Linux](https://learn.microsoft.com/en-us/answers/questions/5688454/cannot-find-an-option-to-enable-disable-web-socket)
