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
> devenu la **Phase 11**.

> 🧭 **À lire d'abord — la nature du gap.** Le socle *conceptuel* est déjà de
> niveau pro (agnosticisme LLM, mémoire court/long terme, RAG, orchestration
> explicite, human-in-the-loop, éval, robustesse, guardrails). Ce qui sépare
> encore l'agent d'un vrai système de prod n'est donc **pas de l'architecture** :
> c'est de l'**infrastructure, de l'opérationnel et de la conformité**. On classe
> les manques par gravité, du bloquant au raffinement.

### B.1 — Bloquants absolus (sans ça, « prod » est un abus de langage)

1. **Exposition — l'agent n'est appelable par personne.**
   C'est la **Phase 13** (pas faite). Aujourd'hui c'est un
   `python -m support_agent.agent` en CLI : pas d'API, pas de serveur, pas de
   contrat d'interface.

2. **Authentification & autorisation** — le trou le plus dangereux.
   Toute l'isolation (mémoire, tickets, evaluator `no_cross_user_leak`) repose sur
   `user_id` **passé dans le contexte runtime**. Rien ne prouve que l'appelant
   *est* ce `user_id`. Il faut une couche d'auth (token/session) qui **dérive**
   `user_id` d'une identité vérifiée — sinon n'importe qui lit les commandes et la
   mémoire de n'importe qui. Les guardrails protègent de l'injection, **pas** de
   l'usurpation.

3. **Backends réels, pas « InMemory ».**
   Le défaut est en RAM, SQLite optionnel :
   - `InMemorySupportBackend` → écrire l'adaptateur vers le **vrai SI** (commandes,
     tickets). Le port `SupportBackend` est prêt, l'adaptateur reste à faire.
   - Checkpointer/Store SQLite = mono-nœud → un service multi-instances a besoin de
     **Postgres** (`PostgresSaver` / `PostgresStore`).
   - FAQ en `InMemoryVectorStore` (4 fichiers réingérés au boot) → un **vector
     store** persistant + un pipeline de ré-ingestion (la FAQ évolue).

### B.2 — Qualité prod attendue (pas bloquant au boot, mais un pro le voit tout de suite)

4. **Cycle de vie du support** — c'est la **Phase 11** : Case auto, statuts,
   signature, détection de récurrence. Sans ça, pas de traçabilité des dossiers.

5. **Observabilité au-delà du tracing.**
   LangSmith trace les runs (idéal pour débuguer), mais il manque le triptyque
   d'exploitation : **métriques** (latence p95, taux d'escalade, coût/conversation,
   taux de résolution), **logs structurés** centralisés, **alerting/SLO**. « Est-ce
   que l'agent va bien, là, maintenant ? » n'a aujourd'hui pas de réponse.

6. **Coût & latence.**
   Choix du modèle par tâche (un petit modèle suffit pour le nœud `router`),
   **streaming** des réponses (UX indispensable en chat), caching, résumé de
   conversation pour les fils longs. Un chat non-streamé qui répond en 6 s est
   perçu comme cassé.

7. **Rate-limiting distribué.**
   Le `RateLimiter` de la Phase 12 est **in-process** (documenté comme à remplacer
   par Redis). En multi-instances, la limite ne tient plus.

### B.3 — Conformité & gouvernance (l'oubli fatal classique d'un projet « technique »)

8. **RGPD / cycle de vie de la donnée.**
   La PII est masquée avant persistance (bien), mais il manque : **rétention**
   (purge auto), **droit à l'oubli** (supprimer *tout* d'un `user_id` sur demande),
   consentement. Inclut le **cycle de vie de la mémoire** : `save_memory` empile
   une nouvelle entrée sans jamais corriger → si un client change d'avis, deux
   faits contradictoires coexistent.

9. **Gestion des secrets.**
   Clés API dans `.env` → en prod : **secret manager** (Vault / KMS cloud),
   rotation, aucune clé en clair sur disque.

10. **La vraie boucle humaine.**
    `interrupt()` met le graphe en pause en mémoire — mais côté opérateur, ni file
    d'attente ni interface. Une escalade doit atterrir quelque part (queue,
    dashboard agent, SLA de reprise).

### B.4 — Rigueur d'ingénierie

11. **CI/CD.**
    `make check` existe (lint + tests) mais aucune **CI** ne le lance à chaque
    push, ni de pipeline de déploiement. La non-régression n'est utile que si elle
    est **automatique**.

12. **Couverture de tests élargie.**
    Aujourd'hui : guardrails, robustesse, éval. Manquent les nœuds du graphe, les
    chemins d'erreur, et un test d'intégration bout-en-bout.

### Priorisation (ordre qui minimise le risque réel)

1. **Auth/authz** (2) + **backends durables Postgres & SI réel** (3) — sans ça,
   le reste est cosmétique.
2. **Exposition/API** (Phase 13) + **streaming** (6).
3. **Observabilité opérationnelle** (5) + **CI** (11).
4. **Conformité RGPD** (8) + **secrets** (9).
5. **Phase 11** (cycle de vie) pour la complétude métier.

> ⚠️ Conséquence pour la ROADMAP : pour mériter le mot *prod*, la **Phase 13**
> (« exposition ») doit embarquer **auth + streaming + un backend durable**, pas
> juste « un endpoint qui répond ». Le chemin pédagogique reste 11 → 13 ; c'est le
> *contenu* de la 13 qui monte en exigence.

---

> 📌 Document vivant : on le met à jour quand un point du radar devient une phase,
> ou quand un nouveau critère « règles de l'art » apparaît.
