# 🎯 Périmètre final — la « definition of done » du produit

> Ce document est la **destination**, pas la prochaine étape. La `ROADMAP.md`
> décrit le *chemin pédagogique* (on apprend une couche à la fois) ; ici on note
> ce à quoi doit ressembler le produit **fini**, pour ne jamais confondre « ça
> marche en démo » et « c'est prêt pour la prod ».

## Le cap (non négociable)

Le tuto n'est que le moyen. La cible est un **chatbot de support client 100%
fonctionnel, digne d'un déploiement professionnel, réalisé dans les règles de
l'art à jour en 2026** — pas une maquette de démonstration.

Conséquence pratique : à chaque phase, on distingue explicitement ce qui relève
du **pédagogique/démo** (acceptable temporairement) et ce qui est déjà de la
**qualité prod**. Les choix « en RAM » actuels (`InMemorySaver` / `InMemoryStore`)
sont assumés comme **provisoires**.

---

## Partie A — Ce que la ROADMAP couvre déjà

Les phases restantes construisent l'essentiel du produit :

| Phase | Apport au produit final |
|---|---|
| **7 — Human-in-the-loop** | escalade réelle vers un humain : indispensable, on ne laisse jamais l'IA seule sur les cas sensibles. |
| **8 — Outils & actions** | l'agent *agit* (statut commande, création de ticket) — ce qui le rend utile, pas seulement bavard. |
| **9 — Évaluation & qualité** | tests de non-régression : sans eux, on ne *sait pas* si une modif casse l'agent. |
| **10 — Persistance & robustesse** | survivre à un redémarrage, encaisser les erreurs transitoires (429/timeout). |
| **11 — Cycle de vie du support (Case + Ticket)** | dossiers de support comme en prod : log auto, statuts, signature du bot, récurrence. |
| **12 — Sécurité & guardrails** | résister aux entrées malveillantes, protéger les PII, borner ce que les outils peuvent faire. |
| **13 — Exposition & déploiement** | rendre l'agent appelable de l'extérieur (API / serveur), config par environnement. |

---

## Partie B — Le radar prod (pas encore des phases à part entière)

Ces points font partie du périmètre final mais ne sont pas encore des étapes
dédiées dans la ROADMAP. On les garde au radar pour les traiter au bon moment,
plutôt que de les découvrir en production.

> **Promus en phases** (voir ROADMAP) : *Persistance durable* et *Robustesse* →
> **Phase 10** (persistance SQLite faite, fallback/erreurs restants) ; *Sécurité
> & guardrails* → **Phase 12**. Le *cycle de vie du support* (Case + Ticket) est
> devenu la **Phase 11**. Restent au radar :

1. **Coût & latence**
   Choix du modèle par tâche (ex : un petit modèle suffit pour le nœud `router`),
   streaming des réponses, caching, résumé de conversation pour les fils longs.
   Un chatbot pro doit être rapide et maîtrisé côté budget.

2. **Cycle de vie de la mémoire**
   Mise à jour / oubli des faits obsolètes. Aujourd'hui `save_memory` empile une
   nouvelle entrée sans jamais corriger : si un client change d'avis, les deux
   faits contradictoires coexistent.

---

> 📌 Document vivant : on le met à jour quand un point du radar devient une phase,
> ou quand un nouveau critère « règles de l'art » apparaît.
