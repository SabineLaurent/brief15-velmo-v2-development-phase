# 🌊 Le streaming de la réponse — un contrat de bout en bout

> Note de référence (scope B). Quand on a branché le streaming token par token
> (phase B1.3), une question a surgi : **qui**, en prod, « gère » le streaming —
> le front ? un paramètre de l'API ? Réponse : **personne tout seul**. Le
> streaming est une **propriété que chaque couche doit préserver**. Si une seule
> bufferise, tout casse. Ce doc fixe la répartition des rôles.
> Complète [`vision.md`](vision.md) §4 (la couture) et
> [`roadmap-frontend.md`](roadmap-frontend.md) (B1.3, B2).

> 🛑 **Mise à jour importante (revue de code).** La répartition des rôles
> ci-dessous reste **entièrement valable** — c'est le modèle mental à garder.
> Mais **chez nous, le maillon « agent » bufferise volontairement** : voir
> [§ L'exception qui prime : le garde de sortie](#lexception-qui-prime--le-garde-de-sortie).
> Lis cette section avant d'appliquer la règle « ne jamais retenir le flux ».

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
| **Agent / backend** | **Produire** le flux — la source de vérité. Chez nous `stream_reply` **EST** un générateur async : le streaming est sa **forme native**, pas une option. ⚠️ Il décide aussi **ce qui est publiable** : aujourd'hui il retient tout jusqu'au garde de sortie (§ ci-dessous). | Ne ralentit pas, ne met pas en forme l'affichage. |
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

## L'exception qui prime : le garde de sortie

Le principe « ne retenir jamais le flux » suppose une chose qu'on n'avait pas
vue : que **tout token produit est un token publiable**. Chez nous c'est **faux**.

Le **garde de sortie** (`guard_output`, phase 12-A/B) est un **nœud postérieur**
aux nœuds LLM : il caviarde les PII/secrets et **remplace** une réponse qui
recopie le prompt système. Or streamer en direct, c'est **afficher avant** que ce
nœud ait tourné :

```
answer/model ──tokens──► [AFFICHÉ AU CLIENT] ──► guard_output ──► « ah, il fallait caviarder »
                              ↑ trop tard : c'est déjà à l'écran
```

Le garde ne protégeait donc plus que le **checkpoint**. Pas le client.

**Peut-on garder les deux ?** Non. Le garde raisonne sur la réponse **complète**
(il peut la remplacer entièrement), il ne peut pas valider un token isolé. Donc :

> **Garder ≠ streamer : c'est exclusif.** Tant que la vérification porte sur le
> texte entier, rien ne peut être libéré avant la fin.

**Décision retenue :** la correction prime sur le confort. `stream_reply` lit
l'**état terminal** du graphe et livre le message garanti-gardé en **un chunk**.
Conséquence assumée : **TTFT = temps total** (cf. [`latence.md`](latence.md)).

**Ce qu'on n'a PAS fait**, et pourquoi :

| Option | Pourquoi non |
|---|---|
| Streamer puis **rétracter** ce que le garde change | La PII est **affichée** avant d'être retirée. Un garde qui fuit d'abord n'est pas un garde. |
| Streamer **seulement si** `GUARDRAILS_ENABLED=false` | L'UX changerait avec un kill switch, et le défaut est `true` : bénéfice quasi nul pour une branche en plus. |
| Garder **par tokens** (fenêtre glissante) | Marche pour un caviardage local, **pas** pour « remplacer toute la réponse ». Faux sentiment de sécurité. |

**Le vrai remède au silence** n'est pas d'afficher du texte non vérifié : c'est de
rendre l'attente **lisible** en montrant les **étapes** du graphe (« je consulte
la FAQ… ») — **phase B1.5**. Le contrat reste un générateur async, donc B1.5
n'aura qu'à yielder plus de chunks : **aucun front à modifier**.

## Où on en est concrètement

- **Aujourd'hui : les trois couches existent pour de vrai** (déploiement étapes 1
  et 4). Le flux traverse le réseau et retrouve sa forme de générateur de l'autre
  côté :

  ```
  graphe ──► stream_reply()      produit   (support_agent/api.py)
         ──► StreamingResponse   transporte en SSE   (support_agent/server.py)
         ──► stream_reply()      reconstitue le générateur (client_chainlit/agent_client.py)
         ──► msg.stream_token()  affiche   (client_chainlit/app.py)
  ```

  Le point à retenir : les deux `stream_reply` ont **la même signature**. Le front
  fait toujours `async for chunk in stream_reply(...)` — il ne sait pas, et n'a pas
  à savoir, qu'il y a un réseau au milieu. C'est ce qui a permis à `app.py` de ne
  changer que d'une ligne d'import quand Chainlit est devenu un client HTTP.
- Le flux ne contient toujours **qu'un chunk** (la réponse gardée), mais chaque
  couche le traite **comme un flux** : le jour où B1.5 en yield plusieurs, rien à
  reprendre. Le transport SSE est déjà **typé** (`{"type": "chunk"|"error"|"done"}`)
  pour que de nouveaux types d'événements n'obligent aucun client à changer.
- **Une contrainte que seul le réseau impose :** le client HTTP doit traduire ses
  propres pannes (connexion refusée, 401, timeout) en **un** message affichable.
  L'invariant « tout chemin livre exactement un chunk non vide » se réaffirme donc
  à chaque couche — une promesse faite de l'autre côté d'une couture ne se présume pas.
- **Demain (niveau 2, front React).** Rien de nouveau à construire côté agent :
  le React consommera **le même** `POST /chat` en SSE, avec `fetch` +
  `ReadableStream`. Voir [`roadmap-frontend.md`](roadmap-frontend.md) §B2.

---

> 📌 À retenir : le streaming est un **contrat respecté à chaque couche**, pas un
> réglage unique. L'agent le **produit** (forme native de `stream_reply`), l'API
> le **transporte** (SSE), le front l'**affiche** (et lui seul décide de la
> cadence). Le typewriter/smoothing est un détail de **rendu**, jamais de cerveau.
>
> 🛑 Et la nuance qui prime sur tout le reste : **on ne streame que ce qu'on a le
> droit de montrer**. Un contrôle qui porte sur la réponse **entière** (notre
> garde de sortie) rend le streaming token par token **impossible**, pas
> seulement difficile. Le contrat « générateur async » survit ; la **taille des
> chunks**, elle, est dictée par la sécurité.
