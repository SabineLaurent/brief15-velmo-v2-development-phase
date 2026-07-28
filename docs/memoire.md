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
| **Long terme — épisodique** | « Un cas *ressemblant* a-t-il déjà été bien traité ? » | `Store` (autre namespace) | `("episodes",)` — **pas de `user_id`** | ✅ Phase 14 |
| **Long terme — procédural** | « Quelle est LA bonne façon de faire ? » (règles) | instructions / prompt qui évolue | (le system prompt) | ⛔ **hors périmètre** (décidé le 2026-07-28) |

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
généralisé — c'est d'ailleurs pour ça qu'on peut s'en passer ici (⛔ hors
périmètre, voir plus bas) : l'épisodique en porte déjà l'essentiel du bénéfice.

## L'épisodique en pratique (Phase 14)

Le *quoi* est dit plus haut. Voici le *comment*, et c'est là que se joue la
différence entre une démo et un agent qui tient en prod.

### Le schéma : quatre champs, écrits **avec le recul**

Repris de LangMem (`observation` / `thoughts` / `action` / `result`). Le champ
qui justifie toute la fonctionnalité est **`thoughts`** : sans lui, un épisode
n'est qu'un couple question→réponse, c'est-à-dire un doublon plus cher de la FAQ
que l'agent interroge déjà. Avec lui, l'agent reçoit un **exemple travaillé** de
la façon dont un collègue a raisonné jusqu'à la résolution.

### Le cycle, en trois temps — un seul appel LLM, et il est hors ligne

```
un tour client   ─►  écrit un CANDIDAT    (1 upsert, 0 modèle)      nœud close_turn
`make consolidate` ─►  écrit un ÉPISODE     (1 appel LLM / fil)       hors ligne
un tour suivant  ─►  lit les épisodes     (1 appel embeddings)      nœud model
```

**Pourquoi ce découpage.** Distiller un cas coûte un appel LLM. Le faire pendant
le tour ajouterait un 3ᵉ hop séquentiel à une réponse déjà à ~5 s
([`latence.md`](latence.md)) — et distillerait un **fragment**, puisqu'en milieu
de conversation on ne connaît pas encore l'issue.

**Le debounce est gratuit** : le candidat est **une clé par `thread_id`**,
réécrite à chaque tour. Un fil = un candidat, toujours à jour. La consolidation
ne ramasse que les fils **silencieux depuis `EPISODIC_IDLE_MINUTES`** — rien dans
un chat ne dit « au revoir » de façon fiable, le silence est le seul signal de fin
de cas exploitable.

