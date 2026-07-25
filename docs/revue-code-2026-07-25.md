# Revue de code — branche `velmo-2.0`

**Objet :** revue de code **générale** du dépôt (correction, cohérence, dette),
complémentaire de la revue de sécurité du même jour — qui, elle, ne cherchait que
des vulnérabilités exploitables.
**Date :** 2026-07-25
**Commit testé :** `6dc258f` — *feat(compose): pin the container to authenticated mode, whatever .env says*
**Branche :** `velmo-2.0` (arbre propre au moment de la revue)
**Périmètre :** tout le code applicatif des deux packages (`support_agent`, `client_chainlit`),
les tests, la conteneurisation (`compose.yaml`, les deux `Dockerfile`, `.dockerignore`),
le `Makefile`, les manifestes `pyproject.toml` et le rangement `data/` · `database/`.
**Méthode :** lecture intégrale des 49 fichiers Python, `make check` (78 tests ✅,
`ruff` ✅), puis **vérification expérimentale de chaque hypothèse** — aucun finding
ci-dessous n'est déduit de la lecture seule.
**Aucune modification appliquée.**

> 📌 À lire avec [`revue-securite-2026-07-25.md`](revue-securite-2026-07-25.md)
> (identité non prouvée, V1/V2) et [`audit-code-2026-07-19.md`](audit-code-2026-07-19.md)
> (l'audit dont C1 ci-dessous est le reliquat).

---

## Synthèse

La qualité de fond est élevée et il faut le dire avant le reste : l'agnosticisme LLM
tient réellement (aucune instanciation de provider hors de `llm/factory.py`), la couture
`stream_reply` est testée **comme un contrat** et non comme une implémentation, et
`test_server.py` neutralise le `.env` de la développeuse par les kwargs du constructeur
— le genre de détail qui distingue une suite qui protège d'une suite qui rassure.

**Trois défauts de correction** sortent de la revue, tous sur des chemins **normaux**
(pas des cas limites), et aucun n'est couvert par les trous déjà assumés :

| # | Sévérité | Le problème en une ligne |
|---|---|---|
| **C1** | 🔴 haute | Une escalade **condamne le fil définitivement** via HTTP : plus aucun message ne sera jamais traité. |
| **C2** | 🔴 haute | Le garde de sortie **caviarde les adresses de contact de la boutique**, qui sont la réponse attendue de deux fiches FAQ. |
| **C3** | 🟠 moyenne | Une clé d'API **non-ASCII** rend `500` au lieu de `401`. |

Le point commun de C1 et C2 : dans les deux cas, un mécanisme **correct dans son
principe** (la pause human-in-the-loop, la rédaction de PII) est appliqué **sans
connaître son propre contexte** — le graphe ne sait pas qu'il doit pouvoir repartir,
le garde ne sait pas quelles adresses sont les siennes.

---

## Ce qui a été vérifié et jugé SAIN

Écrit noir sur blanc, pour la même raison que dans la revue de sécurité : une revue qui
ne liste que ce qui cloche laisse croire que le reste n'a pas été regardé.

| Sujet | Verdict | Pourquoi |
|---|---|---|
| Agnosticisme LLM (invariant n°1) | ✅ | `grep` exhaustif : **aucune** instanciation de provider hors `llm/factory.py`. La règle n'est pas qu'un commentaire. |
| Découplage du client (étape 4) | ✅ | Ni `support_agent`, ni `langgraph`, ni `langchain` dans `packages/client`. La dépendance est absente du `pyproject.toml`, donc de la fermeture installée par son image. |
| Contrat de la couture | ✅ | `test_api_seam.py` paramètre les **6** chemins de sortie et assied l'invariant « exactement un chunk non vide ». La règle est exécutable, plus seulement écrite. |
| Isolation `thread_id` / `user_id` | ✅ | Assertée jusque dans les kwargs passés au graphe (`test_api_seam.py:135`). Une régression silencieuse serait attrapée. |
| `numpy` en dépendance directe | ✅ | **Vérifié, et contre-intuitif** : `langgraph.store.memory` et `langchain_core.vectorstores.in_memory` l'importent **sans le déclarer**. Le déclarer nous-mêmes est donc correct, pas superflu. |
| Idempotence de `create_ticket` | ✅ | Id dérivé du contenu, séparateurs `\x00` — deux requêtes distinctes ne peuvent pas se télescoper par concaténation. |
| Ordre `__interrupt__` avant `messages` | ✅ | `api.py:96`. L'inversion rejouerait la réponse du tour précédent ; le test `test_interrupt_wins_over_a_stale_reply` verrouille l'ordre. |
| Gestion d'erreur réseau du client | ✅ | `httpx.SSEError` hérite de `httpx.TransportError`, donc le `except httpx.HTTPError` couvre bien le cas « 200 mais mauvais content-type ». |
| Connexion SQLite partagée | ✅ | `check_same_thread=False` + `isolation_level=None` : les deux réglages sont justifiés et exacts pour les backends LangGraph. |
| `make check` | ✅ | 78 tests passent, `ruff` ne signale rien, en 30 s et **sans réseau**. |

---

## 🔴 C1 — Une escalade condamne le fil définitivement, via HTTP

**Sévérité :** haute · **Catégorie :** correction fonctionnelle · **Confiance :** 10/10
**Fichiers :** `packages/support-agent/src/support_agent/api.py:96` ·
`graph/nodes.py:293-323` · `server.py` (absence de route de reprise) ·
à comparer avec `agent.py:90-100`, qui sait le faire

### Le fait

L'audit A2 prescrivait **deux** choses : yielder un message d'attente **et**
« exposer/persister le payload » de l'`interrupt()`. Seule la première a été faite.
Il n'existe **aucun chemin de reprise** au-delà de la couture : ni `api.py` ni
`server.py` ne savent envoyer un `Command(resume=…)`.

Ce n'est pas seulement « l'opérateur ne peut pas répondre ». C'est bien pire, et c'est
la partie qui n'était pas connue.

### Vérification

Graphe minimal reproduisant le câblage (`router → escalate → interrupt`), checkpointer
en mémoire, **même `thread_id`** :

```
turn1  « je veux un humain »            → __interrupt__ : True
turn2  « quels sont vos délais ? »      → __interrupt__ : True   ← messages[-1] == le message humain
turn3  « bonjour ? »                    → __interrupt__ : True
```

LangGraph reprend la **tâche pendante** avant toute autre chose : le nouveau message est
bien ajouté à l'état, mais `router` **n'est jamais réévalué**. Le nœud `escalate`
re-exécute, rappelle `interrupt()` sans valeur de reprise, et repart en pause.

### Ce que vit le client

Il tape « je voudrais parler à quelqu'un », reçoit `ESCALATION_PENDING_MESSAGE`
(« un conseiller prend le relais »), puis **toutes** ses questions suivantes — y compris
« finalement laissez tomber, quels sont vos délais ? » — reçoivent exactement la même
phrase, indéfiniment. Le fil est mort, sans qu'aucun log ne signale une erreur : du point
de vue du serveur, tout s'est bien passé.

### Pourquoi ça compte plus que le trou `user_id`

`user_id` est un trou **assumé, documenté, planifié** (étape 5). C1 ne l'est pas : c'est
une branche que **le router choisit tout seul**, sur une phrase de support parfaitement
banale, et la roadmap coche la phase 7 comme « pause + reprise vérifiées » — ce qui est
vrai **en CLI seulement**. Le déploiement a déplacé le chemin réel du client hors du CLI
sans emporter la reprise avec lui.

### Correctif

Deux directions, à trancher :

- **Rendre la reprise possible** : une route `POST /chat/{thread_id}/resume` et
  l'exposition du payload d'`interrupt()` (il est déjà structuré pour ça :
  `reason` / `user_id` / `customer_message`). C'est ce que l'audit demandait.
- **Ou ne pas mettre le graphe en pause du tout** tant qu'aucun opérateur n'est branché :
  `escalate` ouvre un ticket via le backend et **termine le tour** normalement. Le fil
  reste vivant, le client peut continuer à parler, et l'escalade devient asynchrone —
  ce qui est d'ailleurs le comportement d'un vrai service de support.

⚠️ Dans les deux cas, ajouter un test « deux tours sur le même `thread_id` après une
escalade » : le trou actuel est précisément celui qu'aucun test à un seul tour ne voit.

---

## 🔴 C2 — Le garde de sortie caviarde les adresses de la boutique

**Sévérité :** haute · **Catégorie :** correction fonctionnelle (qualité de réponse) · **Confiance :** 10/10
**Fichiers :** `guardrails/output_guard.py:90` (`apply_pii_policy` sans politique dédiée)
· `guardrails/pii.py:60-65` (`DEFAULT_POLICY`, `email: redact`) ·
`data/kb-velmo/contact-pro.md`, `data/kb-velmo/retractation-rgpd.md`

### Le fait

`DEFAULT_POLICY` redige l'entité `email`, et le garde de **sortie** réutilise cette
politique telle quelle. Or la FAQ **publie deux adresses de la boutique**, qui sont la
réponse attendue de deux fiches entières.

### Vérification

```python
>>> g = build_output_guard([SUPPORT_SYSTEM_PROMPT])
>>> g.check("Pour exercer vos droits RGPD, écrivez à privacy@velmo.example "
...         "(source : retractation-rgpd.md).").sanitized_text
'Pour exercer vos droits RGPD, écrivez à [REDACTED_EMAIL] (source : retractation-rgpd.md).'
```

Les deux adresses concernées, présentes dans la base de connaissance versionnée :

| Adresse | Fiche FAQ | Sujet |
|---|---|---|
| `pro@velmo.example` | `contact-pro.md` | ouverture d'un compte revendeur |
| `privacy@velmo.example` | `retractation-rgpd.md` | exercice des droits RGPD |

### Le vrai défaut de conception

La politique ne distingue pas deux choses qui n'ont rien à voir :

- **l'e-mail du client**, qu'on ne réémet pas (défense en profondeur, légitime) ;
- **l'e-mail de la boutique**, qui **EST** l'information demandée.

Le garde applique une règle correcte sans connaître **son propre domaine**. Joli cas
d'école, d'ailleurs : c'est la limite structurelle du déterministe-d'abord, et elle ne
se voit pas en test unitaire parce qu'un test unitaire choisit ses exemples.

### Angle mort de la suite de tests

`test_guardrails.py:150` asserte le comportement actuel sur `client@example.com` — donc
la suite **verrouille le bug** au lieu de l'attraper. Le contre-exemple manquant est une
adresse *de la boutique*, pas une adresse *du client*.

### Correctif

- Une **allowlist de domaines propriétaires** dans `OutputGuard` (alimentée par les
  adresses présentes dans la KB, ou par une variable de config), appliquée avant la
  rédaction ; ou
- politique **asymétrique** : `email → redact` en entrée (on ne veut pas la stocker),
  `email → allow` en sortie, la fuite d'e-mail sortante étant déjà couverte par le fait
  que le modèle ne répond que depuis la FAQ.

Et dans les deux cas : un test qui vérifie qu'une réponse citant `contact-pro.md`
**conserve** `pro@velmo.example`.

---

## 🟠 C3 — Une clé d'API non-ASCII rend 500 au lieu de 401

**Sévérité :** moyenne · **Catégorie :** robustesse du chemin d'authentification · **Confiance :** 10/10
**Fichier :** `server.py:133` (`secrets.compare_digest`)

### Le fait

`secrets.compare_digest` **refuse** les `str` contenant des caractères non-ASCII :

```
TypeError: comparing strings with non-ASCII characters is not supported
```

Starlette décode les valeurs d'en-tête en **latin-1**, donc un octet ≥ 0x80 dans
`X-API-Key` produit un `str` non-ASCII, qui fait lever `compare_digest` **avant** toute
comparaison. FastAPI transforme ça en `500`.

### Vérification

Avec `TestClient(server.app, raise_server_exceptions=False)`, en-tête envoyé en octets
bruts (`httpx` refuse la forme `str`, ce qui explique que le cas n'ait jamais été vu) :

```
X-API-Key: b"cl\xe9"   →  500 Internal Server Error      (attendu : 401)
```

### Portée honnête

Ce n'est **pas** un contournement d'authentification : la requête est rejetée, rien ne
passe. L'impact est plus modeste — un appelant non authentifié provoque une exception et
un stack trace dans les logs à volonté, et un client légitime dont la clé contiendrait un
caractère accentué recevrait un `500` illisible au lieu du `401` qui lui dirait quoi
corriger. La revue de sécurité note ce mécanisme pour `X-User-Id` **en sortie** ; ici il
est sur le chemin d'auth **en entrée**, ce qui n'avait pas été relevé.

### Correctif

Écarter le cas avant la comparaison — `if not x_api_key or not x_api_key.isascii() or
not secrets.compare_digest(...)` — ou comparer des `bytes` (`.encode("latin-1")` des deux
côtés), ce qui supprime la classe entière de problème.

---

## Points mineurs

Rangés par intérêt décroissant. Aucun n'est urgent ; tous sont peu coûteux.

### M1 — `_MIN_LEAK_LEN` ment sur son seuil réel

`guardrails/output_guard.py:36,74`

Le pas de fenêtre vaut `_MIN_LEAK_LEN // 2 = 30`, donc une fuite verbatim de 60
caractères tombant sur un offset défavorable **passe au travers**. Mesuré sur
`SUPPORT_SYSTEM_PROMPT`, fuite prise à l'offset 45 :

| Longueur de la fuite | Détection |
|---|---|
| 60 caractères | ❌ MISSED |
| 75 caractères | ✅ DETECTED |
| 90 caractères | ✅ DETECTED |

Le seuil **garanti** est donc ~90, pas 60 — alors que le commentaire annonce
« minimum length of a verbatim overlap to call it a leak ». Les prompts font quelques
centaines de caractères : passer le pas à `1` coûte un temps négligeable et rend le
commentaire vrai. Sinon, corriger le commentaire.

### M2 — `RateLimiter._hits` ne se purge jamais

`guardrails/tool_guard.py:42-56`

Une `deque` par `user_id`, conservée à vie dans un process long. Les timestamps périmés
sont bien retirés, **la clé jamais**. La croissance est modeste, mais le `user_id` est
justement la valeur **non prouvée** que n'importe quel appelant choisit librement (V1 de
la revue de sécurité) : les deux défauts se composent. Une éviction des clés à deque
vide, ou un `TTLCache`, suffit.

### M3 — `actions/sql/schema.py` n'est importé par personne

`packages/support-agent/src/support_agent/actions/sql/schema.py` (219 lignes)

Vérifié par `grep` sur tout le dépôt : aucun import. Trois conséquences :

- le module tire **`sqlalchemy`** dans les dépendances **runtime** du package, donc dans
  l'image de production, pour du code que rien n'exécute ;
- le dossier `actions/sql/` est le **seul** sous-paquet sans `__init__.py` ;
- même statut pour `data/db-shop-velmo/db.py` et `sampledata.py`, alors que `CLAUDE.md`
  décrit `data/` comme « SOURCE versionnée, écrite par un humain : la FAQ » — et que
  `.dockerignore` exclut ce dossier du contexte de build.

Si c'est l'amorce du chantier Velmo / Phase 11 (doublure du système marchand), une ligne
dans `TODO_priorities.md` suffit à lever le doute. Sinon, c'est de la dette à retirer.

### M4 — `database/README.md` annonce un dossier qui s'appelle autrement

Le README parle de `database/business/` *(à venir)* ; le dossier réellement présent —
avec son `.gitkeep` — s'appelle `database/shop/`.

### M5 — Une arête conditionnelle sans `path_map`

`graph/builder.py:126`

`add_conditional_edges("guard_input", guard_route)` est la seule des trois arêtes
conditionnelles sans dictionnaire de destinations, alors que les deux autres en ont un.
Sans lui, le graphe dessiné (LangSmith, `get_graph().draw_*`) montre des arêtes vers
**tous** les nœuds. Purement cosmétique, mais c'est le diagramme qui sert à expliquer
l'agent.

---

## Ce qui a été écarté (et pourquoi)

Pour que la prochaine revue ne re-signale pas les mêmes choses.

- **`numpy` en dépendance directe apparemment inutilisée.** Écarté après vérification :
  `langgraph.store.memory` et `langchain_core.vectorstores.in_memory` l'importent
  **sans le déclarer**. Le retirer casserait le backend `memory` et l'index FAQ. La
  déclaration explicite est **correcte** — ne pas « nettoyer » ça.
- **Connexion SQLite unique partagée entre threads.** `check_same_thread=False` sur une
  connexion servie depuis le pool de threads d'`asyncio.to_thread` : vérifié, les
  backends SQLite de LangGraph sérialisent leurs accès par verrou. Pas de finding.
- **`InMemorySupportBackend` global mutable, tickets qui s'accumulent.** Doublure de
  démo, explicitement temporaire (`database/README.md` : « en prod, on la débranche »).
- **Absence de rate limit / timeout global sur `/chat`.** Réel, mais c'est de
  l'épuisement de ressources sur un service non encore public, et la revue de sécurité
  a déjà tranché cette catégorie comme hors périmètre.
- **`logging.basicConfig` appelé à l'import de `server.py`.** Effet de bord au niveau
  module, mais `server.py` **est** un point d'entrée applicatif, et le commentaire
  justifie déjà le choix. Assumé.
- **Identité `user_id` / `thread_id` non prouvée.** Couvert par V1/V2 de
  [`revue-securite-2026-07-25.md`](revue-securite-2026-07-25.md) — pas rejoué ici.

---

## Suite proposée

L'ordre suit le coût pour le client, pas la difficulté :

1. **C2** d'abord : c'est le moins cher (une allowlist, un test) et il dégrade
   **aujourd'hui** deux réponses de la FAQ, à chaque fois qu'elles sont demandées.
2. **C3** ensuite : trois lignes, et ça ferme une classe entière de `500`.
3. **C1** en discussion : il touche à ce que doit *devenir* l'escalade une fois l'agent
   derrière une API — donc il se décide avec l'étape 5 du déploiement, pas contre elle.
   Trancher d'abord la question de conception (reprise synchrone vs. escalade
   asynchrone par ticket), coder ensuite.
4. Les **mineurs** au fil de l'eau, M1 et M3 en priorité (l'un ment dans un commentaire,
   l'autre fait porter une dépendance à la prod).
