# Architecture classique d'un agent IA de support client

_Note de référence, agnostique — 2026-07-06._

> Contexte : l'expert (`reco_expert.md`) laisse l'architecture « à notre main ».
> Cette note prend de la hauteur sur la construction **classique** d'un agent de
> support, indépendamment de Velmo, pour éclairer les décisions de conception.

## Le modèle mental de base

Un agent de support, c'est **un LLM entouré de 4 systèmes satellites**,
coordonnés par un **orchestrateur**, et surveillés par une **boucle
d'évaluation** :

```
                        ┌──────────────────┐
                        │   ORCHESTRATEUR   │  ← décide quoi faire
                        └────────┬─────────┘
             ┌──────────┬────────┼────────┬──────────┐
             ▼          ▼        ▼        ▼          ▼
        ┌─────────┐┌────────┐┌──────┐┌────────┐┌──────────┐
        │ MÉMOIRE ││ SAVOIR ││OUTILS││ SÛRETÉ ││   LLM    │
        │(qui,hist)││(RAG/FAQ)││(APIs)││(guardrails)││(génère) │
        └─────────┘└────────┘└──────┘└────────┘└──────────┘
                        ▲
                        │
                  ┌───────────┐
                  │ ÉVALUATION │  ← mesure, boucle qualité
                  └───────────┘
```

Aucun agent sérieux n'échappe à ces briques. Ce qui varie, c'est **qui tient la
barre** (orchestrateur déterministe vs LLM autonome) et **le degré de
sophistication** de chaque satellite.

## Le pipeline canonique (le trajet d'un message)

Presque tous les agents de support suivent cette séquence, dans cet ordre :

| # | Étape | Rôle | Implémentation classique |
|---|-------|------|--------------------------|
| 1 | **Canal** | recevoir le message | widget web, e-mail, WhatsApp, téléphone (STT) |
| 2 | **Sûreté entrée** | filtrer l'interdit/hors-sujet | classifieur (modèle de modération ou règles) |
| 3 | **Assemblage du contexte** | « qui parle, de quoi » | récupération mémoire (voir plus bas) |
| 4 | **Compréhension & orchestration** | intention → plan d'action | routeur déterministe **ou** LLM planificateur |
| 5 | **Récupération de savoir** | trouver la bonne info | **RAG** sur base de connaissance |
| 6 | **Actions** | agir sur les systèmes réels | **tool/function calling** vers des APIs |
| 7 | **Génération** | rédiger la réponse | LLM, contraint par le contexte récupéré |
| 8 | **Sûreté sortie** | vérifier avant d'envoyer | anti-PII, anti-hallucination, ton |
| 9 | **Écriture mémoire** | retenir l'échange | persistance (voir plus bas) |
| 10 | **Escalade** | passer la main | handoff humain avec contexte |
| 11 | **Observabilité** | journaliser & évaluer | logs, traces, métriques, jeux d'éval |

## La décision structurante n°1 : qui orchestre ?

C'est **le** choix d'architecture, sur un spectre :

**A. Pipeline déterministe (« intent-based »)** — l'école historique.
Un routeur (règles/regex ou classifieur NLU) identifie l'intention, puis appelle
l'action correspondante. Le LLM ne fait que reformuler.
→ Prévisible, testable, sûr. Rigide : chaque cas doit être prévu.

**B. Agent LLM autonome (« tool-calling / ReAct / planning »)** — l'école moderne.
On donne au LLM la liste des outils et il **décide lui-même** de la séquence
d'appels, en boucle, jusqu'à résoudre. Variantes : ReAct (raisonne→agit→observe),
plan-and-execute, multi-agents (un superviseur délègue à des spécialistes).
→ Flexible, gère l'imprévu et le langage naturel. Moins prévisible → besoin de
garde-fous et confirmations solides.

**C. Hybride** — le compromis dominant en production support.
Déterministe sur les **actions à risque** (rembourser, annuler), LLM autonome sur
le **conversationnel** et parfois la récupération. C'est la position de Velmo.

> Règle empirique du domaine : **plus une étape peut coûter de l'argent ou violer
> une règle, plus on la veut déterministe.** Le LLM autonome brille sur la
> compréhension et la récupération ; il inquiète sur les actions irréversibles.

