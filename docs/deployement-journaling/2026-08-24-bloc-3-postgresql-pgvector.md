# 2026-08-24 — Bloc 3 : PostgreSQL Flexible Server et pgvector

**Objet :** créer le serveur qui portera la mémoire de l'agent, autoriser pgvector,
créer la base, et **vérifier depuis la machine** avant de déployer quoi que ce soit.
**Correspond à :** §7 du tuto `docs/2026-08-24-tuto-deploiement-azure-portail.md`
(compétences 4 et 6 du brief — persistance R2, isolation R3).
**Prérequis :** [bloc 2](2026-08-24-bloc-2-foundry-service-ia.md) terminé.
**Statut :** ✅ **terminé** — serveur créé, `vector 0.8.2` installé, connexion vérifiée.

---

## Les valeurs de ce bloc

| Élément | Valeur |
|---|---|
| Serveur | `pgsablvelmo` → `pgsablvelmo.postgres.database.azure.com` |
| Région | **West Europe** (co-localisée avec la future App Service) |
| Version | **PostgreSQL 17.10** |
| SKU | Burstable **B1ms**, stockage 32 Gio, HA désactivée |
| Authentification | PostgreSQL uniquement |
| Admin | `velmoadmin` |
| Base | **`velmo-agent`** |
| Extension | `vector` **0.8.2** |
| Schéma applicatif | `agent_state` *(créé par le code, pas à la main)* |

---

## Décision 1 — PostgreSQL 17, alors que 18 était proposé

**Ce que 18 apporterait ici : rien.** Ses nouveautés portent sur les I/O asynchrones et
l'optimiseur ; l'usage est un `PostgresSaver` et un `PostgresStore` sur quelques Mo.

**Ce qu'il coûterait : deux inconnues.**

1. L'écart avec la pile locale, testée sur `pgvector/pgvector:pg17` (`compose.yaml`). La
   parité permet d'affirmer « ce qui a été testé est ce qui tourne » au lieu de l'espérer.
2. La disponibilité de `pgvector`, certaine sur 16 et 17, non vérifiée sur 18. Elle ne se
   découvrirait qu'à l'écran `azure.extensions`, **après** la création — et la version
   n'est pas modifiable après coup.

Principe appliqué depuis le début : **ne pas faire entrer une variable neuve dans un
système qu'on n'a pas encore vu marcher.**

## Décision 2 — le nom de la base : `velmo-agent`

Le tuto proposait `agent` (parité avec `POSTGRES_DB: agent` de `compose.yaml`).
Retenu : `velmo-agent`, cohérent avec `acrsablvelmo` / `pgsablvelmo`.

Sur le **tiret** : il ne gêne que pour un identifiant écrit dans une requête SQL. Un nom
de base dans une chaîne de connexion n'a pas besoin d'être quoté, et le code ne référence
jamais le nom de la base — il ne manipule que le schéma et l'extension. Aucun impact.

Seule conséquence : la chaîne de connexion diffère du local d'un mot. À se rappeler si on
compare un jour les deux pour trouver un écart.

## Décision 3 — les données métier **ne vont pas** dans Postgres

Question soulevée pendant ce bloc : et le double marchand (commandes, clients,
expéditions) ?

**Réponse : il reste en `SUPPORT_BACKEND=memory`**, et ce n'est pas un renoncement.
`database/README.md` avait déjà tranché :

> *« Cette base représente un système tiers. Aucune jointure ne doit être faite entre elle
> et les mémoires de l'agent : en production, ce backend est remplacé par des appels au SI
> du marchand. »*

Mettre les commandes dans la base de l'agent confondrait **ce que l'agent sait** et **ce
que la boutique possède**. C'est une frontière de conception, pas une contrainte de temps.

Deux raisons techniques s'y ajoutent :

- `SqlSupportBackend.from_path()` (`actions/sql/backend.py:44`) n'ouvre qu'un **fichier**
  SQLite. Dans un conteneur App Service, le système de fichiers est éphémère : le fichier
  serait perdu à chaque redémarrage — donc la complexité sans la persistance.
- Les deux adaptateurs n'ont pas la même convention d'identifiants (`CMD-1001` vs
  `O-2024-0103`) et `eval/dataset.py` épingle celle de `memory` : basculer casserait les
  évaluations. Cette raison-là vaudrait aussi en local.

Il n'existe pas de `SUPPORT_BACKEND=postgres` (`actions/backend.py:206` n'accepte que
`memory` ou `sqlite`).

