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
| **Long terme — épisodique** | « Un cas *ressemblant* a-t-il déjà été bien traité ? » | `Store` (autre namespace) | `("episodes",)` — **pas de `user_id`** | ✅ construit |
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

## L'épisodique en pratique

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
octet** à celui d'avant l'épisodique — un store froid ne coûte rien et ne change rien.

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

**Protocole.** 7 cas du dataset d'évaluation × 2 répétitions × 2 bras
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

### Le comptage des embeddings (2026-07-28) — une hypothèse fausse, un vrai déchet

Hypothèse de départ : « la question est sûrement embeddée deux fois, une fois par
la FAQ, une fois par l'épisodique — mutualisons ». **Fausse**, et le comptage le
montre. Un tour support avec un appel d'outil = **3 allers-retours** d'embeddings
(⚠️ `OpenAIEmbeddings.embed_query` **délègue** à `embed_documents` : une trace
naïve compte 4 appels là où le réseau n'en voit que 3) :

| # | Qui | Texte embeddé |
|---|---|---|
| 1 | rappel épisodique | la question **verbatim** du client |
| 2 | recherche FAQ | `'délais de livraison en Europe'` — **reformulation du modèle** |
| 3 | rappel épisodique | **la même question verbatim, à nouveau** |

**Pourquoi la mutualisation ne marche pas :** la FAQ est un **outil**, donc c'est
le *modèle* qui écrit son `query` ; l'épisodique embedde le *message brut*. Textes
différents, instances `Embeddings` différentes, moments différents (nœud `tools`
vs nœud `model`). Il n'y a rien à partager.

**Le vrai déchet était ailleurs, et plus gros :** le rappel tournait à **chaque
passe de la boucle ReAct**. Le message du client ne change pas pendant un tour →
même entrée, même sortie, deux appels réseau facturés. Le coût n'était donc pas
300 ms par tour mais **300 ms × nombre de passes du nœud `model`**.

**Correctif :** mémoïsation dans `EpisodicRecall`, **clé = l'id du dernier message
humain**. Les id sont uniques par message, donc le memo s'invalide tout seul au
tour suivant — pas de TTL, pas de champ de state, rien dans le checkpoint. Memo de
taille 1 (les appels répétés sont consécutifs) : sous charge concurrente deux fils
peuvent s'évincer, ce qui coûte un recalcul, jamais un bloc erroné — la clé doit
correspondre. Vérifié : **3 → 2 allers-retours** sur le même tour.

### Confirmation bout-en-bout (`make latency`, 5 runs par bras, tracé LangSmith)

Après correctif, sur « Quels sont vos délais de livraison en Europe ? » :

| | Sans épisodique | Avec épisodique |
|---|---|---|
| **delivered** (l'attente réelle du client), médiane | **4,29 s** | **4,55 s** |
| min / max | 4,05 / 4,33 s | 4,24 / 5,01 s |
| `close_turn` (écriture du candidat) | — | **0,00 s** |
| Cache de prompt | 80 % (HIT) | 80 % (HIT) |

**+260 ms**, cohérent avec le microbenchmark. La mesure est concluante parce que
les deux bras ont envoyé un prompt **identique** — mêmes comptes de tokens
(1152/1258 puis 1152/1618) — vérification faite : **0 épisode injecté**, meilleur
score **0,326** contre un plancher à **0,35**. Ces 260 ms sont donc du **coût pur,
sans bénéfice sur ce tour**, ce qui est le cas nominal et non l'exception.

Deux invariants confirmés au passage : `close_turn` est **gratuit** (0,00 s — la
conception « écrire sans modèle » tient), et le bloc épisodique **ne casse pas le
cache de prompt** (80 % dans les deux bras), ce qui valide de l'avoir placé après
le prompt stable.

⚠️ À rapporter au bon dénominateur : ~260 ms sur un tour à ~4,3 s, c'est **~6 %**.
Mesurable, pas perceptible. Le TTFT reste dominé par les **hops LLM séquentiels**
(routeur 1,2 s + modèle 1,3 s + outil + modèle 1,5 s) — voir [`latence.md`](latence.md).

### Ce qui reste ouvert
- **Programmer la consolidation** (cron App Service) — aujourd'hui c'est manuel.
- **Plafonner / dédupliquer le vivier** : rien ne limite encore le nombre
  d'épisodes ni ne fusionne deux cas quasi identiques. Le TTL (compté depuis le
  **dernier accès**) fait déjà mourir les épisodes que personne ne repêche.
- **La latence du rappel.** Deux pistes examinées, une seule était réelle — voir
  la mesure ci-dessous. Restent, si les ~300 ms deviennent un sujet : embeddings
  **locaux** pour le rappel, ou rappel **conditionnel** (au 1er tour d'un fil,
  ou selon la décision du routeur).
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

**État projet : le levier retenu est le résumé qui REMPLACE** (nœud `compact`,
`memory/compaction.py`). Jusqu'à `COMPACT_AFTER_MESSAGES` messages (défaut **30**)
l'historique part *entier* au LLM — c'est ce qui tient l'exigence des 30 tours.
Au-delà, le bloc le plus ancien est condensé dans `state["summary"]` et **retiré**
du state via `RemoveMessage(REMOVE_ALL_MESSAGES)`, la queue récente
(`COMPACT_KEEP_LAST_MESSAGES`, défaut 10) restant verbatim.

Trois choix à connaître avant d'y toucher :

- **On supprime vraiment, on ne trimme pas seulement à l'appel.** Trimmer à
  l'`invoke` garderait un checkpoint qui grossit sans fin : le tour resterait bon
  marché pendant que le stockage — donc les données personnelles à effacer —
  continuerait de s'accumuler. Une copie de moins à oublier.
- **Le nœud est AVANT le router**, pas après la réponse. Compacter après coup
  compacterait pour le tour *suivant* en envoyant quand même le prompt surdimensionné
  maintenant — exactement la panne qu'on veut éviter.
- **« Sans perdre l'information critique » n'est pas le travail du résumé.** Un
  fait durable (« client pro », « tutoie-moi », un n° de contrat) vit dans la
  mémoire **long terme**, indexée par `user_id`, qui survit à la compaction. Le
  résumé a le droit d'oublier la *formulation* du tour 3 précisément parce que ce
  qui comptait a été *sauvegardé comme fait*. C'est la division du travail entre
  les deux mémoires qui tient l'exigence, pas le résumé seul.

Le coût est **un appel LLM sur le tour qui franchit le seuil**, soit environ un
tour sur vingt avec les valeurs par défaut. En cas d'échec du provider, le nœud
renvoie `{}` : on garde l'historique **complet** plutôt que de perdre des tours
qu'on n'a pas su résumer.

## Inspecter et effacer ce que l'agent a retenu

Deux surfaces, une seule logique (`memory/privacy.py`) :

| Qui | Comment | Pour quoi |
|---|---|---|
| le **client**, dans la conversation | l'outil `forget_memory` | « oublie mon numéro de commande » |
| l'**opérateur**, en ligne de commande | `make memory ARGS='--user-id X …'` | audit, effacement RGPD art. 17 |

```bash
make memory ARGS='--user-id alice'                       # tout ce qui est retenu
make memory ARGS='--user-id alice --forget "order id"'    # ciblé, À BLANC
make memory ARGS='--user-id alice --erase --write'        # effacement total
```

**Le mot dur de l'exigence est « vérifiable ».** Un `delete` qui renvoie `None`
ne prouve rien : il ne distingue pas « 3 faits supprimés » de « rien ne
correspondait, je n'ai rien fait ». D'où deux mécanismes :

1. toute fonction **retourne les enregistrements sur lesquels elle a agi** ;
2. `delete_user_memories` **relit chaque clé** après suppression et **lève** si
   une ligne survit. Un backend qui a avalé l'écriture, un cache périmé ou une
   faute de namespace seraient sinon invisibles — et le client s'entendrait dire
   que ses données ont disparu alors qu'elles sont toujours en base.

⚠️ **Le plancher de similarité est le piège de cette fonctionnalité.** Supprimer
sur une correspondance faible détruit le **mauvais** fait, irréversiblement.
`FORGET_MIN_SCORE` (défaut **0,35**) est donc **mesuré**, pas choisi : sur
`text-embedding-3-small`, le fait visé sort entre 0,40 et 0,59 selon la
formulation, les faits non visés entre 0,09 et 0,24. Un premier essai à 0,6
paraissait « prudent » et était la pire valeur possible — au-dessus de *toutes*
les vraies correspondances, donc la fonctionnalité ne supprimait **jamais rien**
en annonçant « rien ne correspondait ». Si tu montes ce seuil, revérifie qu'une
suppression réelle se produit encore.

**Rétention ≠ effacement, et l'un ne remplace pas l'autre.** Le *sweeper* TTL
(`MEMORY_TTL_DAYS`) répond à « on ne garde pas éternellement » : il expire les
lignes après un délai compté depuis le **dernier accès**, donc les données d'un
client *actif* n'expirent jamais. `--erase` répond à « supprimez les miennes,
maintenant ».

🔴 **Trou connu et assumé : les transcripts ne sont pas effaçables par
`user_id`.** La mémoire long terme est indexée par `user_id`, mais les
checkpoints le sont par `thread_id`, et **aucun index user→threads n'existe** dans
ce projet. `forget_thread(thread_id)` efface une conversation qu'on peut
*nommer* (`make memory ARGS='--user-id X --thread-id T --write'`) ; effacer
*tous* les fils d'un client demanderait cet index. À construire si le besoin
devient réel.

## Conformité au cahier des charges mémoire (R1 → R6)

| | Exigence | Où ça se joue |
|---|---|---|
| **R1** | tenir 30 tours | checkpointer (`memory/short_term.py`) + `MessagesState` ; rien n'est coupé sous le seuil |
| **R2** | persister d'une session à l'autre | store par `user_id` + outils `save_memory` / `search_memories` |
| **R3** | isolation stricte | `memories_namespace(user_id)`, **une seule** définition ; le `user_id` vient du runtime context, jamais du LLM |
| **R4** | résumer au-delà de 30 | nœud `compact` (`memory/compaction.py`) + faits durables en long terme |
| **R5** | droit à l'oubli | outil `forget_memory` (client) + `make memory --forget/--erase` (opérateur), suppression **relue et vérifiée** |
| **R6** | traçabilité | `make memory --user-id X` liste **tout** (paginé), avec `created_at` / `updated_at` |

**Où c'est verrouillé par des tests** — R1 et R2 dans
`tests/test_memory_requirements.py`, R3/R5/R6 dans `tests/test_memory_privacy.py`,
R4 dans `tests/test_compaction.py`. Tous **hors ligne** (embeddings factices,
SQLite en `tmp_path`) : une suite qui réclame un provider et une clé est une suite
qu'on finit par ne plus lancer.

➕ **Et le corpus du starter est exécuté** (2026-07-29) : les 12 cas de
`data/eval/memory_cases.jsonl` sont pilotés par `tests/test_memory_cases.py`
(36 tests hors ligne : 19 sur SQLite, 17 de plus dès qu'un Postgres est offert —
voir juste en dessous). Ils n'ajoutent pas une exigence, ils ajoutent de la
**largeur** — dix natures de fait de plus, et surtout **deux paires** que les
fichiers précédents n'avaient pas :

- la **paire R3** est la même phrase avec un numéro de commande différent, pour
  deux clients. Sous embeddings sac-de-mots les deux faits ont des vecteurs
  **identiques** : la similarité ne peut pas les distinguer, seul
  `memories_namespace(user_id)` les sépare. C'est ce qui rend cette paire
  meilleure qu'un test d'isolation à texte distinct — elle retire la possibilité
  de passer par chance lexicale. Vérifiée en cassant le namespace : le test
  devient rouge, et lui seul.
- la **paire R5** vérifie l'oubli par **trois chemins** (rappel sémantique, dump
  d'audit, valeur de retour) *et* après réouverture du stockage — une purge qui
  n'aurait vidé qu'un cache en RAM passerait les trois premiers.

⭐ **Et depuis le 2026-07-30, ces 12 cas tournent une fois PAR MOTEUR de
persistance** (chantier 8) : SQLite toujours, **Postgres/pgvector** dès qu'une base
est offerte (`EVAL_DATABASE_URL` ; la CI en démarre une). Le raisonnement tient en
une phrase : **R3 est une propriété de sécurité**, et elle repose sur deux moitiés
dont une seule est à nous. `memories_namespace(user_id)` est notre code, commun aux
deux moteurs ; la **requête de similarité** qui pourrait ramener la ligne du voisin
appartient au store — sqlite-vec ici, pgvector là. La prouver sur l'un ne dit rien
de l'autre, et la production, c'est l'autre.

Effet secondaire qui valait le détour : **R1 est enfin durable**. Le fil était
rejoué sur un saver en RAM ; il tourne maintenant sur celui du moteur et se relit à
travers un **second objet saver** sur le même stockage. `PostgresSaver` fait donc
son aller-retour sous assertion, ce qui n'existait nulle part.

Deux adaptations à savoir défendre, toutes deux dans le même esprit que le piège
de portage ci-dessous : **chaque tag est asserté contre le mécanisme qui
l'implémente** (R1 → checkpointer, R2/R3 → store, R5 → `forget_user_memories`), et
sur R2 l'assertion porte sur la **présence** dans le rappel, pas sur le rang.
Noter le rang sous des embeddings factices mesurerait le faux : « statut de
compte » ne partage aucun mot avec « je suis revendeur », donc un vrai modèle le
classe et celui-là ne peut pas. Le rang est asserté là où c'est loyal — face à un
concurrent réel — dans `test_memory_requirements.py`.

R1 et R2 ont été ajoutés le 2026-07-29 en comparant notre suite aux critères
d'acceptance de référence : ils étaient
implémentés et raisonnés, mais aucun test ne les tenait. Attention au piège de
portage — là-bas R1 et R2 sont **une seule** classe et R1 passe par une recherche
sémantique ; ici ce sont **deux mécanismes distincts** (checkpointer / store), et
asserter R1 via une recherche sémantique testerait le mauvais.

Sur R3, une nuance à savoir défendre : c'est le **sémantique** qui est cloisonné.
L'**épisodique** est partagé entre clients — par conception — et ce qui tient R3
là est l'**anonymisation à l'écriture**, pas le cloisonnement (voir plus haut).

Sur R6, une décision : **aucun journal d'écritures maison**. Le store horodate
déjà chaque ligne ; une seconde copie serait une seconde chose à synchroniser —
et à effacer.

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
