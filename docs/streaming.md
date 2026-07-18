# 🌊 Le streaming de la réponse — un contrat de bout en bout

> Note de référence (scope B). Quand on a branché le streaming token par token
> (phase B1.3), une question a surgi : **qui**, en prod, « gère » le streaming —
> le front ? un paramètre de l'API ? Réponse : **personne tout seul**. Le
> streaming est une **propriété que chaque couche doit préserver**. Si une seule
> bufferise, tout casse. Ce doc fixe la répartition des rôles.
> Complète [`vision.md`](vision.md) §4 (la couture) et
> [`roadmap-frontend.md`](roadmap-frontend.md) (B1.3, B2).

## Le principe : un flux qu'on ne retient jamais

Le streaming n'est pas un interrupteur unique posé quelque part. C'est une
**chaîne** où chaque maillon reçoit des tokens et les repasse **immédiatement**
au suivant, sans les accumuler :

```
LLM            →   graphe            →   stream_reply     →   TRANSPORT   →   front
(stream=True)      (stream_mode=         (async generator)     (réseau)        (rendu)
                    "messages")
   produit           expose               notre couture        SSE / HTTP      affiche
   les tokens        le flux              (forme native)        chunké          au fil de l'eau
```

Il suffit qu'**un** maillon attende d'avoir tout reçu pour transmettre, et
l'utilisateur voit un bloc au lieu d'un texte qui s'écrit.

## La répartition des rôles en prod

Trois couches, trois responsabilités **distinctes**. La confusion fréquente
(« c'est le front qui décide » / « c'est un paramètre de sortie de l'API »)
vient de ce qu'on mélange ces rôles.

| Couche | Rôle | Ce qu'elle NE fait PAS |
|---|---|---|
| **Agent / backend** | **Produire** les tokens — la source de vérité. Chez nous `stream_reply` **EST** un générateur async : le streaming est sa **forme native**, pas une option. | Ne ralentit pas, ne met pas en forme l'affichage. |
| **API (FastAPI / LangServe)** | **Transporter** le flux sur le réseau, via **SSE** (Server-Sent Events) ou HTTP chunké — typiquement `StreamingResponse` qui enveloppe `stream_reply`. | Ne décide pas *comment* c'est produit ni *comment* c'est peint à l'écran. |
| **Front (React, Chainlit…)** | **Rendre** au fil de l'eau, et gérer la **cadence d'affichage** (smoothing / typewriter). | Ne « demande » pas le streaming : il consomme un flux qu'on lui sert. |

> **En une phrase :** l'agent *produit*, l'API *transporte*, le front *affiche*.

## Le « paramètre » existe — mais à la frontière API, pas dans l'agent

L'intuition d'« un flag de streaming » est juste, à condition de le placer au bon
endroit. Une API expose souvent **deux modes de transport** :

- `POST /chat` → réponse **complète** en JSON (non-streaming) ;
- `POST /chat/stream` → **SSE** (streaming) ; ou un flag `stream: true`.

Ce flag choisit le **transport**, pas la capacité de l'agent à streamer. Un
appelant non-streaming se contente de **drainer le générateur** et de renvoyer la
concaténation — exactement ce que faisait la phase B1.2 avant qu'on ajoute
`stream_token` en B1.3.

## Smoothing / typewriter : c'est du **rendu**, donc côté front

Ralentir des tokens trop rapides (effet machine à écrire) ou lisser des arrivées
en burst (*smoothing*) est une **décision de présentation**. Règle :

- ❌ **Jamais dans `stream_reply` / l'agent.** La couture doit livrer les tokens
  **le plus vite possible** ; y injecter un délai ralentirait *tous* les
  consommateurs et polluerait le contrat. La cadence n'est pas un problème du
  cerveau.
- ✅ **Dans le front (couche de rendu)**, comme une **option** : on bufferise et
  on révèle à *X* caractères/seconde.

**Pertinence pour la démo :** faible. Un typewriter rend les réponses *plus
lentes* — l'inverse du bénéfice du streaming. Le vrai point d'UX n'est pas la
vitesse d'écriture mais le **silence avant le 1er token** (routing + RAG sur la
branche support) ; on le traite en montrant les **étapes** (phase B1.5), pas en
ralentissant l'écriture.

## Où on en est concrètement

- **Aujourd'hui (niveau 1, in-process).** `stream_reply` yield directement à
  Chainlit — **zéro réseau**. Le front fait `async for token in stream_reply(...)`
  puis `msg.stream_token(token)`. C'est le cas le plus simple : la couture
  d'agent et le rendu partagent le même process.
- **Demain (niveau 2, front découplé).** La **Phase 13 du scope A** ajoute l'API
  HTTP : c'est **elle** qui portera le streaming sur le réseau (SSE), en
  enveloppant la **même** `stream_reply`. Le React n'aura qu'à consommer le flux.
  Voir [`roadmap-frontend.md`](roadmap-frontend.md) §B2.

---

> 📌 À retenir : le streaming est un **contrat respecté à chaque couche**, pas un
> réglage unique. L'agent le **produit** (forme native de `stream_reply`), l'API
> le **transporte** (SSE), le front l'**affiche** (et lui seul décide de la
> cadence). Le typewriter/smoothing est un détail de **rendu**, jamais de cerveau.
