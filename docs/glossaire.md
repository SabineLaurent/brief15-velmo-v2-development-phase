# 📖 Glossaire — le jargon dev, en clair

> Les mots d'anglais tech qui reviennent dans le projet, expliqués simplement.
> Doc **vivante** : on ajoute une entrée dès qu'un terme mérite d'être retenu.
> Classé par ordre alphabétique.

---

## agnostic (agnostique)

« Qui ne **dépend pas** d'un choix particulier, qui ne tranche pas. » Ici, le
code applicatif est agnostique au **fournisseur de LLM** : passer de Mistral à
Groq ou Azure = **une variable `.env`**, pas une réécriture. C'est l'invariant
n°1 du projet. *(Origine : grec* a-gnostos*, « qui ne sait pas ».)*

## checkpointer

*(LangGraph)* Le composant qui **sauvegarde l'état de la conversation** après
chaque étape, indexé par `thread_id`. C'est lui qui fait que « l'agent se
souvient du fil en cours » (mémoire **courte**). Cf. `memoire.md`.

## couture (angl. *seam*)

Le **point de contact unique et stable** entre deux parties d'un système, qui
**cache l'intérieur** de chacune. Ici `stream_reply` : le front lui parle sans
jamais voir LangGraph. Une bonne couture rend les deux côtés **remplaçables**
indépendamment (on peut jeter Chainlit sans toucher au cerveau). Cf.
`streaming.md`, `vision.md` §4.

## embedding

La transformation d'un texte en **vecteur de nombres** qui capture son *sens*.
On compare alors des textes par **proximité de sens** (et non de mots-clés). Brique
de base du RAG et de la mémoire longue.

## factory (patron *fabrique*)

Une **fonction qui fabrique un objet** selon la config, pour que le reste du code
n'ait pas à savoir *comment* il est construit. Ici `get_chat_model()` fabrique le
bon LLM depuis `.env` → c'est le cœur concret de l'agnosticisme.

## fallback

Le **plan B automatique** : si le fournisseur principal est en panne, on bascule
sur un secondaire sans planter le tour. *(Littéral. « se rabattre sur ».)*

## hop

