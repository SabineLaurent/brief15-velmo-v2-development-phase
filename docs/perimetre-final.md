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
| **10 — Exposition & déploiement** | rendre l'agent appelable de l'extérieur (API / serveur), config par environnement. |

---

## Partie B — Le radar prod (pas encore des phases à part entière)

Ces points font partie du périmètre final mais ne sont pas encore des étapes
dédiées dans la ROADMAP. On les garde au radar pour les traiter au bon moment,
plutôt que de les découvrir en production.

1. **Persistance durable**
   Remplacer `InMemory*` par un backend durable (SQLite / Postgres) via les
   factories `get_checkpointer()` / `get_store()` — le « one-line change » prévu
   dans leurs docstrings. Sans ça : redémarrage du process = amnésie totale
   (mémoire court **et** long terme perdues).

2. **Robustesse & gestion d'erreurs**
   Déjà rencontré en vrai avec les `429 Rate limit` de Mistral. Un agent pro gère
   proprement : retries avec backoff, timeouts, fallback provider — pas un
   `try/except` de script de test.

3. **Sécurité & guardrails**
   Prompt-injection, filtrage/masquage des données personnelles (PII), limites
   sur ce que les outils ont le droit de faire. Critique dès qu'un vrai client
   parle à l'agent.

4. **Coût & latence**
   Choix du modèle par tâche (ex : un petit modèle suffit pour le nœud `router`),
   streaming des réponses, caching. Un chatbot pro doit être rapide et maîtrisé
   côté budget.

5. **Cycle de vie de la mémoire**
   Mise à jour / oubli des faits obsolètes. Aujourd'hui `save_memory` empile une
   nouvelle entrée sans jamais corriger : si un client change d'avis, les deux
   faits contradictoires coexistent.

---

> 📌 Document vivant : on le met à jour quand un point du radar devient une phase,
> ou quand un nouveau critère « règles de l'art » apparaît.
