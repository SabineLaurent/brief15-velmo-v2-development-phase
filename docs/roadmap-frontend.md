# 🗺️ ROADMAP — Scope B (Frontend)

> La [`ROADMAP.md`](../ROADMAP.md) racine trace le **scope A** (le cerveau).
> Ce document trace le **scope B** : rendre l'agent *montrable*. Même méthode —
> une phase = un **objectif d'apprentissage** + un **livrable** concret ; on ne
> passe à la suivante qu'une fois la précédente comprise ET fonctionnelle.
> Contexte stratégique : [`vision.md`](vision.md) §7 (les deux niveaux de
> visibilité) et §4 (la couture API, contrat entre agent et front).

Légende : ✅ fait · 🚧 en cours · ⬜ à venir

---

## Le cap du scope B (rappel de la vision §7)

Deux **niveaux de visibilité**, à ne pas confondre :

- **Niveau 1 — coquille de démo** (ce document, phases B1.x) : une UI web de chat,
  **assumée non-prod**, pour *voir l'agent parler* (streaming). But concret : un
  **GIF de 20 s pour le README**. Techno : **Chainlit** (Python, zéro front à
  écrire — parfait pour apprendre le cycle d'une app de chat).
- **Niveau 2 — vrai front déployé** (phases B2.x, plus tard) : interface web
  séparée, **URL live**, consommant l'agent par l'**API HTTP** (Phase 13 du scope
  A). Découplé au point qu'on remplace Chainlit par du React sans toucher au cerveau.

> On construit le **niveau 1 maintenant** ; le niveau 2 attend la Phase 13 (l'API
> HTTP), car c'est *elle* la couture que le vrai front consommera.

## Le principe non négociable : la couture (vision §4)

Le front **ne connaît jamais** l'intérieur lang* (LangGraph, nœuds, state). Il
parle à **un seul point d'entrée stable** côté agent :

```
   Chainlit (scope B)  ──►  stream_reply(...)  ──►  [ LangGraph caché ]
                            ↑ LA COUTURE (contrat)
```

⚠️ Les tutos Chainlit classiques font `graph.stream(...)` **dans** l'app front —
on s'en écarte **volontairement** : ce couplage nous interdirait de swapper le
front. La couture `stream_reply` est ce qui rend Chainlit **jetable**.

> 🔗 **Où vit la couture.** La phase **B1.1** ci-dessous écrit du code dans
> `packages/support-agent/` (la couture *appartient* au cerveau — comme la Phase
> 13), tout en étant réalisée dans le cadre du scope B. Le mot « scope » désigne
> l'**appartenance logique** du code, pas la branche qui l'accueille.

---

## Phase B1.0 — Migration workspace ✅
**Objectif :** transformer le mono-package en **uv workspace** pour accueillir
`packages/frontend/` à côté de `packages/support-agent/` sans tout mélanger.
**Livrable :** arborescence `packages/*`, un `uv.lock` + `.venv` partagés.
**Fait :** commit `ed9fb1a` (voir [`architecture.md`](architecture.md) §9 et
[`anatomie-package-workspace.md`](anatomie-package-workspace.md)).

## Phase B1.1 — La couture API : `stream_reply` (côté agent) ⬜
**Concept :** un **contrat stable** entre l'agent et n'importe quel front — un
point d'entrée unique qui **cache** LangGraph. Pourquoi : c'est ce qui garde le
front interchangeable (on pourra jeter Chainlit).
**Où :** `packages/support-agent/src/support_agent/api.py` (scope A — la porte de
sortie du cerveau, cf. vision §3).
**Livrable :** une fonction **async** `stream_reply(message, *, user_id, thread_id)`
qui **yield** des tokens de la réponse finale. Elle encapsule : l'`invoke`/`stream`
du graphe, le `thread_id` (mémoire courte) et le `user_id` (contexte runtime /
mémoire longue), et **ne laisse fuiter aucun objet lang***. Testable **sans**
Chainlit (un petit script qui itère sur le générateur).
**Détail piège :** notre graphe a **plusieurs** nœuds LLM (`router` à sortie
structurée, `answer`, `support`). En `stream_mode="messages"`, il faut **ne
streamer que la réponse cliente**, pas la décision du routeur. Ce filtrage vit
**dans la couture**, pas dans le front.

## Phase B1.2 — « Hello Chainlit » : la coquille minimale ⬜
**Concept :** le **cycle de vie** d'une app Chainlit — `@cl.on_chat_start`,
`@cl.on_message`, `cl.Message(...).send()`, la commande `chainlit run`. Créer le
membre `packages/frontend/` (dépend de `support-agent` via `workspace = true`).
**Livrable :** une page de chat dans le navigateur qui appelle `stream_reply` et
affiche la réponse **complète** (pas encore de streaming). *On voit l'agent parler.*
**Pédagogie :** d'abord le câblage (un tour qui marche), le confort ensuite.

## Phase B1.3 — Le streaming token par token ⬜
**Concept :** `msg.stream_token(token)` + `msg.update()`. **Pourquoi c'est
critique** : un chat non-streamé qui répond en 6 s est *perçu comme cassé*
(cf. [`perimetre-final.md`](perimetre-final.md) §6). Brancher le générateur de
`stream_reply` sur le flux Chainlit.
**Livrable :** la réponse s'écrit **au fil de l'eau**, comme un vrai assistant.

## Phase B1.4 — Session, `thread_id` & identité ⬜
**Concept :** `cl.user_session` (état par session) et `cl.context.session.id`
(→ notre `thread_id` de mémoire courte). Relier une **session Chainlit** à la
**mémoire** de l'agent (courte via `thread_id`, longue via `user_id`).
**Livrable :** la conversation **garde son contexte** dans l'UI ; on peut simuler
plusieurs utilisateurs.
**Honnêteté prod (assumée) :** en démo, le `user_id` est **simulé** (pas d'auth).
C'est le trou n°2 du [`perimetre-final.md`](perimetre-final.md) — documenté comme
**non-prod**, résolu au niveau 2 (auth réelle dérivant `user_id` d'une identité).

## Phase B1.5 — Sources FAQ & visibilité des étapes ⬜
**Concept :** rendre l'agent **lisible** dans l'UI — l'esprit « capot ouvert » du
scope A, côté front. Afficher les **citations FAQ** (`cl.Text(display="side")`) et,
en option, les **étapes du graphe** (routing, appels d'outils) via les *steps* /
`cl.LangchainCallbackHandler`.
**Livrable :** quand l'agent cite la FAQ, la source est **consultable** ; les
actions (statut commande, ticket) sont visibles. *La confiance passe par la
transparence.*

## Phase B1.6 — Escalade humaine dans l'UI + polish + le GIF ⬜
**Concept :** présenter dans le front le cas **escalade** (le `__interrupt__` de la
Phase 7 — l'agent se met en pause et rend la main à un humain). Puis le vernis
démo : écran d'accueil (`chainlit.md`), *starters*, thème, et `make ui` dans le
`Makefile` (porte d'entrée unique).
**Livrable :** le cas escalade est **honnêtement affiché** ; l'app est présentable ;
on tourne **le GIF de 20 s pour le README**. → fin du **niveau 1**.

---

## Phase B2 — Le vrai front déployé (plus tard, après Phase 13) ⬜
**Pré-requis :** la **Phase 13 du scope A** (API HTTP) doit exister — c'est la
couture réseau universelle que ce front consommera.
**Concept :** interface web séparée (React ou autre), **URL live**, qui ne parle à
l'agent que par **HTTP** (aucune dépendance au workspace, cf.
[`workspace-et-deploiement.md`](workspace-et-deploiement.md)).
**Livrable :** le projet est *montrable en ligne* — la « definition of done » de la
vision (§1).

---

### Où on en est (scope B)
- [x] B1.0 — migration workspace (`packages/*`)
- [ ] B1.1 — couture `stream_reply` (côté agent) ← **prochaine étape**
- [ ] B1.2 — hello Chainlit (coquille minimale)
- [ ] B1.3 — streaming token par token
- [ ] B1.4 — session, `thread_id` & identité
- [ ] B1.5 — sources FAQ & visibilité des étapes
- [ ] B1.6 — escalade dans l'UI + polish + GIF
- [ ] B2 — vrai front déployé (après Phase 13)

> 📌 Document vivant. Les phases B1.x construisent la **coquille de démo** (niveau
> 1) ; B2 est le **front déployé** (niveau 2). Voir aussi [`vision.md`](vision.md)
> (le cap) et la [`ROADMAP.md`](../ROADMAP.md) racine (le scope A).
