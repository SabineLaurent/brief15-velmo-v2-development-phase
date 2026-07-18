# 🧩 uv workspace, conteneurs & interchangeabilité — dev vs runtime

> Note de clarification. Elle dissout une confusion fréquente : *« si le `uv.lock`
> est mutualisé par le workspace, est-ce que tout est soudé ensemble au
> déploiement ? »*. Réponse : **non** — le workspace organise le **code au dev**,
> les conteneurs / l'API / les variables d'env organisent le **déploiement au
> runtime**. Deux mondes distincts. Complète [`architecture.md`](architecture.md) §9.

## Le nœud : `uv.lock` mutualisé ≠ conteneurs soudés

Trois plans qui **ne se touchent pas** :

| Plan | C'est quoi | Le `uv.lock` mutualisé y joue quoi ? |
|---|---|---|
| **1. Build / dev** | *Quelles versions* de dépendances | ✅ ici, et **uniquement** ici |
| **2. Image Docker** | *Quel code + quelles deps* embarque un conteneur | ❌ chaque conteneur n'embarque qu'une **tranche** |
| **3. Runtime** | *Qui parle à qui* (réseau, quelle BDD) | ❌ piloté par réseau + variables d'env |

Le lockfile partagé est un artefact de **dev**, pas une frontière de **runtime**.
Il garantit juste que *quand* deux packages utilisent la même dépendance, ils sont
sur la **même version**. C'est une **garantie de cohérence**, pas une soudure.

> 🧳 **Analogie.** Le `uv.lock` = **la liste de courses commune** du foyer. Chacun
> ne met dans **sa propre valise** (son conteneur) que ce dont il a besoin. Une
> liste, des valises séparées.

## Individualisation en conteneurs : une image par scope

Pour construire l'image de l'agent, on n'installe **que le membre `support-agent`**
et ses dépendances — pas `chainlit`, pas le front. uv sait faire des **installs
partielles** :

- `uv sync --package support-agent --no-dev` → seulement ce membre ;
- flags `--no-install-workspace` / `--no-install-package <nom>` → exclure le reste ;
- build **multi-stage** + `--no-editable` → l'image finale ne contient que ce que
  ce service exécute (pas les sources des autres scopes).

Résultat : **un lockfile → N images indépendantes**, chacune une tranche. Mono-repo
au **dev**, conteneurs **individualisés** au déploiement. Aucune contradiction.

## Interchangeabilité du FRONT : deux coutures vers l'agent

| Front | Comment il atteint l'agent | Couplage |
|---|---|---|
| **Dans le repo** (coquille Chainlit de démo) | l'importe *via le workspace* (`workspace = true`) | commodité de dev |
| **N'importe quel autre** (React, mobile, autre repo, non-Python, `curl`) | par l'**API HTTP** de l'agent déployé (Phase 13) | frontière réseau universelle |

Point clé : **l'API réseau est la vraie frontière universelle.** Un client qui
parle HTTP au conteneur agent n'a besoin de **rien** du workspace (ni lockfile, ni
Python). L'appartenance du front de démo au workspace est un confort, **pas** une
condition pour consommer l'agent. → On déploie l'agent en conteneur, et **autant de
fronts qu'on veut**, d'où on veut, tapent dessus par l'API.

## Interchangeabilité du BACK : brancher une autre BDD

C'est le rôle des **ports** (`checkpointer` / `store` derrière la config). L'agent
ne code **aucune BDD en dur** : il lit `PERSISTENCE_BACKEND` + une **URL de
connexion** (variable d'env). Exemple docker-compose :

```yaml
services:
  agent-api:
    environment:
      DATABASE_URL: postgres://user:pass@postgres:5432/agent   # ← pointe vers…
  postgres:                                                     # …ce conteneur
    image: postgres:16
```

Changer de BDD = changer l'URL / le service. Le workspace et le lockfile **n'y sont
pour rien** : la BDD est une dépendance de **runtime**, atteinte par le réseau,
configurée par env, branchée **derrière le port**. Local, managé (RDS, Neon…),
peu importe.

## En une phrase

Le **workspace** organise le *code au dev* ; les **conteneurs + l'API + les
variables d'env** organisent le *déploiement au runtime* — et les seconds restent
libres **précisément parce que** l'archi est agnostique (ports + API). Le mono-repo
n'enferme dans **aucune** topologie de déploiement.

---

> 📌 Détails concrets (Dockerfile multi-stage, `docker-compose.yml` complet) à
> écrire à la **Phase 13** (exposition & déploiement). Voir `ROADMAP.md`.