**Ce que ça coûte, à savoir :** un échange ou un remboursement fait pendant une démo est
perdu au redémarrage, ainsi que le compteur de `guardrails_action_rate_limit`. Aucun des
8 tests d'acceptance n'en dépend.

### Et une seconde base sur le même serveur ?

Techniquement oui, et gratuitement : on paie le **serveur**, pas les bases. Ce serait même
architecturalement plus juste — deux bases rendent la jointure *impossible* plutôt que
déconseillée.

Mais aucun code ne saurait la lire aujourd'hui : il faudrait écrire l'adaptateur Postgres
du double métier. Du développement, pas du déploiement. Non fait.

*Si c'est fait un jour :* nommer `site_marchand` (underscore, pas tiret : un tiret force
le quoting dans toutes les requêtes SQL), et se souvenir que `CREATE EXTENSION` s'exécute
**par base**, pas par serveur.

---

## Le déroulé, écran par écran

### Écran « Général »

`Créer une ressource` → `Bases de données` → `Azure Database for PostgreSQL serveur
flexible` → `Créer`.

⚠️ **Régler « Type de charge de travail » = `Développement` AVANT de toucher au calcul** :
ce choix reconfigure les SKU proposés en dessous, et le faire après réinitialise le
réglage.

### Écran « Mise en réseau » — le seul irréversible du bloc

- Méthode de connectivité → **`Accès public`**
- ☑️ Autoriser l'accès public via une adresse IP publique
- ☑️ **`Autoriser l'accès public depuis n'importe quel service Azure`** ← **c'est cette
  case qui laisse passer la Web App.** Sans elle, le bloc 5 échoue sur un timeout muet
- **`+ Ajouter l'adresse IPv4 actuelle du client`** → pour vérifier depuis le poste

*Private access (VNet) serait plus propre en vraie production, mais ranimerait le piège
`tiktoken` du plan de déploiement (sortie réseau restreinte = conteneur qui ne démarre
pas). Choix assumé.*

> **Nuance utile :** ces deux cases sont des **règles de pare-feu**, donc modifiables après
> coup — contrairement au choix Accès public/privé. Une fois l'App Service créée, on
> pourra remplacer « n'importe quel service Azure » par ses IP sortantes précises.

### Sur l'IP du client et les connexions mobiles

L'adresse ajoutée est l'IP publique par laquelle Azure voit la machine — donc celle de la
box 5G, **dynamique**, et souvent derrière un CGNAT.

| Règle | Qui l'utilise | Cassée si l'IP change ? |
|---|---|---|
| « n'importe quel service Azure » | **La Web App** | Non |
| « IP du client » | **Moi, depuis mon poste** | Oui |

L'agent déployé continuera donc de fonctionner. Seul l'accès `psql` depuis le Mac
casserait. Réparation en 10 s : serveur → `Mise en réseau` → `+ Ajouter l'adresse IPv4
actuelle` → `Enregistrer`.

Pour trancher en cas de doute : `curl -s ifconfig.me`, comparer à la règle enregistrée.

### Ce qui se crée en deux temps

Le déploiement affiche **deux** ressources : `pgsablvelmo` (le serveur) passe au vert
d'abord, puis `firewallRules-…`. Normal : les règles s'appliquent à une ressource qui doit
exister d'abord.

---

## Les deux gestes qu'on oublie

### Geste 1 — mettre `VECTOR` en liste blanche (portail obligatoire)

Serveur → `Paramètres` → **`Paramètres du serveur`** → chercher **`azure.extensions`** →
cocher **`VECTOR`** → `Enregistrer`.

**Ne se fait pas en SQL** : c'est un paramètre serveur Azure. Sur Azure, une extension doit
être autorisée **avant** que `CREATE EXTENSION` fonctionne. Sans ça,
`memory/postgres_conn.py:59` échoue — au démarrage du conteneur, au pire moment.

Azure applique le changement comme un déploiement à part entière
(`PostgreSQLFlexibleServerParameters_…`). C'est un paramètre **dynamique** : pas de
redémarrage du serveur, la base reste disponible. ~1 minute.

### Geste 2 — créer la base

Serveur → `Paramètres` → **`Bases de données`** → `+ Ajouter` → nom `velmo-agent` → UTF8 →
`Enregistrer`. Instantané.

**Ne rien créer d'autre** : ni schéma, ni table, ni utilisateur. `setup()` créera
`agent_state` et ses tables au premier démarrage, et l'admin en a le droit.

---

## La vérification — une commande, quatre réponses

Le geste le plus rentable du bloc : une erreur trouvée **ici** se lit en clair, alors que
la même erreur trouvée au bloc 5 se présente sous la forme d'un conteneur qui refuse de
démarrer sans dire pourquoi.

