# Architecture cible — les blocs déployés et le rangement des données

**Objet :** figer le découpage en blocs (conteneurs) de l'agent de support, et
trancher l'organisation **relationnel ↔ vectoriel**, restée floue jusqu'ici.
**Date :** 2026-07-25
**Statut :** décision retenue. Ce document est la **cible** ; il ne décrit pas
l'état actuel du code (pour ça : [`architecture.md`](architecture.md)).
**Portée :** déploiement, propriété des données, choix des bases. Pas le code
applicatif — dont l'intérêt est justement de **ne pas changer** quand on bascule.

---

## La décision en une phrase

Trois services en production — **agent-api**, **client**, **un seul Postgres
(pgvector)** — plus une **doublure du SI marchand en développement uniquement**,
parce que la base métier n'est pas à nous et ne le sera jamais.

---

## 1. Les blocs retenus

```
                        ┌──────────────┐
                        │   client     │  (Chainlit aujourd'hui, autre demain)
                        └──────┬───────┘
                               │ HTTP + token vérifié
                        ┌──────▼───────┐        ┌──────────────────┐
                        │  agent-api   │───────▶│ LangSmith (SaaS) │
                        │  stream_reply│        └──────────────────┘
                        └──┬────────┬──┘
              état + savoir │        │ port actions/
                        ┌───▼────┐   │   ┌────────────────────────────┐
                        │postgres│   └──▶│ SI marchand + CRM/ticketing│
                        │pgvector│       │   (externes, PAS à nous)   │
                        └────────┘       └────────────────────────────┘
                            ▲
                            │ (job découplé, CI/cron)
                        ┌───┴────────┐
                        │ ingestion  │  data/kb-velmo/ → index vectoriel
                        └────────────┘
```

| Bloc | Prod | Dev | Nature |
|---|---|---|---|
| `agent-api` | conteneur | process local | le cerveau + sa porte de sortie `api.py` |
| `client` | conteneur | process local | support d'interaction, ne voit que `stream_reply` |
| `postgres` (pgvector) | conteneur / base managée | 2 fichiers SQLite | **tout ce que l'agent possède** |
| `ingestion` | **job**, pas un service | fait par l'app au démarrage | projection reconstructible |
| `shop-double` | ❌ **n'existe pas** | conteneur / SQLite | doublure du SI marchand |
| observabilité | LangSmith, SaaS | idem | pas un conteneur |

⚠️ Le bloc « base relationnelle » de la formulation initiale était **ambigu** : il
désignait à la fois notre état et la base métier. Ce sont deux choses de
propriétaires opposés. La section 2 est la règle qui les sépare.

---

## 2. La question qui range tout : qui **possède** la donnée ?

Pas « quelle est sa forme ? » (relationnelle, vectorielle) — **« qui en est
propriétaire en production ? »**. La forme est un détail d'implémentation ; la
propriété décide du déploiement, des sauvegardes, du RGPD et des pannes.

| Donnée | Forme | Propriétaire en prod | Cycle de vie |
|---|---|---|---|
| **Working memory** (`thread_id`) | relationnel | **nous** | jetable, TTL court |
| **Agent memory** (`user_id`) | relationnel **+ vectoriel** | **nous** | données perso → rétention RGPD |
| **Index FAQ** | vectoriel | nous, mais **reconstructible** | effaçable sans hésiter |
| **Boutique** (commandes, retours, clients) | relationnel | **le marchand** | on ne le migre pas : on le **débranche** |

Les trois premières lignes sont notre état : elles vivent dans **notre** base. La
quatrième est un **appel réseau** derrière le port `actions/`.

⚠️ **Le piège** : faire un `JOIN` entre nos souvenirs et les commandes du client.
Ça marche en dev (même moteur, même fichier) et devient impossible en prod, où les
deux vivent chez des fournisseurs distincts. **La frontière doit rester un appel,
jamais une jointure.** Détail : [`database/README.md`](../database/README.md).

---

## 3. Décision : **une seule base**, pas deux conteneurs

Un Postgres avec l'extension **pgvector** porte les trois données qui sont à nous.
« Relationnel » et « vectoriel » ne sont **pas deux endroits** — c'est déjà vrai
aujourd'hui : `agent_memory/memories.db` contient `store` *et* `store_vectors`
(via `sqlite-vec`), dans le même fichier.

**Pourquoi :**

1. **LangGraph le fait nativement.** `PostgresSaver` pour le court terme ;
   `PostgresStore(index=PostgresIndexConfig(...))` pour le long terme, avec la
   recherche sémantique **en pgvector dans la même base** — et un
   `ttl=TTLConfig(...)` avec son *sweeper*, exactement ce qu'il faut pour purger
   les conversations (RGPD). Rien à écrire nous-mêmes.
2. **Le corpus ne le justifie pas.** 16 fichiers markdown. Un moteur vectoriel
   dédié pour ça, c'est un conteneur, une sauvegarde, un pool de connexions et un
   mode de panne de plus, pour zéro gain mesurable.
3. **Une transaction, un dump, une restauration.** « La mémoire du client et son
   contexte sont incohérents après un restore » est un bug bien plus coûteux, sur
   un agent de support, qu'une milliseconde de recherche vectorielle.