> 🔁 **Pourquoi pas le `ReflectionExecutor` de LangMem** (l'équivalent officiel) :
> c'est une file **en RAM**. Sur App Service, un recyclage ou un scale-out la perd
> — en silence, la pire façon pour une boucle d'apprentissage d'échouer. Nos
> candidats sont des lignes du `Store` : elles survivent au redémarrage, se
> relisent, se testent hors ligne. Et le candidat ne stocke qu'un **pointeur**
> (`thread_id`) : la conversation est déjà persistée par le checkpointer, la
> recopier créerait un 2ᵉ exemplaire de données personnelles à faire oublier.

### Les trois garde-fous, et ce qu'ils empêchent

**1. Le filtre de qualité : on n'apprend que des cas RÉSOLUS.**
`resolved = not handled_by_human`. Une mémoire épisodique qui stocke ses échecs
**empoisonne le vivier** dans lequel elle pioche ses few-shot — et invisiblement.
Le signal existait déjà : c'est le drapeau d'escalade, celui-là même sur lequel se
mesure le taux de déflexion ([`escalade.md`](escalade.md)).

**2. Le plancher de pertinence (`EPISODIC_MIN_SCORE`).**
Une recherche vectorielle renvoie **toujours** son meilleur match : elle ne sait
pas dire « rien ici ne colle ». Sans plancher, le **premier épisode jamais écrit**
atterrit dans **toutes** les conversations — l'agent n'a pas l'air de se souvenir,
il a l'air de radoter. Un match faible est pire que pas de match : il coûte de la
place dans le prompt pour désigner la mauvaise piste. *(Ce défaut existait dans la
première version du code ; c'est un test qui l'a sorti.)*

**3. L'anonymisation à l'écriture — parce que le namespace est PARTAGÉ.**
`("episodes",)` ne porte **pas** de `user_id`, contrairement au sémantique où
l'isolation *est* la fonctionnalité. C'est voulu (apprendre du cas d'Alice pour
servir Bob), donc la fuite est **structurelle** : ce qu'un épisode contient *sera*
montré à un autre client. Deux défenses, toutes deux **à l'écriture** :
le prompt d'extraction ordonne de généraliser (ni nom, ni n° de commande), et
`save_episode` repasse le même `ToolGuard.sanitize` que les outils d'écriture.
La première est une promesse de modèle, la seconde est une regex — d'où les deux.

### Où ça se branche

| Élément | Fichier |
|---|---|
| schéma, lecture/écriture, format du prompt | `memory/episodic.py` |
| distillation hors ligne | `memory/consolidate.py` (`make consolidate`) |
| capture du candidat | nœud `close_turn` (`graph/nodes.py`, câblé dans `builder.py`) |
| injection few-shot | `EpisodicRecall`, dans le nœud `model` |
| réglages | `EPISODIC_*` dans `.env` |

⚠️ **Le bloc épisodique s'ajoute APRÈS `SUPPORT_SYSTEM_PROMPT`, jamais avant.** Le
cache de prompt travaille sur un **préfixe stable** : un contenu variable placé en
tête invaliderait le cache à chaque tour ([`prompt-caching.md`](prompt-caching.md)).
Et quand aucun épisode ne passe le plancher, le prompt est **identique octet pour
octet** à celui d'avant la Phase 14 — un store froid ne coûte rien et ne change rien.

> Pourquoi une **injection automatique** et pas un outil, alors que
> `search_memories` en est un ? Parce que ce ne sont pas les mêmes connaissances.
> Savoir qu'il faut relire les faits d'un client, le modèle peut le décider (le
> client y fait référence). Savoir qu'il faudrait un cas ressemblant, **non** : un
> modèle qui patauge ne sait pas qu'il patauge — c'est précisément le moment où
> l'exemple vaut le plus. Le rail LangChain 1.x pour ça est le middleware
> `@dynamic_prompt`, réservé à `create_agent` ; notre `StateGraph` explicite fait
> l'équivalent dans le nœud.

### ⚠️ Une éval ne doit pas nourrir ce qu'elle mesure

Défaut trouvé **au premier run live**, pas en relecture. `make check` et `make
eval` pilotent le **vrai** graphe : avec l'apprentissage actif, chaque run
distillait des épisodes **à partir des conversations qui servent à noter
l'agent**, et le run suivant était noté contre un vivier que le précédent avait
fait grossir. Un score qui monte tout seul — la façon la plus flatteuse pour un
benchmark de mentir. Concrètement : deux `make check` avaient déjà déposé 12
candidats dans la base de dev.

Correctif : `build_support_graph(learn_from_turns=False)` pour l'éval. Le nœud
`close_turn` n'est alors **pas câblé du tout** — c'est la topologie compilée qui
garantit qu'aucun chemin ne peut écrire, pas un drapeau lu à l'exécution. Le
**rappel reste actif** : on note l'agent tel qu'il est déployé. Mesurer l'apport
de l'épisodique se fait en basculant `EPISODIC_MEMORY_ENABLED` **volontairement**,
jamais en laissant le harnais écrire.

## 📊 La mesure du 2026-07-28 — sans dommage, coût chiffré, gain NON prouvé

**Protocole.** 7 cas du dataset Phase 9 × 2 répétitions × 2 bras
(`EPISODIC_MEMORY_ENABLED` false/true), même modèle, graphe d'éval en
`learn_from_turns=False`. Vivier de **6 épisodes** distillés de 6 conversations
portant sur des sujets FAQ **absents du dataset** (échange, entretien, frais de
port, authenticité, délai de remboursement, réassort) — s'entraîner sur le test
aurait fabriqué le gain.

| | Sans épisodique | Avec épisodique |
|---|---|---|
| Assertions d'évaluateurs passées | **22/22** | **22/22** |
| Cas où un épisode a franchi le plancher | — | **1 sur 7** |

**Ce que ça prouve : l'absence de dommage.** Aucune régression, sur aucun cas.
C'est un vrai résultat — un dispositif qui touche au system prompt de la branche
support pouvait très bien dégrader le routage ou la citation de source.

**Ce que ça NE prouve PAS : le gain.** Et ce n'est pas réparable par un run de
plus, pour trois raisons structurelles :
1. **Effet plafond** — l'agent était déjà à 100 % *avant* l'expérience. Un
   dataset de non-régression ne peut mesurer qu'une chute.
2. **1 cas sur 7 enrichi** — les 6 autres ont tourné avec un prompt **identique
   octet pour octet** dans les deux bras. Sur ces cas-là, les deux bras sont
   littéralement le même code : comparer n'a pas de sens.
3. **Évaluateurs déterministes** — route exacte, présence d'une citation, mots de
   refus. Ils ne voient pas une réponse *mieux écrite* ou *mieux méthodique*,
   c'est-à-dire exactement ce qu'un épisode est censé apporter.

**Le coût, lui, est mesurable — et il l'a été à part.** Le delta bout-en-bout
(+0,68 s de moyenne) est **inutilisable** : des cas à **0 épisode injecté**
affichaient +1,4 s et +1,8 s, donc le bruit dépasse le signal à n=2. Isolé au
microbenchmark, le rappel coûte **~300 ms médians par tour support** (l'aller-retour
embeddings), soit ~6-8 % d'un tour à ~5 s.
⚠️ **Ces 300 ms sont payés même quand le rappel ne ramène rien** (2 sondes sur 3
sont revenues vides et ont payé le même prix). Sur un vivier vide — le jour 1 —
c'est 300 ms par tour support pour rien : un court-circuit « vivier vide → pas
d'appel » est l'optimisation évidente, non faite à ce jour.

**Pour mesurer un vrai gain il faudrait** un dataset de cas où l'agent échoue ou
répond de façon inégale (pas 100 % d'entrée), un **juge sémantique** plutôt que des
heuristiques de forme, et un vivier alimenté par du **trafic réel**. C'est un
chantier d'évaluation, pas un run de plus.

### Ce qui reste ouvert
- **Programmer la consolidation** (cron App Service) — aujourd'hui c'est manuel.
- **Plafonner / dédupliquer le vivier** : rien ne limite encore le nombre
  d'épisodes ni ne fusionne deux cas quasi identiques. Le TTL (compté depuis le
  **dernier accès**) fait déjà mourir les épisodes que personne ne repêche.
- **La latence du rappel (~300 ms/tour support).** ⛔ Le court-circuit « vivier
  vide → pas d'appel » a été **examiné puis écarté** : il n'aide que tant que le
  vivier est vide (jour 1, après purge), et surtout il ne mord pas sur le vrai
  coût — les 300 ms sont payés à *chaque* tour dès qu'il y a un seul épisode, y
  compris quand rien ne franchit le plancher. ⭐ **La piste sérieuse, non
  vérifiée à ce jour** : dans un même tour, la question du client est
  probablement embeddée **deux fois** — une fois par la recherche FAQ
  (`knowledge/`), une fois par le rappel épisodique. Si c'est bien le même
  texte, mutualiser l'appel supprime 100 % du surcoût. **C'est la première chose
  à vérifier** avant toute autre optimisation (embeddings locaux, rappel
  conditionnel au routeur, rappel au 1er tour seulement).
- **Prouver le gain**, si on le veut vraiment : cela demande un dataset de cas
  *ratés*, un juge sémantique et du trafic réel — pas un run de plus.

⚠️ **État du vivier de dev** : 6 épisodes, ceux de l'expérience du 2026-07-28
(sujets FAQ hors dataset d'éval). Ce ne sont pas des données de production.

## ⛔ Le procédural ne sera pas construit (décision du 2026-07-28)

Ce n'est **pas un oubli ni une tâche en attente** — c'est un choix, à ne pas
rouvrir sans déclencheur explicite. Ça reste décrit ici comme **piste « pour aller
plus loin »**, parce que la taxonomie n'a de sens qu'entière et que le concept
s'explique bien : la même passe de consolidation, sur N épisodes semblables,
distillerait une **règle** poussée dans le system prompt.

Ce que ça coûterait, et qui n'est pas payé : un prompt qui **s'auto-modifie** n'est
plus un artefact versionné qu'on relit en revue — il faudrait le versionner, le
diffuser, le faire approuver, et pouvoir revenir en arrière quand une mauvaise
règle dégrade toutes les conversations d'un coup (l'épisodique, lui, se trompe cas
par cas). L'épisodique apporte l'essentiel du bénéfice sans ce rayon de souffle.

**Déclencheur qui justifierait de rouvrir :** un motif récurrent, mesuré sur le
vivier d'épisodes, que le rappel par ressemblance rate systématiquement — donc
après la mesure de déflexion, pas avant.

## Détecter une récurrence (« ça lui est déjà arrivé »)

Deux mécanismes complémentaires — plus un, le plus fiable :

1. **Sémantique par `user_id`** — au début du nouvel échange, `search_memories`
   fait le rapprochement par **embeddings + similarité vectorielle**, isolé par
   client. Best-effort : il faut que l'agent ait *pensé* à `save_memory` avant.
2. **Épisodique** — `store.search(("episodes",), ...)` sort le cas passé
   ressemblant comme exemple de résolution. ⚠️ Il ne dit **rien** sur CE client :
   le cas vient peut-être de quelqu'un d'autre. Il répond « comment bien faire »,
   jamais « est-ce que ça lui est déjà arrivé ».
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
- L'agent a **court terme + sémantique + épisodique**, et c'est le périmètre
  final. Le **procédural** est décrit pour la compréhension, **volontairement pas
  construit** (voir la section dédiée) : un prompt auto-modifié se trompe sur
  toutes les conversations à la fois, là où un épisode se trompe cas par cas.
- La ligne à ne jamais franchir : le sémantique est **cloisonné par client**,
  l'épisodique est **partagé**. Deux namespaces, deux régimes de confidentialité.
  Confondre les deux, c'est transformer un vivier d'exemples en fuite de données.
