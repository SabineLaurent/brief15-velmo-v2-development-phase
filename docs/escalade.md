# L'escalade vers un humain — pourquoi elle ne met PAS le graphe en pause

**Objet :** la conception de la branche `escalate`, et pourquoi le `interrupt()`
de la Phase 7 en a été retiré.
**Date de la décision :** 2026-07-26
**Défaut d'origine :** C1 de [`revue-code-2026-07-25.md`](revue-code-2026-07-25.md).
**Se lit avec :** [`architecture.md`](architecture.md) (le graphe) et
[`../ROADMAP.md`](../ROADMAP.md) (Phase 7, Phase 11).

---

## 1. Le défaut, tel qu'il se voyait du côté client

L'ancien nœud `escalate` appelait `interrupt()` : le graphe se mettait en pause et
attendait un `Command(resume=…)`. En CLI ça marchait — la conseillère, c'était la
personne qui tapait dans le même terminal. **Derrière une API, ça tuait le fil.**

Mesuré, sur un même `thread_id` :

```
tour 1   « je veux parler à un humain »        → __interrupt__ : True
tour 2   « finalement, quels sont vos délais ? » → __interrupt__ : True
tour 3   « bonjour ? »                          → __interrupt__ : True
```

LangGraph reprend la **tâche pendante** avant toute autre chose : le nouveau
message est bien ajouté à l'état, mais le `router` n'est **jamais** réévalué. Le
nœud re-exécute, rappelle `interrupt()`, repart en pause. Le client reçoit
indéfiniment la même phrase, et **aucun log ne signale d'erreur** : du point de vue
du serveur, tout s'est bien passé.

---

## 2. Ce que fait un vrai service de support en production

C'est ce qui a tranché, plus que n'importe quel argument d'élégance.

**Rien n'est « en pause ».** Une conversation n'est pas un processus suspendu,
c'est **une ligne en base** avec un statut et un assigné. Toutes les plateformes
(Zendesk, Intercom, Salesforce Service Cloud, Freshdesk, Gorgias…) partagent la
même ossature :

```
Conversation / Case
├── status    : open · pending · snoozed · solved · closed
├── assignee  : le BOT, ou une équipe, ou un humain nommé
├── channel   : chat · email · WhatsApp · in-app
└── messages  : la suite, horodatée, jamais interrompue
```

L'IA n'est pas un système à part : c'est **un assigné parmi d'autres**. Escalader,
c'est `assignee: bot → file humaine`. Concrètement :

1. le statut passe en *attente d'un humain*, la conversation entre dans une file ;
2. un humain est **notifié** ;
3. le bot est **coupé** sur cette conversation (« agent takeover ») — muet, pas bloqué ;
4. le client **continue d'écrire dans le même fil**. Ses messages s'empilent et
   attendent l'humain. **Personne n'ouvre un second thread.**

Et le retour de l'humain passe par **le canal**, pas par l'agent : boîte de
réception persistante, e-mail, notification. C'est là que l'industrie a résolu le
problème — dans le transport, pas dans le bot.

---

## 3. La décision

> `escalate` **crée le dossier, coupe le bot, et termine le tour.**

| | Avant | Après |
|---|---|---|
| Mécanisme | `interrupt()`, graphe en pause | ticket + drapeau d'état, tour terminé |
| Le fil après escalade | **mort** | vivant |
| Suppose un opérateur branché | oui | non |
| Objet du dossier | un thread LangGraph gelé (opaque) | un **ticket** (listable, assignable) |

Trois pièces :

- **`make_escalate(backend, tool_guard)`** — ouvre un ticket via le port
  `actions/` (donc agnostique au SI), journalise l'événement, renvoie un message
  de confirmation portant le numéro de dossier, et pose `handled_by_human = True`.
- **`handled_by_human`** dans `SupportState` — le drapeau « agent takeover ».
- **`human_takeover`** — le nœud qui répond aux tours suivants **sans appel LLM** :
  le bot est muet, pas en train de réfléchir. Répondre reviendrait à parler
  par-dessus la conseillère sur son propre dossier.

---

## 3 bis. QUAND escalader — le critère, et pourquoi il a changé le déclencheur

Le correctif ci-dessus règle *ce qui se passe* à l'escalade. Il ne disait rien de
*quand elle part* — et le routeur la déclenchait au premier « je veux un humain »,
**avant toute tentative**. Or :

> **Un agent de support IA existe pour alléger les tâches humaines qui n'ont pas
> besoin d'un humain** : les demandes basiques, répétées, où personne n'apporte de
> valeur ajoutée à la résolution.

Escalader sans avoir essayé est donc un **échec du produit**, pas une politesse.
Et l'inverse est vrai aussi : faire patienter derrière trois tentatives de bot un
client qui envoie une mise en demeure ne soulage personne non plus. La règle
opérationnelle est symétrique :

> **Escalader exactement quand un humain apporte de la valeur — ni moins, ni plus.**

⚠️ Ce critère a d'autant plus de poids que le drapeau `handled_by_human` **coupe le
bot pour de bon** : le correctif de C1 a *augmenté le coût d'un faux positif*. Un
déclencheur trop prompt n'était plus seulement inélégant, il confisquait le fil.

### Le défaut structurel : le routeur décide sans information

Le routeur ne voit **que le message**. Or savoir si un humain est nécessaire dépend
de ce que le bot aurait trouvé — la FAQ répond-elle ? l'outil commande donne-t-il le
statut ? — et cette information n'existe qu'**après** l'exécution de la branche
support. On demandait à un classificateur de trancher une question pas encore
décidable.

### La correction : l'escalade devient une ISSUE, pas une classification d'entrée