**Ce qu'on sépare quand même :** les **schémas** Postgres (`agent_state`,
`knowledge`), pas les serveurs. On garde la frontière logique — et la possibilité
d'extraire plus tard — sans payer l'exploitation de deux clusters.

**Ce que la décision ne ferme pas.** Sortir vers un moteur dédié (Qdrant,
Weaviate, Azure AI Search) reste un changement **d'un seul fichier**
(`knowledge/ingest.py`) : l'agent ne bouge pas. C'est ce qui rend le report
raisonnable plutôt que risqué.

### Les déclencheurs de sortie (à surveiller, pas à anticiper)

On extrait l'index vers un moteur dédié **quand l'un de ces faits est constaté** :

- corpus au-delà de ~1 M chunks, ou latence de recherche mesurée comme gênante ;
- besoin de **recherche hybride** BM25 + vecteurs avec *reranking* ;
- réindexation qui doit **scaler indépendamment** de l'agent ;
- produit managé **imposé** par le client ou la DSI.

Aucun de ces déclencheurs n'est un pressentiment : chacun se vérifie.

---

## 4. Ce qui change entre dev et prod

```
DEV                                     PROD (cible)
───                                     ────────────
PERSISTENCE_BACKEND=sqlite              PERSISTENCE_BACKEND=postgres
  2 fichiers, 1 process                   1 base, N process concurrents
l'app indexe la FAQ au démarrage        job d'ingestion découplé (CI / cron)
  (~1,6 s, zéro dépendance)               └─ l'app n'indexe JAMAIS
shop-double (SQLite local)              ❌ débranché → API du marchand
identité passée par le client           identité prouvée par un token vérifié
```

Côté agent, le basculement de persistance est **une variable d'environnement** —
même esprit que l'agnosticisme LLM. Ce qui change vraiment, c'est l'**ingestion**
(qui cesse d'être faite par l'application) et l'**identité** (§5).

Esquisse de composition, dev :

```
agent-api  ·  client  ·  postgres(pgvector)  ·  shop-double   ← profil "dev" seulement
```

---

## 5. Les trous connus de cette architecture

Ils sont **structurels**, pas cosmétiques : à traiter avant toute mise en ligne.

### 5.1 🔴 Qui signe le `user_id` ?

Aujourd'hui le `user_id` vient du client. Si le front peut le poster librement,
**n'importe qui lit la mémoire long terme de n'importe quel client** — il suffit de
changer une valeur dans la requête. Le namespace `("memories", user_id)` isole
correctement les clients *à condition que l'identité soit prouvée*.

Cible : le client ne transmet pas une **identité**, il transmet une **preuve**
(token vérifié côté `agent-api`, qui en dérive le `user_id`). C'est le point de
sécurité n°1 de ce découpage.

### 5.2 La sortie d'escalade est externe

`create_ticket` écrit dans notre doublure. En prod, la destination est Zendesk /
Freshdesk / le CRM du marchand : **même statut que la boutique** — un appel derrière
le port `actions/`, pas une table à nous.

### 5.3 L'observabilité est un bloc, sans être un conteneur

LangSmith est un SaaS (compte **EU**). Il figure dans l'architecture parce qu'il
reçoit des traces contenant des messages clients — donc il relève de la même
analyse RGPD que la base, même s'il n'apparaît dans aucun `docker-compose`.

---

## 6. Vocabulaire : **doublure**, pas *mock*

Le service qui remplace le SI marchand s'appelle `shop-double`. Pas `shop-mock`.

Dans la taxonomie des *test doubles*, un **mock** est programmé avec des attentes
et sert à **vérifier des interactions**. Ce que nous avons est un **fake** : une
implémentation qui fonctionne vraiment (vrai schéma SQLAlchemy, vraies commandes,
vraies transitions de statut), en plus simple que la production. Elle ne vérifie
rien : elle **sert des données**.

La conséquence est pratique. Nommer ça « mock » signale « bouchon jetable, données
bidon, pas grave si c'est incohérent » — or tout l'intérêt de cette doublure est
l'inverse : c'est parce que `O-2024-0103` a un vrai statut, un vrai transporteur et
une vraie fenêtre de rétractation que l'agent est démontrable. **Le nom protège le
réalisme.**

Deux nuances : si un jour la doublure est exposée en HTTP pour imiter l'API du
marchand, `fake-shop-api` devient le terme exact. Et si un test ajoute un objet qui
*vérifie* qu'un ticket a bien été créé avec tel sujet — **ça**, ce sera un vrai mock.
Garder les deux mots distincts évite de confondre les deux rôles dans le même dépôt.

---

## 7. Ce que ce document ne dit pas

| Question | Où |
|---|---|
| Comment le code est structuré aujourd'hui | [`architecture.md`](architecture.md) |
| Le rangement `data/` vs `database/`, en détail | [`../database/README.md`](../database/README.md) |
| La taxonomie des mémoires (sémantique / épisodique / procédural) | [`memoire.md`](memoire.md) |
| Qui streame quoi | [`streaming.md`](streaming.md) |
| Le chemin de déploiement | [`plan-deploiement-2026-07-25.md`](plan-deploiement-2026-07-25.md) |
