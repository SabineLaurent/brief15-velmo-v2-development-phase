# 🗃️ Le prompt caching — alléger chaque *hop*

> Note de référence. Le prompt caching est listé comme **levier n°2** dans
> [`latence.md`](latence.md) ; ce doc-ci en explique le **POURQUOI / COMMENT** : le
> mécanisme, pourquoi un agent de support en est un bon terrain, les autres use
> cases, les différences de providers, et surtout **ce qui est vrai chez nous**.
> À lire avec [`latence.md`](latence.md) (d'où vient le TTFT) et le
> [`glossaire.md`](glossaire.md) (`hop`, `token`).

## 1. Le problème que ça résout

On a **mesuré** (voir [`latence.md`](latence.md)) que le silence avant la réponse
vient de **hops LLM séquentiels**, et que le coût d'un hop est surtout du
**round-trip réseau + traitement du prompt** (pas la taille du modèle). Le prompt
caching attaque **la deuxième moitié** : le *traitement du prompt*.

Il ne **supprime** aucun hop (ça, c'est un autre levier) — il rend **chaque hop
moins cher** en temps et en tokens.

## 2. Le mécanisme, en une idée

Quand un provider traite un prompt, il calcule un état interne (le *KV cache*) pour
chaque token. Le prompt caching **garde cet état pour le *préfixe*** d'un prompt
déjà vu récemment. Au prochain appel, si le **début du prompt est identique**, il
**saute le re-calcul** de ces tokens.

Trois conditions pour un *hit* (cache touché) :

- **identique au token près** sur le préfixe (un seul caractère qui change plus
  haut = cache manqué) ;
- **assez long** (OpenAI : > 1024 tokens) ;
- **récent** — le cache expire après quelques minutes d'inactivité.

## 3. La règle de design qui en découle

> **Le stable devant, le variable derrière.**

- **Préfixe** (cachable) : system prompt, schémas d'outils, exemples few-shot,
  gros contexte figé.
- **Suffixe** (jamais caché) : la question précise du client, le fil de
  conversation frais.

⚠️ **L'anti-pattern** : interpoler une donnée variable **en tête** de prompt (un
horodatage, un `user_id`, un compteur…) → ça casse le préfixe → **0 hit**. Toute
la variabilité doit vivre **à la fin**.

**Chez nous, c'est déjà bien structuré** : chaque nœud construit
`[SystemMessage(...), *state["messages"]]` — le prompt stable est **en tête**, la
conversation variable **derrière**. On est *éligible* par construction.

## 4. Dans notre agent de support : bon terrain, deux nuances

Le support coche presque toutes les cases du caching :

- **Gros préfixe stable** : le system prompt (persona, règles, ton) + les schémas
  d'outils (`search_faq`, `get_order_status`, `create_ticket`…) sont **identiques
  pour tous les clients et tous les tours**.
- **Volume + répétition** : des milliers de conversations avec le *même* setup →
  taux de *hit* élevé → gros gain **agrégé**.
- **Multi-tours + boucle ReAct** : chaque hop re-envoie le prompt qui grossit ;
  cacher le préfixe coupe le coût *par hop* — pile ce qu'on a mesuré.

**Les deux nuances honnêtes** (et pourquoi on **vérifie avant de coder**) :

1. **Le contexte FAQ récupéré (RAG) n'est PAS cachable** : il **change à chaque
   question**, donc il vit dans le *suffixe*. Ce qui se cache = système + outils,
   **pas** les chunks retrouvés.
2. **En démo à faible trafic, le cache est souvent froid** (personne n'a
   « réchauffé » le préfixe dans les dernières minutes) → gain peu visible. Le
   prompt caching est surtout un levier **à l'échelle prod**.

## 5. Dans d'autres use cases d'agent

Règle simple : **gros préfixe stable + réutilisé souvent = gros gain.**

| Terrain | Gain | Pourquoi |
|---|---|---|
| **Agent de code** | 🟢 énorme | System + defs d'outils + fichiers du repo re-envoyés à chaque étape → préfixe massif, beaucoup d'itérations |
| **Q&A sur un gros document figé** | 🟢 énorme | On cache le doc **une fois**, on pose N questions dessus (doc = préfixe, questions = suffixe) |
| **Classification / extraction few-shot** | 🟢 fort | Les exemples (souvent longs) sont stables → cachés |
| **Chatbot persona fixe, fort QPS** | 🟢 fort | Même en-tête pour tout le monde, haut volume |
| **Appel one-shot court** (< seuil) | ⚪ nul | Rien d'assez long à cacher |
| **Prompt dynamique en tête** | 🔴 anti-pattern | Variable devant → casse le préfixe → 0 hit |
| **Trafic rare / espacé** | ⚪ faible | Cache expiré entre deux appels → froid à chaque fois |

## 6. Différences de providers (une ligne chacune)

- **OpenAI / Azure** *(notre cas, via `openai_compatible`)* : **automatique**, basé
  préfixe, > 1024 tokens, **zéro code** ; input caché ~2× moins cher, TTL court.
- **Anthropic** : **explicite** — on pose des *breakpoints* `cache_control` sur ce
  qu'on veut cacher ; lecture jusqu'à ~90 % moins chère, petit surcoût d'écriture,
  TTL 5 min (option 1 h).
- **Google Gemini** : *context caching* explicite (on crée un handle de contexte
  caché), idéal pour un gros contexte fixe.

> 💡 L'agnosticisme reste : notre code met déjà le stable en tête (§3). Selon le
> provider derrière la [factory](glossaire.md), le caching est soit **gratuit et
> automatique** (OpenAI/Azure), soit à **activer explicitement** — sans changer les
> nœuds.

## 7. Ce qu'on fait concrètement chez nous — *mesuré (2026-07)*

Le caching étant **automatique** sur notre chemin Azure/OpenAI et notre prompt déjà
bien structuré, la bonne démarche n'était **pas** d'écrire du caching spéculatif,
mais de **mesurer s'il mord**. C'est fait : le harnais [`make latency`](latence.md)
remonte désormais `cache_read` **par nœud**, et le verdict est net.

**Un piège traversé en chemin — le streaming masque l'usage.** OpenAI n'émet pas
les compteurs de tokens sur une réponse **streamée** sauf si on l'active. Comme
*tout* le graphe streame (via LangGraph), `usage_metadata` — donc `cache_read` —
était **invisible** (`None`). Corrigé dans la [factory](glossaire.md) : la branche
`openai_compatible` passe `stream_usage=True` (elle est toujours OpenAI-wire, donc
zéro entorse à l'agnosticisme). L'usage/coût remonte maintenant partout, pas juste
dans le harnais.

**Ce que la mesure montre** (6 runs **dans un seul process**, question « délais de
livraison », `thread_id` neuf à chaque run → seul le **préfixe** système+outils est
stable) :

| Nœud (appel LLM) | input tokens | `cache_read` | Pourquoi |
|---|---:|---:|---|
| `model` — **1er passage** (décision d'outil) | 917 | **0** | **sous le seuil** Azure de 1024 tokens → jamais caché |
| `model` — **2e passage** (la réponse) | 1609 | **0 → 1152** | au-dessus du seuil → caché ; **froid run 1**, puis **chaud et stable runs 2‑6** |

Effet sur la latence, froid vs chaud :

| | 2e passage `model` | TTFT |
|---|---:|---:|
| **run 1 (froid)** | 1.93 s · `0/1609` | 3.65 s |
| **runs 2‑6 (chaud)** | 1.3–1.7 s · `1152/1609` | médiane **3.41 s** (min 3.33 / max 3.50) |

**Quatre enseignements :**

1. **Le cache mord — sur la cible qui compte.** Le 2e passage (la réponse : long
   system prompt + 6 schémas d'outils + chunks FAQ) est **le** gros bloc de TTFT
   (§[latence](latence.md)), et c'est lui qui se cache : **1152/1609 ≈ 72 %** des
   tokens servis par le cache une fois chaud.
2. **Le plafond à ~72 % confirme la règle §3.** Seul le **préfixe stable**
   (système+outils) est caché ; les **chunks FAQ** et la question fraîche (le
   suffixe variable) ne le sont **jamais** — pile « stable devant, variable
   derrière », et pile la nuance §4.1 (le RAG n'est pas cachable).
3. **Froid à faible trafic (§4.2), confirmé — et le protocole compte.** Dans **un
   seul process**, la connexion est réutilisée → on retape le **même réplica** chaud
   → froid run 1, puis HIT **stable** runs 2‑6. Au contraire, relancer `make latency`
   en **process séparés** rejoue la **loterie des réplicas** (HIT/cold alternés) : le
   cache Azure est **best-effort, par réplica, à TTL court**. Pour mesurer, toujours
   `--runs N` en **un** process.
4. **Le gain est réel mais petit, et noyé dans le bruit réseau.** Chaud, le 2e
   passage passe de ~1.9 s à ~1.5 s (≈ −0.3 s) et le TTFT médian de 3.65 → **3.41 s**
   (~−0.24 s, ~7 %) — dans l'épaisseur du round-trip. En démo on ne le « voit » pas ;
   c'est un levier **d'échelle prod**.

**Ce qu'on ne fait PAS :** pas de `prompt_cache_key` par nœud, pas de caching
explicite. Le cache automatique suffit une fois chaud ; l'ajouter n'aiderait qu'à
épingler le routage réplica sous fort trafic (hors sujet démo). Le gain TTFT chaud
est réel mais **modeste et bruité** (~0.3 s sur le 2e passage) — cohérent, à la
baisse, avec l'estimation « ~0.5–1 s » de [`latence.md`](latence.md). Le vrai gros
levier restant est ailleurs : **réduire le nombre de hops**.

**Statut :** ✅ instrumenté et vérifié — `make latency` affiche `cache … read/total`
par nœud + un verdict `cache HIT / cold`. Rien de plus à coder côté caching.

---

> 📌 **À retenir :** le prompt caching est autant un **réflexe d'architecture de
> prompt** (*stable devant, variable derrière*) qu'une feature provider. Le support
> en profite bien **en prod** (préfixe système/outils réutilisé massivement), moins
> en **démo** (froid). Et il **allège** chaque hop — il n'en **supprime** aucun.