*(Litt. « saut / bond ».)* Un **aller-retour complet vers le LLM** : ton code
envoie un prompt → réseau → le modèle traite → la réponse revient. **Un appel =
un hop.** Le coût d'un hop est surtout du **round-trip + traitement du prompt**
(pas la puissance du modèle) : une question client qui enchaîne **3 hops
séquentiels** (router → décision d'outil → réponse) paie **3×** ce coût fixe. D'où
la hiérarchie des leviers de latence : **supprimer un hop** économise ~1 s d'un
coup, alors que **prendre un modèle plus petit** ne change presque rien. Cf.
`latence.md`.

## hot reload / watch (`-w`)

Le **rechargement à chaud** : l'outil surveille les fichiers et recharge l'app
dès qu'on édite, **sans redémarrer** à la main. Pur confort de développement.

## human-in-the-loop

*(Litt. « un humain dans la boucle ».)* Un flux où l'agent **se met en pause** et
**rend la main à un humain** pour une décision, puis reprend. Ici : l'escalade
(`interrupt`) quand un cas dépasse l'agent.

## kill switch

*(Litt. « interrupteur d'arrêt ».)* Un **interrupteur unique** qui désactive tout
un dispositif d'un coup. Ici `GUARDRAILS_ENABLED` : off = le graphe redevient
**exactement** comme avant, sans surcoût.

## KV cache (*Key-Value*)

*(Interne au modèle.)* Le **« travail déjà mâché »** sur les tokens qu'un LLM a
déjà lus. En traitant un texte, le modèle calcule pour chaque token une **clé (K)**
et une **valeur (V)** — qui ne changent plus ensuite. Plutôt que de les recalculer
à chaque token suivant, il les **garde en mémoire** : c'est le KV cache. Analogie :
les **annotations** d'un contrat déjà lu, qu'on réutilise au lieu de tout
re-décortiquer. C'est **ce que le prompt caching réutilise d'un appel à l'autre**
quand le préfixe est identique (→ input moins cher + 1er token plus rapide). Cf.
[`prompt-caching.md`](prompt-caching.md).

## lean

**Prononciation :** « line ». **Sens littéral :** *maigre*, *sans gras*.

En dev (et au-delà), « lean » qualifie quelque chose de **sobre, minimaliste,
réduit à l'essentiel** — l'inverse de « gras / *bloated* » (surchargé, redondant).

**Concrètement, un fichier / code / process *lean* est :**
- **court** — que l'essentiel, pas de pavés ;
- **sans duplication** — il *renvoie* vers la source au lieu de la recopier ;
- **ciblé** — rien que ce qui a de la valeur ici ;
- **actionnable** — clair et directement utile, pas de blabla.

**Exemple dans le projet :** le `CLAUDE.md` d'un package est *lean* — il ne dit
que le **local** et renvoie vers `docs/` au lieu de répéter le root. Un fichier
« gras » serait long, répéterait les docs, et noierait l'info importante.

**D'où ça vient (culture générale utile) :** le terme naît du *lean
manufacturing* de Toyota (années 1950) — produire en **éliminant le gaspillage**,
ne garder que ce qui crée de la valeur. L'idée a essaimé partout :
- **lean startup** — construire le minimum pour apprendre vite (le *MVP*), sans
  sur-investir avant d'avoir validé ;
- **lean code** — du code simple et sans superflu ;
- **run lean** — fonctionner avec peu de ressources.

**À retenir en une phrase :** *lean = garder ce qui a de la valeur, couper le
superflu.*

## memoization (*mémoïsation*)

**Retenir le résultat d'un calcul pour ne pas le refaire.** Le mot vient de *memo*
(le pense-bête) : la fonction note sa réponse sur un carnet, et à la question
suivante regarde d'abord le carnet. ⚠️ Orthographe : *memo**i**zation*, **sans
« r »** — ce n'est pas *memorization*, malgré les apparences.

**Condition de validité :** la fonction doit rendre **toujours** le même résultat
pour la même entrée (on la dit *pure* ou *déterministe*). « Quelle dimension fait
`mistral-embed` ? » → toujours 1024, mémoïsable. « Quel est le statut de la
commande 42 ? » → change avec le temps, **surtout pas** mémoïsable : on
fabriquerait un bug invisible.

Ici : la dimension des vecteurs d'embeddings, sondée une fois puis relue depuis un
petit fichier JSON, au lieu d'un appel réseau à chaque démarrage.

**À ne pas confondre avec une empreinte** (*fingerprint*), qui pose une question
différente : la mémoïsation demande « **je connais déjà la réponse ?** » et suppose
qu'elle ne change jamais ; l'empreinte demande « **ce qui est stocké est-il encore
valide ?** » et suppose que la source a pu changer. La première fait gagner du
temps ; la seconde protège la **justesse**. Cf. [`prompt-caching.md`](prompt-caching.md)
pour un cache du même esprit, mais côté provider.

## monorepo / workspace

*monorepo* = **un seul dépôt git** qui héberge plusieurs projets liés.
*workspace* (uv) = la **mécanique** qui les fait cohabiter proprement (un
`uv.lock` + un `.venv` **partagés**). Ici : `packages/support-agent`,
`packages/client`… Cf. `anatomie-package-workspace.md`.

## MVP (*Minimum Viable Product*)

Le **produit minimal viable** : le moins qu'il faut construire pour
apprendre/valider quelque chose, sans sur-construire. Proche cousin de *lean*.

## RAG (*Retrieval-Augmented Generation*)

« Génération augmentée par récupération. » Avant de répondre, l'agent **va
chercher** les passages pertinents (ici dans la FAQ) et répond **à partir d'eux**,
au lieu d'inventer. C'est ce qui **ancre** les réponses dans une vraie source.

## ReAct (*Reason + Act*)

Le patron d'agent où le LLM **alterne raisonnement et actions** — appeler un
outil, lire le résultat, continuer — jusqu'à la réponse. Ici : la boucle
`model ⇄ tools` de la branche support.

## scratch

*(Litt. « gribouillage / brouillon ».)* Un espace ou fichier de **travail
jetable** : essais, données locales, sorties intermédiaires — **hors du projet
livré**, ni versionné ni partagé. L'analogie : la feuille de brouillon sur le côté
du bureau, pas la copie rendue. On parle de **scratch directory** (dossier
temporaire). ⚠️ Contre-exemple instructif dans ce projet : le dossier `TEMP/` a
été **renommé `database/`** (2026-07-21) précisément parce qu'il n'était *pas* du
scratch — il contenait l'état durable de l'agent (conversations, souvenirs). Un
dossier gitignoré n'est pas forcément jetable. ⚠️ À ne pas confondre avec **from scratch** = « **à partir de zéro** »
(ex. « réécrire l'app from scratch »), un sens différent.

## SSE (*Server-Sent Events*)

Un moyen, pour un serveur, de **pousser des données au fil de l'eau** vers le
navigateur sur **une seule** connexion HTTP. C'est le transport typique du
**streaming** de tokens en prod (le front consomme le flux). Cf. `streaming.md`.

## token

L'**unité de découpage du texte** pour un LLM — un bout de mot (~4 caractères en
moyenne, variable). Le modèle **génère token par token** : c'est exactement ce
qu'on voit défiler quand la réponse « s'écrit » en streaming.

---

<!-- Prochaines entrées possibles : boilerplate, idempotent, retriever, guardrails,
     prompt injection, PII, structured output… à ajouter quand le terme est croisé. -->
