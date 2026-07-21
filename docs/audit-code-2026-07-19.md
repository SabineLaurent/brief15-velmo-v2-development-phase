# Audit de code — agent de support agnostique

**Objet :** revue du code à la recherche d'anomalies, dysfonctionnements, incohérences et code mort.
**Date :** 2026-07-19
**Périmètre :** `packages/support-agent/src` (tous modules), `packages/frontend/src`, tests, `config.py`, `Makefile`, `.env.example`, `pyproject.toml`.
**Méthode :** lecture intégrale du code + `ruff check` (OK) + tests unitaires sans réseau (guardrails + robustesse : 27/27 OK). **Aucune modification appliquée.**

---

## Synthèse

Le code est propre et cohérent avec ses conventions (agnosticisme LLM bien tenu, ports/adaptateurs partout, type hints, docstrings pédagogiques). **Pas de code mort significatif** : les exports sont tous consommés. Les problèmes trouvés sont surtout **deux incohérences comportementales réelles sur la couture `api.py`**, plus des points mineurs de configuration et de qualité de test.

**Priorité de traitement suggérée :** A1 (sécurité, chemin client réel) → A2 (UX cassée) → I1 → I2 → Q1/Q2/Q3.

---

## 🔴 Anomalies importantes

### A1 — Le guard de sortie ne protège PAS le chemin streamé (sécurité)

**Fichiers :** `api.py:120-134`, `graph/nodes.py:319-345` (`make_guard_output`), `guardrails/output_guard.py`

`stream_reply` diffuse les tokens des nœuds `answer`/`model` **en direct** vers le client (`loop.call_soon_threadsafe(queue.put_nowait, text)`). Le nœud `guard_output` — qui rédige les PII/secrets et remplace une réponse qui fuit le system prompt — ne s'exécute **qu'ensuite**, et ne modifie que l'état LangGraph. **Le client a déjà reçu les tokens bruts.**

Conséquence : sur le **seul chemin client réel** (Chainlit → `stream_reply`), le guard de sortie est cosmétique. Une PII/un secret/une fuite de prompt part quand même. Le guard ne « mord » que sur les chemins **non streamés** : le CLI `agent.py` (via `invoke`) et l'éval. La docstring d'`output_guard.py` affirme pourtant *« last check before a reply reaches the customer »* — faux en pratique. Ce n'est documenté nulle part (vérifié dans `docs/streaming.md`).

**Correctifs possibles :**
- soit **bufferiser** la réponse du nœud face-client et ne la libérer qu'après `guard_output` (on perd le streaming token-par-token pour ces nœuds) ;
- soit appliquer un **guard incrémental dans la couture** (`_pump`) avant `queue.put_nowait` — au moins la rédaction PII/secret déterministe peut se faire sur le flux ;
- a minima, **corriger la docstring** pour ne pas prétendre une garantie que le chemin streamé n'offre pas, et tracer la limite dans `docs/streaming.md`.

### A2 — L'escalade est silencieusement cassée via la couture API

**Fichiers :** `api.py:112-144`, `graph/nodes.py:283-313` (`escalate`)

Si le router choisit `escalate`, le nœud appelle `interrupt()` et met le graphe **en pause**. Dans `stream_reply` :

