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

## 6. Un dépôt unique pour des scopes découplés

Choix **stratégique** (le *pourquoi*, pas le *comment*) : les 3 scopes vivent dans
**un seul dépôt**, pas dans des dépôts séparés. Pour un projet solo de portfolio,
le mono-dépôt sert l'objectif — un seul clone, des changements atomiques à travers
les coutures, et surtout un lecteur qui voit les **3 scopes côte à côte, frontières
explicites**. Ça *prouve* qu'on sait découpler sans fragmenter (là où 3 dépôts
séparés obligeraient à tout recoller à la main, sans bénéfice à cette échelle).

> 🔧 **La traduction physique** de ce choix (organisation des dossiers, outillage
> uv workspace, arbre `packages/`, migration) est de l'architecture factuelle :
> elle vit dans [`architecture.md`](architecture.md), pas ici.

## 7. Le frontend : deux niveaux de visibilité

- **Étape 1 — visibilité immédiate** : une coquille de démo, assumée *non-prod*,
  pour *voir* l'agent parler (chat web, streaming). But : un GIF de 20 s pour le
  README — pas la porte d'entrée finale.
- **Étape 2 — vrai front déployé** : interface web séparée, **URL live**,
  consommant l'agent par la couture API (Phase 13).

---

> 📌 Document vivant. On le met à jour quand un scope démarre, quand une couture
> se matérialise (API), ou quand le cap évolue. Voir aussi `ROADMAP.md` (le
> chemin), [`architecture.md`](architecture.md) (le COMMENT physique) et
> `docs/perimetre-final.md` (la definition of done).
