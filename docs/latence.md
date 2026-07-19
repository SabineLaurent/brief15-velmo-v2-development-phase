# ⏱️ La latence — notre démo vs un assistant support prod-grade 2026

> Note de référence (scope B). En reprenant le front (après B1.3), une vraie
> question a surgi : **pourquoi ~5 secondes de silence avant que l'agent ne
> réponde ?** Ce doc mesure d'où vient ce délai *chez nous*, puis le compare à ce
> que fait un assistant de support **en production**. But : savoir quels leviers
> valent le coup pour un **tuto**, et lesquels sont « prod-grade » à documenter
> comme cible (niveau 2) sans les coder maintenant.
> Complète [`streaming.md`](streaming.md) (§ « le silence avant le 1er token »),
> [`perimetre-final.md`](perimetre-final.md) (le produit fini) et
> [`architecture.md`](architecture.md) (le graphe).

## La bonne métrique : le **time-to-first-token** (TTFT)

Ce qui « fait cassé », ce n'est pas la durée **totale** d'une réponse — c'est le
**silence avant le 1er token**. Un chat qui commence à écrire en 300 ms *paraît*
rapide même s'il met 6 s à finir ; un chat qui reste muet 5 s *paraît* cassé même
s'il finit en 5.8 s. Donc on optimise le **TTFT**, pas le total. (Le streaming, lui,
ne réduit pas le calcul — il **révèle** la réponse tôt ; voir [`streaming.md`](streaming.md).)

## D'où viennent nos 5 secondes (mesuré, pas supposé)

Mesure instrumentée sur une question factuelle (« Quels sont vos délais de
livraison ? » → branche support + FAQ), provider Azure `gpt-5.6-sol`, temp 0,
`stream_mode=["updates","messages"]` pour chronométrer **par nœud** :

| Étape | Type | Durée | Part du TTFT |
|---|---|---:|---:|
| `guard_input` | règles (pas de LLM) | 0.00 s | ~0 % |
| `router` | **LLM #1** — décide la route, **non streamé** | 1.25 s | 25 % |
| `model` (1er passage) | **LLM #2** — décide d'appeler `search_faq`, **non streamé** | 1.22 s | 24 % |
| `tools` | embed de la requête (Mistral) + similarity search | 0.49 s | 10 % |
| `model` (2e passage) → 1er token | **LLM #3** — la réponse, *prompt processing* | 2.10 s | 41 % |
| **TTFT** | | **5.06 s** | **100 %** |
| streaming de la réponse | | +0.74 s | |
| **total** | | **5.80 s** | |

**Deux constats qui tombent :**

