# 🧠 La mémoire de l'agent — les types et où ils vivent

> Note de référence. On distingue **deux axes** : la durée (court vs long terme)
> et, dans le long terme, **trois types cognitifs** (sémantique / épisodique /
> procédural). But : ne jamais confondre « se souvenir de CE fil », « connaître
> CE client » et « avoir appris à bien faire ».

## Vue d'ensemble

| Mémoire | Question à laquelle elle répond | Support technique | Clé | État projet |
|---|---|---|---|---|
| **Court terme** | « Qu'est-ce qu'on s'est dit dans CE fil ? » | checkpointer (`InMemorySaver` / `SqliteSaver`) | `thread_id` | ✅ (durable possible) |
| **Long terme — sémantique** | « Qu'est-ce que je sais de CE client ? » (faits) | `Store` + recherche sémantique | `("memories", user_id)` | ✅ (`save_memory`) |
| **Long terme — épisodique** | « Un cas *ressemblant* a-t-il déjà été bien traité ? » | `Store` (autre namespace) | `("memories", "episodes")` | ❌ à construire |
| **Long terme — procédural** | « Quelle est LA bonne façon de faire ? » (règles) | instructions / prompt qui évolue | (le system prompt) | ❌ à construire |

À part, mais crucial : le **système métier** (tickets, commandes) via le port
`SupportBackend` est le **registre de référence** des incidents — pas de la
« mémoire » au sens agent, mais *la* source de vérité vérifiable.

## Les trois types, sur un cas « souci de livraison »

**Sémantique = des FAITS sur le client.**
Ex. stocké : *« A eu un colis (CMD-1001) marqué livré mais non reçu, juillet 2026. »*
→ rangé dans `("memories", user_id)`, ressorti par `search_memories` au début
d'un futur échange. Sert à **personnaliser** (« je vois que ça vous est déjà
arrivé »).

**Épisodique = une EXPÉRIENCE précise, gardée au format cas.**
Ex. stocké : *« Le 3 juillet, client X, Colissimo livré-non-reçu → vérifié
tracking, ouvert ticket transporteur, proposé renvoi → résolu, satisfait. »*
→ un **cas daté**, repêché **par similarité** avec la situation courante, injecté
en **few-shot**. Sert à **réutiliser ce qui a marché**.

**Procédural = une RÈGLE généralisée, appliquée à chaque fois.**
Ex. : *« Colis livré-non-reçu ⇒ (1) vérifier tracking, (2) ouvrir ticket
transporteur, (3) renvoi après 48 h. »*
→ vit dans le **system prompt**, **toujours actif** (pas repêché par
ressemblance). Sert à **standardiser** la bonne pratique.

### Le fil qui les sépare (le test)
Même matière première (des expériences passées), traitée différemment :

- On garde **le cas particulier**, repêché par ressemblance → **épisodique**.
- On abstrait **une consigne** appliquée à tous les cas → **procédural**.
- On retient juste **un fait** sur le client → **sémantique**.

Et ils s'enchaînent : on **accumule des épisodes**, on repère le motif, on en
**distille une règle procédurale**. Le procédural est souvent de l'épisodique
généralisé.

## Détecter une récurrence (« ça lui est déjà arrivé »)

Deux mécanismes complémentaires — plus un, le plus fiable :

1. **Sémantique par `user_id`** — au début du nouvel échange, `search_memories`
   fait le rapprochement par **embeddings + similarité vectorielle**, isolé par
   client. Best-effort : il faut que l'agent ait *pensé* à `save_memory` avant.
2. **Épisodique** — `store.search(("memories","episodes"), ...)` sort le cas
   passé ressemblant comme exemple de résolution.
3. **⭐ Le plus fiable : le backend métier** — un outil déterministe type
   `list_customer_tickets(user_id)` rend « ça s'est déjà produit » **vérifiable**
   (2 tickets « livraison » en 3 mois), là où la mémoire n'est qu'un pari.
   → complément recommandé, encore à ajouter au port `SupportBackend`.

## Mémoire court terme : garder / trimmer / résumer

Le **court terme** (le fil courant) pose deux questions distinctes.

**1. Les échanges sont-ils stockés ?** → choix du checkpointer :

| Choix | Effet |
|---|---|
| aucun checkpointer | pas de souvenir, chaque tour repart de zéro |
| `InMemorySaver` | stocké en RAM, perdu au redémarrage |
| `SqliteSaver` | stocké sur disque, survit au redémarrage |

**2. Faut-il tout garder / tout envoyer au LLM ?** Sur un fil long, l'historique
complet dépasse la fenêtre de contexte (et coûte cher). LangGraph offre quatre
leviers natifs :

- **Trimming** (`trim_messages`) — garder une fenêtre (N messages / X tokens).
- **Suppression** (`RemoveMessage`, `REMOVE_ALL_MESSAGES`) — retirer des messages
  du state (nécessite le reducer `add_messages`, déjà fourni par `MessagesState`).
- **Résumé** (nœud de summarization) — condenser les vieux messages en un résumé,
  puis remplacer par `résumé + N récents`. ⭐ C'est le **« résumé de
  conversation »** à ne pas confondre avec l'épisodique : il compresse le fil
  courant (court terme), il n'apprend pas de cas passés.
- **Gestion des checkpoints** — quoi/combien on conserve.

**Nuance clé : *persisté* ≠ *envoyé au LLM*.**
- Élaguer le **stockage** (`RemoveMessage` / résumé qui remplace) → modifie le
  checkpoint, perte de l'info brute.
- Trimmer **seulement à l'appel** (`trim_messages` dans le nœud, sans réécrire le
  state) → l'historique complet reste stocké, le LLM ne voit qu'une fenêtre.

**État projet.** Le nœud support (`graph/nodes.py`) envoie *tout* l'historique à
chaque tour et le checkpointer stocke *tout* : choix le plus simple, OK pour des
fils courts. Trim / résumé sont des ajouts **locaux** (avant l'`invoke`, ou un
nœud `summarize` conditionné à la longueur), sans toucher au reste ni à
l'agnosticisme (le résumé passe par `get_chat_model()`). → point radar **coût &
latence**.

## À retenir
- Le **backend** dit *que* c'est arrivé (vérité). Le **sémantique**
  *personnalise*. L'**épisodique** dit *comment bien faire*. Le **procédural**
  *standardise*. Ils se complètent.
- Aujourd'hui l'agent n'a que **court terme** + **sémantique**. Épisodique,
  procédural et l'outil d'historique tickets sont des extensions naturelles une
  fois le `Store` durable (fait) en place.
