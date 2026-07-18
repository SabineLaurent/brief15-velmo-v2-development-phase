# 🌅 Vision du projet — le cap, les scopes, la structure

> Ce document est la **boussole du projet**. Il ne décrit ni le métier (`spec.md`),
> ni la technique interne de l'agent (`architecture.md`), ni les étapes
> (`ROADMAP.md`) : il dit **où on va, comment on découpe le travail, et pourquoi
> le repo est organisé comme il l'est**. À lire en premier pour comprendre
> l'intention d'ensemble.

## 1. Le cap

Construire un **agent IA de support client de niveau professionnel** — pas une
maquette de démo — et en faire un **projet de portfolio réussi**, montrable et,
à terme, **déployé avec une URL live**. La `docs/perimetre-final.md` détaille la
« definition of done » prod ; ce document-ci fixe l'**organisation** qui permet
d'y arriver proprement.

## 2. Le principe fondateur : le découplage, à tous les étages

Le projet applique **une seule idée, à deux échelles** :

- **Au niveau du code** — l'agnosticisme : le cœur ne dépend pas d'un provider
  LLM concret. On change de provider avec une variable d'environnement, via la
  factory. Voir `CLAUDE.md` (contrainte n°1).
- **Au niveau du projet** — la séparation des scopes : le cœur ne dépend ni de
  son interface (front) ni de son infrastructure (back). On branche/débranche
  l'un ou l'autre sans toucher au cerveau.

Même philosophie, une échelle au-dessus. C'est ce qui donne le super-pouvoir
recherché : *« je débranche, je rebranche au besoin et à l'envie »*.

## 3. Les 3 scopes découplés

Chaque scope évolue avec **sa propre roadmap** et son propre rythme.

| Scope | Rôle | État |
|---|---|---|
| **A — Cœur agent** (le « cerveau ») | features métier : mémoire, RAG, orchestration, guardrails, cycle de vie du support… | actif — `ROADMAP.md` |
| **B — Frontend** (l'interface) | rendre l'agent *montrable* : coquille démo → front déployé | à démarrer |
| **C — Backend / infra** (le socle) | durabilité prod : vrai SI, Postgres, auth, secrets… | plus tard |

**La Phase 13 (exposition / API) appartient au Scope A** : c'est la *porte de
sortie* du cerveau, ce qu'il publie au monde. Le Scope B la consomme.

## 4. Les 2 coutures (contrats d'interface)

Pour que les scopes restent **vraiment** découplés (pas juste rangés dans des
dossiers voisins), on nomme leurs points de contact — comme les *ports* le font
déjà dans le code :

- **Couture agent ↔ front = l'API.** Le front ne connaît *jamais* l'intérieur
  lang*. Il parle à un contrat stable : au niveau 1, un point d'entrée Python
  unique (`stream_reply(...)`) ; aux niveaux suivants, une API HTTP (Phase 13).
  Conséquence : on remplace Chainlit par du React sans toucher au cerveau.
- **Couture agent ↔ infra = les ports existants** (`SupportBackend`,
  checkpointer / store). Le vrai SI et Postgres se branchent *derrière* ces
  ports, sans toucher aux nœuds du graphe.

> Règle d'or : **3 scopes, 2 coutures.** Tant qu'on respecte les coutures, chaque
> scope reste interchangeable.

## 5. Agnostique où ça compte, engagé où c'est fécond

Choix **assumé et lucide** : on ne s'abstrait pas de *tout* (ça mène à la
paralysie), on s'abstrait de ce qui est **volatil et coûteux à changer**.

- **Agnostique** → le **provider LLM** (factory). C'est là que le lock-in ferait
  mal ; on garde la liberté de tester Mistral / Groq / Foundry / API maison.
- **Engagé sciemment** → **LangGraph / LangSmith** comme socle d'orchestration et
  d'observabilité. Lock-in choisi, structurant, et terrain d'expérimentation
  volontaire pour ce projet.

## 6. La structure repo : un mono-repo, outillé pour 2026

**Décision : mono-repo** (un seul dépôt), pas des repos séparés.

**Pourquoi pas des repos séparés (polyrepo).** Le seul vrai bénéfice du polyrepo
(équipes indépendantes, releases séparées, accès cloisonné) ne s'applique pas à
un projet solo. On n'en paierait que les coûts : compatibilité inter-repos gérée
à la main, pas de changement atomique à travers une couture, CI multipliées — et
un lecteur devrait cloner 3 dépôts pour comprendre le système. À rebours de
l'effet portfolio.

**Pourquoi le mono-repo sert le portfolio.** Un seul clone, des changements
atomiques à travers les coutures, une CI unique déclenchée par chemin — et
surtout un reviewer voit les **3 scopes propres côte à côte, frontières
explicites**. Ça *prouve* qu'on sait découpler sans fragmenter.

**L'outillage (le point d'actualité).** Mono vs poly est consensuel ; ce qui date
un projet, c'est le *comment*.

- **Court terme, tout est Python** (cœur + Chainlit + back) → **uv workspace** :
  plusieurs packages, chacun son `pyproject.toml`, **un seul `uv.lock` partagé**.
  Réponse native et à jour, zéro outil en plus. La couture devient *littérale* :
  le package `frontend` déclare `support-agent = { workspace = true }` et rien
  d'autre — frontière appliquée au niveau des dépendances, pas par convention.
- **Plus tard, si un front JS/React arrive** → *polyglot monorepo* (uv workspace
  Python + dossier app JS). Task-runner léger (**Turborepo / Nx**) *seulement si
  besoin*. On **évite Bazel / Pants** : surdimensionné pour un solo, nuit à la
  lisibilité portfolio. On reste léger : `Makefile` + CI par chemin.

**Structure cible :**

```
agnostic-consumer-support-AI-agent/     ← racine = workspace root
├── pyproject.toml          # [tool.uv.workspace] members = ["packages/*"]
├── uv.lock                 # UN lockfile partagé
├── Makefile                # porte d'entrée unique (make run / ui / test…)
├── docs/                   # docs partagées (vision, spec, archi, roadmap)
└── packages/
    ├── support-agent/      # Scope A — le cœur (l'actuel src/support_agent)
    │   └── src/support_agent/
    ├── frontend/           # Scope B — la coquille (Chainlit → puis web)
    │   └── (dépend de support-agent en workspace = true)
    └── backend/            # Scope C — plus tard (vrai SI, Postgres…)
```

**Coût de migration :** déplacer `src/support_agent/` → `packages/support-agent/`
(+ ajuster `pyproject.toml`, `Makefile`, `CLAUDE.md`). À faire **avant** que le
front existe = le moment le moins cher ; attendre = plus de churn.

## 7. Le frontend : deux niveaux de visibilité

- **Étape 1 — visibilité immédiate** : coquille **Chainlit** (Python) branchée
  sur le point d'entrée `stream_reply`. Chat web, streaming, affichage des outils.
  Assumée *non-prod* : un GIF de 20 s pour le README, pas la porte d'entrée finale.
- **Étape 2 — vrai front déployé** : interface web séparée, **URL live**,
  consommant l'API (Phase 13). Auth + streaming + backend durable côté couture.

---

> 📌 Document vivant. On le met à jour quand un scope démarre, quand une couture
> se matérialise (API), ou quand la structure repo évolue. Voir aussi
> `ROADMAP.md` (le chemin) et `docs/perimetre-final.md` (la definition of done).