| Chemin | Qui décide | Quand |
|---|---|---|
| `escalate` (route du routeur) | le routeur, sur le message seul | **uniquement** l'urgence réelle : litige formel, menace, détresse |
| `request_human_handoff` (**outil**) | le modèle de la branche support, **après** avoir cherché | il a tenté et ne peut pas ; le client insiste après une offre d'aide ; la décision demande une autorité |

L'outil renvoie un `Command(update=…)` qui pose `handled_by_human` — mécanisme
vérifié dans la doc LangGraph avant d'être écrit : `ToolNode` propage la mise à jour,
à condition d'inclure un `ToolMessage` portant le `tool_call_id`.

**Le bénéfice qu'on sous-estime :** le dossier part avec son **contexte**. Mesuré en
live, un ticket réellement produit par l'outil :

```
Sujet : Handoff (agent-requested): contestation d'un prélèvement non
        autorisé de 340 € nécessitant une validation
Corps : Le client conteste un prélèvement de 340 € qu'il affirme n'avoir jamais
        autorisé. J'ai consulté la FAQ : toute demande de remboursement
        supérieure à 50 € doit être validée par un conseiller…
```

Dans un vrai service, le coût humain d'un dossier n'est pas de le **prendre**, c'est
de le **comprendre**. Un handoff sans contexte fait payer deux fois.

### La porte de sortie

`human_takeover` annonce toujours le chemin du retour (« répondez *reprendre* ») et
le reconnaît **par regex — zéro appel LLM**. Reprendre la main **ne clôt pas le
dossier** : la conseillère le garde, le bot se contente de répondre au reste. Une
décision faillible doit être réversible ; sinon la prudence devient une confiscation.

### Le câblage

L'arête d'entrée (`entry_route`) tranche dans cet ordre : *input refusé* → `END`
(un attaquant n'a pas droit à un accusé de réception) ; *dossier chez un humain* →
`human_takeover` ; sinon → `router`. Elle est câblée **indépendamment du kill
switch des guardrails** : la coupure du bot ne doit pas dépendre d'un réglage de
sécurité.

### Deux propriétés qui tombent gratuitement

- **Idempotence :** l'id du ticket est dérivé de son contenu, donc ré-exécuter le
  nœud sur le même message ne peut pas ouvrir un second dossier.
- **Anti-abus :** une fois le drapeau posé, le `router` n'est plus jamais atteint
  sur ce fil — un client qui répète « je veux un humain » ne peut pas empiler les
  tickets. **Le drapeau est le rate-limit.**

---

## 4. Ce qu'on a écarté, et pourquoi

**La reprise synchrone** (`POST /chat/{thread_id}/resume`) — ce que demandait
l'audit A2. Présentée comme « une route », elle en coûte quatre : la route,
l'exposition du payload, **la gestion des messages du client pendant la pause**
(sans quoi il est toujours avalé), et **un opérateur qui répond**. Le dernier
n'existe pas. Sans lui, le fil reste mort pour toujours : le bug d'origine, avec
une route en plus.

**La scission de thread** — mettre le fil escaladé en attente et ouvrir un nouveau
fil pour la suite. Idée séduisante, mais : le `thread_id` appartient à l'**appelant**
(la couture est `stream_reply(message, *, user_id, thread_id)`), donc il faudrait
exposer l'état du fil et inventer un protocole de bascule ; le nouveau fil démarre
**amnésique** (nouveau checkpointer, le client réexplique tout) ; et **ça n'existe
nulle part en prod** — le fil reste le même, c'est le *statut* qui change.

---

## 5. Ce qui reste ouvert (nommé, plus silencieux)

- **Le canal de retour.** Quand la conseillère répond, par où ça revient au client ?
  Le SSE est **portée-requête** : la connexion est fermée depuis longtemps. C'est un
  problème de **transport**, pas d'agent — il appartient au scope B (une boîte de
  réception persistante) et à la Phase 11.
- **La console d'opérateur.** Il n'y en a toujours pas. Le ticket la rend
  possible ; il ne la remplace pas.
- **Rattacher les messages d'attente au ticket.** Ils sont aujourd'hui dans le
  checkpointer (donc lisibles), pas dans le corps du dossier.
- **La reprise à l'initiative du bot.** Le client peut reprendre la main
  (« reprendre ») ; le bot, lui, ne se ré-attribue jamais un dossier tout seul —
  et ne le fera pas tant que personne ne peut **clore** un dossier (Phase 11).
- **La qualité du déclencheur ne se mesure pas encore.** Le taux de déflexion
  (part des conversations résolues sans humain) est LA métrique de cet agent ;
  le dataset d'éval porte maintenant les deux cas limites (`deflect-human-request`
  et `escalate-formal-dispute`), mais pas encore le taux lui-même.

---

## 6. Et `interrupt()` alors ?

Il n'est pas supprimé du projet : il est **déplacé**. Sa vraie place est une
**porte d'approbation avant une action irréversible** (remboursement, annulation,
suppression de compte) — là où quelque chose *ne doit pas* se produire sans accord
humain. Dans un transfert, l'agent n'est sur le point de **rien faire** : il passe
la main, il n'y a rien à retenir.

C'est aussi l'usage qu'en fait l'industrie. La Phase 7 n'a donc pas été annulée,
elle a été **relogée** ; la boucle de reprise du CLI (`agent.py`) est conservée
telle quelle pour la recevoir, et c'est la seule implémentation de reprise du
projet. Il manque, pour la brancher, une action irréversible — le projet n'en a
pas encore (`get_order_status` lit, `create_ticket` est réversible).