1. **Trois allers-retours LLM séquentiels**, dont **seul le dernier streame**. Les
   deux premiers (router + décision d'outil) = **~2.5 s de pré-roll pur**, avant
   même que le stream *puisse* commencer.
2. Le **dernier appel met 2.1 s à sortir son 1er token** (41 %) : gros modèle +
   long system prompt + historique + les 4 chunks FAQ réinjectés. C'est le plus
   gros bloc unique.

> Le graphe ne streame que **0.74 s sur 5.8** : l'utilisateur voit ~5 s de vide
> puis une rafale. Tout le problème est *avant* le stream.

## Poste par poste : notre démo vs la prod

| Notre poste | Ce que fait un assistant support **prod** |
|---|---|
| **Router 1.25 s** (LLM génératif) | Rarement un gros LLM. Soit un **classifieur léger** (petit modèle rapide, ou intent-classification par **embeddings** — pas de génération, ~50–150 ms), soit **pas de router séparé** : un seul agent tool-calling décide tout. |
| **Décision d'outil 1.22 s** | Souvent **fusionnée** avec la réponse (moins de hops), et/ou **retrieval spéculatif** : on lance la recherche FAQ *en parallèle* du routing pour que le contexte soit déjà prêt. |
| **Tools 0.49 s** (embed + search) | Embeddings query **cachés**, vector DB managée → retrieval **sub-100 ms** à l'échelle. Notre embed distant + `InMemoryVectorStore` reconstruit au boot = pattern démo assumé. |
| **Réponse 2.1 s TTFT** | **Prompt caching** systématique (system prompt + contexte FAQ cachés côté provider) = **levier n°1**, coupe souvent 40–70 % du TTFT. + infra **chaude** (provisioned throughput, pas de file d'attente) + prompts **lean**. |

## Les 3 différences structurelles qui font l'écart

1. **Moins de hops séquentiels.** On fait **3 allers-retours LLM** avant de
   streamer ; la prod vise **1 à 2**. Chaque hop supprimé ≈ ~1 s. C'est le levier
   architectural le plus payant — et le plus risqué (notre router « capot ouvert »
   est un choix **pédagogique** assumé, cf. [`architecture.md`](architecture.md)).
2. **Cascade de modèles (petit → gros).** Le petit modèle rapide fait le trivial
   (router, décision, questions faciles) ; le gros n'est appelé **que** pour la
   réponse finale, voire seulement quand la confiance est basse. Chez nous : des
   **rôles de modèle** dans la factory (une variable d'env, zéro code métier).
3. **Cache de réponses sémantique.** En support, les questions **se répètent
   massivement** (« délais de livraison » revient des milliers de fois). Un cache
   sémantique renvoie la réponse d'une question quasi-identique **sans appel LLM**
   (~instantané). Énorme en support, quasi inexistant ailleurs — ça court-circuite
   les cas fréquents, sans remplacer le RAG.

## Les cibles de la prod

- **TTFT < 1 s** idéalement ; la réponse « démarre » quasi tout de suite.
- **Feedback visuel dès ~100 ms** (« Recherche dans notre centre d'aide… ») : ça
  ne réduit pas le calcul, ça **cache l'attente inévitable**. Considéré aussi
  important que la latence réelle.

## Ce qui est réaliste **chez nous** (tuto) — l'ordre

Trois leviers sont à la fois « prod-grade » **et** réalistes ici :

1. **Cascade de modèles via la factory** (router + décision d'outil en modèle
   rapide, réponse en modèle fort). *Le* pattern prod, respecte l'agnosticisme,
   une variable d'env. Gain estimé **~1.5–2 s**. Point d'attention : le nœud
   `model` sert aux **deux** passages (décision *et* réponse) — pour n'accélérer
   que la décision, il faut distinguer les passages (ex. « dernier message =
   `ToolMessage` → modèle fort, sinon → modèle rapide »).
2. **Prompt caching Azure** sur le dernier appel. Levier n°1 de la prod, faible
   risque. Gain **~0.5–1 s**.
3. **Steps / feedback côté front** (une demi-[B1.5](roadmap-frontend.md)) : le
   geste « perçu instantané » — montrer *Analyse… → Recherche FAQ…* pendant que
   ça calcule.

Cumulés, on peut viser un **TTFT ~2.5–3 s** (au lieu de 5.1 s), et le *paraître
instantané* grâce aux steps.

## 🔬 Résultats mesurés (2026-07) — ce que la mesure a corrigé

Un harnais reproductible mesure désormais le TTFT + la durée **par nœud** sur le
vrai graphe : `make latency` (module `support_agent.latency`, via
`stream_mode=["updates","messages"]`). Comparaison de trois configs sur la même
question (« délais de livraison »), 3 runs chacune :

| Config | router | décision d'outil | réponse | **TTFT médian** |
|---|---|---|---|---|
| **Tout-Sol** (baseline) | Sol ~1.25 s | Sol ~1.0 s | Sol ~2.0 s | **4.07 s** |
| **Cascade complète** (router + décision sur Luna) | Luna ~0.9 s | Luna ~1.35 s | Sol ~1.75 s | **3.93 s** |
| **Router-only** (router sur Luna) — ✅ **retenu** | Luna ~0.9 s | Sol ~0.95 s | Sol ~2.0 s | **3.63 s** |

**Ce que ça apprend — contre l'estimation initiale (~1.5–2 s) :**

1. Sur cette infra Azure, **le TTFT est dominé par le coût par [hop](glossaire.md)**
   (round-trip réseau + traitement du prompt), **pas par la taille du modèle**. Un
   petit modèle rapide (Luna) n'aide que là où le prompt est **court** : le
   **routeur** (−0.35 s réel). Sur la **décision d'outil** (long prompt support + 6
   schémas d'outils), Luna est même **plus lente** (+0.35 s) → la cascade complète
   **s'annule**.
2. **Router-only** capte le gain du routeur **sans** la pénalité de la décision :
   **4.07 → 3.63 s (−0.44 s, ~11 %)**. Modeste, mais réel — et **gratuit** (Luna
   moins chère). C'est la config retenue (`LLM_FAST_MODEL` = Luna ; routeur seul,
   cf. `packages/support-agent/src/support_agent/graph/builder.py`).
3. Le doc estimait ~1.5–2 s pour la cascade : **la mesure l'a réfuté.** Leçon
   durable — *on mesure, on ne suppose pas.*

### Prompt caching — le prochain levier (vérifier avant de coder)

Le vrai coût étant le **traitement du prompt** (long system prompt + schémas
d'outils, **stables** d'un appel à l'autre), le prompt caching vise exactement ça.
Sur notre chemin (Azure OpenAI v1 / `openai_compatible`) :

- Il est **automatique** pour tout prompt > 1024 tokens sur les modèles récents —
  **zéro code**.
- Condition : le **préfixe stable** (system prompt + outils) doit être **en tête**
  du prompt → c'est **déjà** notre structure (`SystemMessage(...)` en premier).
- Donc il est **probablement déjà actif**. La bonne démarche n'est **pas** d'écrire
  du caching spéculatif, mais de **vérifier** : lire
  `response.usage_metadata.input_token_details.cache_read` (nb de tokens servis par
  le cache). S'il reste à 0, alors agir (clé `prompt_cache_key` par nœud, vérifier
  le support Azure du déploiement).

**Prochaine étape concrète :** instrumenter le harnais pour remonter `cache_read`,
confirmer si le cache mord, et ne coder que si nécessaire. Ensuite seulement, le
seul gros levier restant : **réduire le nombre de [hops](glossaire.md)** (supprimer
ou fusionner le routeur ≈ un hop de moins ≈ ~1.2 s, mais ça casse le « capot
ouvert » qu'on montre exprès).

## Ce qu'on laisse **hors scope tuto** (cible niveau 2)

À documenter comme prod-grade dans [`perimetre-final.md`](perimetre-final.md),
**pas** à coder maintenant :

- **Suppression du router** (redesign : un seul agent tool-calling) — casse le
  « capot ouvert » qu'on montre exprès. Gain ~1.25 s, risque fort.
- **Cache de réponses sémantique** — vrai composant d'infra (store + seuil de
  similarité + invalidation).
- **Infra chaude** (provisioned throughput, embeddings locaux/managés, vector DB
  persistante) — hors sujet pour une démo.

---

> 📌 À retenir : nos 5 s = **3 hops LLM séquentiels** dont un seul streame, +
> **2.1 s** de *prompt processing* sur le dernier. La prod attaque ça par **moins
> de hops**, une **cascade petit→gros modèle**, du **prompt caching** et un **cache
> sémantique**, tout en **masquant** l'attente restante (feedback < 100 ms).
> Réaliste ici, dans l'ordre : **cascade de modèles (factory) → prompt caching →
> steps front**. Le reste est une cible niveau 2, pas du code de tuto.