## La décision structurante n°2 : la mémoire à étages

Classiquement, la mémoire d'un agent support se **stratifie en 3 (voire 4)
niveaux**, car aucun support unique ne couvre tous les besoins :

| Étage | Contient | Support classique | Question à laquelle il répond |
|-------|----------|-------------------|-------------------------------|
| **Court terme / working** | les N derniers tours | buffer en RAM / la fenêtre du prompt | « de quoi parle-t-on *là* ? » |
| **Long terme structuré** | faits durables, préférences | **base relationnelle** (clé-valeur par user) | « quelle taille prend ce client ? » |
| **Long terme épisodique** | souvenirs, conversations passées | **base vectorielle** (recherche par similarité) | « m'a-t-il déjà parlé d'un flocage fragile ? » |
| **(optionnel) Résumé** | compression du vieux contexte | résumés LLM périodiques | « tenir la fenêtre sans tout perdre » |

Le mécanisme classique, à la **lecture** (`read`) :
1. On charge les **N derniers tours** (court terme).
2. On récupère les **faits structurés** du user (SQL : `WHERE user_id = ?`).
3. On **embed le message** courant et on interroge le vectoriel pour les **k
   souvenirs les plus proches** (épisodique).
4. On **fusionne + on tronque** au budget de tokens (les faits/souvenirs les plus
   pertinents d'abord).

À l'**écriture** (`write`) : on classe l'info — un fait durable (« je suis
revendeur ») va en relationnel ; un échange narratif va en épisodique (embeddé +
indexé). Le **droit à l'oubli** = suppression ciblée dans les deux stores.

C'est pourquoi l'expert impose **Postgres *et* Chroma** : ce ne sont pas deux
options, ce sont **deux étages complémentaires** de la même mémoire.
Postgres = vérité factuelle exacte ; Chroma = rappel associatif flou.

Principe transverse non négociable : **isolation par `user_id`** à chaque étage
(filtre SQL, namespace/collection vectorielle par user). Une fuite mémoire entre
clients est la faute la plus grave.

## Les autres satellites, en bref

- **Savoir (RAG)** : découper les documents (FAQ, CGV) en chunks → les embedder →
  indexer → au moment T, récupérer les chunks pertinents et **ancrer** la réponse
  dessus (« d'après notre FAQ… »). Objectif : répondre sans inventer, avec une
  source.
- **Outils (actions)** : chaque capacité métier (statut commande, remboursement)
  est une fonction avec un contrat clair. Les actions sensibles sont **encadrées**
  : confirmation explicite, plafonds, escalade au-delà.
- **Garde-fous (sûreté)** : deux filtres symétriques. **Entrée** = bloquer
  haine/violence/injection de prompt avant traitement. **Sortie** = bloquer
  PII/fuite de secret/hallucination avant envoi. Chaque décision est
  **journalisée** (observabilité).

## Ce qui distingue un prototype d'un système de prod : la boucle d'éval

La brique la plus souvent bâclée, et celle que l'expert érige en exigence :

- **Jeux d'évaluation** (« golden sets ») : des cas figés avec réponse attendue,
  par capacité (mémoire, sûreté, qualité).
- **Note globale** agrégée + **métriques** : taux de blocage, **taux de faux
  positifs**, latence, coût.
- **Seuil bloquant en CI** : une nouvelle version doit prouver sa non-régression ;
  sous le seuil → livraison bloquée.
- **Boucle en ligne** : logs de prod → détection de dérive → nouveaux cas
  d'éval → on ré-ajuste.

## Où cela nous laisse (puisqu'on a la main)

L'architecture-type ci-dessus **est** celle de Velmo — le scaffold suit le canon.
La latitude porte sur **le *comment* de chaque satellite**, pas sur la liste des
briques. Les vrais points de décision ouverts :

1. **Où placer le curseur déterministe ↔ autonome** (garder le routage regex, ou
   introduire du tool-calling LLM ?).
2. **La stratégie de mémoire** (comment classer fait durable vs épisodique,
   comment fusionner et tronquer au budget).
3. **La finesse des garde-fous** (règles vs modèle de classification) et le seuil
   de faux positifs.
4. **La définition de la note globale** et du seuil CI.