```bash
psql "host=pgsablvelmo.postgres.database.azure.com port=5432 dbname=velmo-agent user=velmoadmin sslmode=require" \
  -c "SELECT version();" \
  -c "SHOW azure.extensions;" \
  -c "CREATE EXTENSION IF NOT EXISTS vector;" \
  -c "SELECT extname, extversion FROM pg_extension;"
```

Une seule connexion, donc **un seul prompt de mot de passe**.

```
Mot de passe pour l'utilisateur velmoadmin :
                                    version
-------------------------------------------------------------------------------
 PostgreSQL 17.10 on x86_64-pc-linux-gnu, compiled by gcc (GCC) 13.2.0, 64-bit

 azure.extensions
------------------
 VECTOR

CREATE EXTENSION

 extname | extversion
---------+------------
 plpgsql | 1.0
 vector  | 0.8.2
```

| Requête | Ce qu'elle prouve |
|---|---|
| `SELECT version()` | La connexion passe → pare-feu **et** TLS OK |
| `SHOW azure.extensions` | La liste blanche est active |
| `CREATE EXTENSION …` | **Le point critique** : la ligne exacte de `postgres_conn.py:59` |
| `SELECT extname…` | `vector 0.8.2` installé |

Effet de bord bienvenu : l'extension étant désormais créée, le `CREATE EXTENSION IF NOT
EXISTS` du démarrage sera un **no-op**.

### Lecture des échecs, si ça avait raté

| Message | Cause |
|---|---|
| `timeout` / pas de réponse | Pare-feu : la case « services Azure » ou l'IP du client |
| `password authentication failed` | Mot de passe, ou caractère spécial mal encodé |
| `extension "vector" is not allow-listed` | Le geste 1 n'a pas été fait ou pas enregistré |

---

## La chaîne de connexion — à composer maintenant, à utiliser au bloc 5

```
postgresql://velmoadmin:MOTDEPASSE@pgsablvelmo.postgres.database.azure.com:5432/velmo-agent?sslmode=require
```

C'est la valeur de `DATABASE_URL`. **Quatre pièges**, tous documentés avant d'être
rencontrés :

1. **`?sslmode=require` est obligatoire** — Azure impose TLS.
2. **L'utilisateur est `velmoadmin`, pas `velmoadmin@pgsablvelmo`.** La syntaxe
   `user@serveur` était celle de l'ancien *Single Server* ; sur *Flexible Server* elle
   échoue.
3. **Un mot de passe à caractères spéciaux doit être URL-encodé** (`@` → `%40`,
   `#` → `%23`, `/` → `%2F`). D'où le conseil, suivi ici : choisir un mot de passe long
   **sans** caractère spécial autre que `-` et `_`, ce qui supprime le problème au lieu de
   le gérer. Une erreur d'encodage produit un message qui ne parle que
   d'authentification — jamais d'encodage.
4. **`:5432`, pas `:6432`.** Le 6432 est le PgBouncer intégré ; le pooling demanderait
   `prepare_threshold=0` (finding P1 de la revue d'escalade), non traité dans le code
   aujourd'hui.

---

## ✅ Bloc 3 terminé

| Preuve attendue | Obtenue |
|---|---|
| Le serveur répond depuis l'extérieur | ✅ `PostgreSQL 17.10` |
| `azure.extensions` contient `VECTOR` | ✅ |
| `CREATE EXTENSION vector` fonctionne | ✅ `vector 0.8.2` |
| La base applicative existe | ✅ `velmo-agent` |

**Trois inconnues écartées sur trois blocs** : l'image tourne en amd64, le service d'IA
répond, la base accepte pgvector. Un échec au bloc suivant ne pourra venir que de la
Web App elle-même.

**Bloc suivant :** plan App Service + Web App `agent-api` en `PERSISTENCE_BACKEND=memory`
(§8 du tuto). La base ne sera branchée qu'au bloc 5 — **un suspect à la fois** : si le
bloc 4 échoue, la cause est l'image, l'ingress, le provider ou les secrets ; jamais la
base.

## Note pour le bloc 4 — paramètres à ne pas oublier

`database/README.md` signale `MEMORY_TTL_DAYS` (défaut 365) : il arme le balayage des
souvenirs périmés, avec un compteur qui repart au **dernier accès**, pas à la création.
Ce n'est pas un secret, mais c'est une configuration à conséquences RGPD — elle doit
figurer dans la liste des paramètres externalisés du dossier de déploiement, à côté des
seuils `GUARDRAILS_*` (`config.py:79-84`).