1. aucun token face-client n'est streamé → `streamed = False` ;
2. le fallback lit l'état final, dont le **dernier message est le `HumanMessage`** du client (le nœud `escalate` n'a pas encore rendu son `AIMessage`) → la condition `isinstance(last, AIMessage)` échoue → **rien n'est yieldé** (UI vide) ;
3. le payload `__interrupt__` (la demande à montrer à l'opérateur humain) est **ignoré**, et le graphe reste en pause sans mécanisme de reprise côté couture.

La docstring d'`api.py:28-32` prétend pourtant livrer *« a human escalation reply … in one shot »*. Le CLI (`agent.py:90-100`) gère bien l'interrupt ; la couture non. Un client qui tape « je veux un humain » dans Chainlit obtient une bulle vide.

**Correctif :** gérer `__interrupt__` explicitement dans `stream_reply` (au minimum yielder un message d'attente « un conseiller va vous répondre » et exposer/persister le payload), ou router l'escalade hors du stream. À défaut, le signaler comme trou assumé de la phase courante (comme `user_id` simulé).

---

## 🟡 Incohérences (mineures)

### I1 — Défaut `sqlite_path` incohérent et pointant vers un dossier temporaire

**Fichier :** `config.py:83`

```python
sqlite_path: str = "./TEMP/database/memory/agent_state.sqlite3"
```

`.env.example` documente `SQLITE_PATH=./data/agent_state.sqlite3`, et le `CLAUDE.md` du front référence aussi `./data/...`. Le défaut pointe vers `TEMP/` (qui vient d'être **gitignoré** — donc un `Settings()` sans `.env`, ou un test, écrit dans un dossier volatil). Ressemble à un reste de debug.

**✅ Résolu (2026-07-21).** Trois chemins coexistaient : `.env.example` → `./data/...`, `.env` local → `./TEMP/database/agent_state.db`, défaut du code → `./TEMP/database/memory/...` (un sous-dossier `memory/` qui n'a jamais existé). **Décision : tout aligner** — et non sur `data/`, comme suggéré initialement ici. Raison : `data/` contient du **contenu source versionné** (la FAQ) ; y loger une base de conversations clients, c'est risquer de la commiter.

**🔄 Amendé le même jour.** L'alignement s'est d'abord fait sur `./TEMP/database/agent_state.db`, puis `TEMP/` a été **remplacé par `database/`** : le dossier ne contenait rien de temporaire, il contenait l'**état durable** de l'agent — un nom qui ment est une dette en soi. Dans la foulée, le fichier unique a été **scindé par horizon de mémoire**, pour que chacun s'inspecte et se vide isolément en dev :

```
database/working_memory/checkpoints.db   WORKING_MEMORY_DB_PATH   (thread_id)
database/agent_memory/memories.db        AGENT_MEMORY_DB_PATH     (user_id)
```

Le réglage `SQLITE_PATH` **n'existe plus** (remplacé par les deux ci-dessus). La séparation est un confort de dev, **pas** une frontière d'architecture : en prod, les deux retournent dans une seule base Postgres. Carte complète : [`database/README.md`](../database/README.md). Alignés : `config.py`, `memory/{short_term,long_term,sqlite_conn}.py`, `.env.example`, `.gitignore`, `CLAUDE.md` racine, `packages/frontend/{CLAUDE,README}.md`, `docs/glossaire.md`.

### I2 — Providers annoncés mais paquets d'intégration non installés

**Fichiers :** `llm/factory.py:19-25`, `.env.example`, `packages/support-agent/pyproject.toml`

`groq`, `google_genai`, `azure_ai` sont proposés dans `_PROVIDER_ALIASES` et `.env.example`, mais `langchain-groq` / `langchain-google-genai` / `langchain-azure-ai` ne sont pas dans les dépendances (seuls `mistralai` et `openai` le sont). Choisir un de ces providers lève un `ImportError` brut au runtime. C'est semi-assumé (commentaire « ajoutées au fil des phases »), mais l'erreur n'est pas guidée. → soit `uv add` à la demande documenté, soit envelopper l'`ImportError` de `init_chat_model` avec un message actionnable (« installe langchain-groq »).

---

## 🟢 Qualité / robustesse (très mineur)

### Q1 — Évaluateur `honest_refusal` trop laxiste

**Fichier :** `eval/evaluators.py:57-71`

La liste de signaux inclut `"support"` et `"contact"` — mots quasi omniprésents pour un agent *de support*. L'évaluateur passe donc presque toujours, affaiblissant le garde-fou de non-régression censé détecter une hallucination confiante. → retirer les signaux trop génériques.

### Q2 — Les évaluateurs supposent `content: str`

**Fichiers :** `eval/evaluators.py` (`.lower()`, `in`), `eval/run.py:75-81`

Certains providers renvoient `content` comme **liste de blocs**. `route_matches`/`cites_source`/`mentions_order` et l'extraction `answer` feraient alors un `AttributeError`. Non déclenché sur Mistral/OpenAI texte, mais fragile pour l'agnosticisme revendiqué. → normaliser `content` en `str` avant comparaison.

### Q3 — Dossiers résiduels à la racine

`TEMP/` (fichiers SQLite runtime `agent_state.db*`) et un dossier `database/` vide traînent. `TEMP/` est désormais gitignoré (bien) ; ni l'un ni l'autre n'est suivi par git (inoffensif), mais à nettoyer pour l'hygiène du repo (lié à I1).

---

## Points positifs confirmés

- Agnosticisme LLM **respecté** : aucun `ChatXxx(...)` en dur hors `llm/factory.py` (vérifié).
- Isolation `user_id` via runtime context (jamais depuis le LLM) : cohérente dans les tools mémoire **et** actions.
- Fallbacks composés « au leaf » (après `bind_tools`/`with_structured_output`) : correct, avec test unitaire.
- Résolution des chevauchements PII (carte vs téléphone) : correcte, couverte par test.
- Pas de code mort détecté.

---

## Récapitulatif

| Réf. | Sévérité | Objet | Fichier(s) principal(aux) |
|------|----------|-------|---------------------------|
| A1 | 🔴 Élevée (sécurité) | Guard de sortie contourné sur le flux streamé | `api.py`, `graph/nodes.py`, `guardrails/output_guard.py` |
| A2 | 🔴 Moyenne (dysfonctionnement) | Escalade silencieusement cassée via la couture | `api.py`, `graph/nodes.py` |
| I1 | 🟡 Faible | Défaut `sqlite_path` incohérent (`TEMP/`) | `config.py` |
| I2 | 🟡 Faible | Providers annoncés sans paquet d'intégration | `llm/factory.py`, `pyproject.toml` |
| Q1 | 🟢 Très faible | Évaluateur `honest_refusal` trop laxiste | `eval/evaluators.py` |
| Q2 | 🟢 Très faible | Évaluateurs supposent `content: str` | `eval/evaluators.py`, `eval/run.py` |
| Q3 | 🟢 Très faible | Dossiers résiduels `TEMP/` et `database/` | (racine) |
