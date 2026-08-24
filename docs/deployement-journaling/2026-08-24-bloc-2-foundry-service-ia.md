# 2026-08-24 — Bloc 2 : la ressource Foundry (service d'IA)

**Objet :** créer la ressource Azure qui expose un endpoint parlant l'API OpenAI, y
déployer **deux** modèles (chat + embeddings), relever endpoint et clé.
**Correspond à :** §5 du tuto `docs/2026-08-24-tuto-deploiement-azure-portail.md`
(compétences 4 et 5 du brief).
**Prérequis :** [bloc 1](2026-08-24-bloc-1-images-docker-acr.md) terminé.
**Statut :** ✅ **terminé** — les trois déploiements répondent, vérifiés depuis la machine.

---

## Pourquoi ce bloc passe avant Postgres, alors que Postgres est plus lent à créer

Parce qu'il est le **maillon inconditionnel du démarrage**.

`packages/support-agent/src/support_agent/memory/long_term.py` construit le store avec un
index vectoriel, et `server.py` fait ce travail **dans le `lifespan`** — donc *avant*
qu'uvicorn n'ouvre son port. Conséquence :

> **Sans déploiement d'embeddings joignable, la Web App ne démarre pas du tout.**

Et Azure ne le dira pas : il affichera *« Container didn't respond to HTTP pings on
port: 8000 »*, message qui décrit un symptôme et jamais la cause. Autant écarter cette
inconnue en premier, pendant qu'elle se teste en cinq secondes dans un playground.

C'est l'application de la règle d'ordre du tuto : **un suspect à la fois.**

## Ce que le code attend exactement — vérifié dans le dépôt

| Variable | Lue par | Valeur à mettre |
|---|---|---|
| `LLM_PROVIDER` | `config.py:29` | `openai_compatible` |
| `LLM_MODEL` | `config.py:30` | **le nom du DÉPLOIEMENT** chat, pas le nom du modèle |
| `LLM_INFERENCE_ENDPOINT` | `config.py:34` | l'URL de base **+ `/openai/v1`** |
| `LLM_INFERENCE_API_KEY` | `config.py:35` | Key 1 de la ressource |
| `EMBEDDINGS_PROVIDER` | `config.py:50` | `openai_compatible` |
| `EMBEDDINGS_MODEL` | `config.py:51` | le nom du **déploiement** embeddings |

⚠️ **Il n'y a pas de variable d'endpoint séparée pour les embeddings** :
`llm/embeddings.py:33` réutilise `settings.llm_inference_endpoint` et
`settings.llm_inference_api_key`. Les deux déploiements doivent donc vivre **sur la même
ressource**.

Le rail est déjà écrit : `llm/factory.py:52-61` appelle
`init_chat_model(model_provider="openai", base_url=…, api_key=…)`. **Aucune ligne de code
à changer pour passer chez Azure** — c'est le bénéfice annoncé au §5 du plan de
déploiement, et il s'encaisse ici.

---

## Étape 1 — créer la ressource (portail)

1. Portail → **`Créer une ressource`** → chercher **`Azure AI Foundry`**
   (ou `Azure OpenAI` : les deux mènent à une ressource utilisable).
2. Onglet **Général / Basics** :
   - **Abonnement** → celui de la formation
   - **Groupe de ressources** → **`slaurentRG`**
   - **Région** → **France Central** *(voir la note région ci-dessous)*
   - **Nom** → `foundry-velmo-…`
   - **Niveau tarifaire** → `Standard S0`
3. **`Vérifier + créer`** → **`Créer`**. Compter 1 à 3 minutes.

### Note région — le piège qui coûte une heure

**Tous les modèles ne sont pas disponibles dans toutes les régions.** Si le modèle voulu
n'est pas proposé en France Central, créer la ressource Foundry en **Sweden Central** et
laisser le reste en France Central : l'agent appelle le modèle en HTTPS, la région n'a
aucune importance fonctionnelle (quelques dizaines de ms).

Rappel du bloc 1 : l'ACR est en **West Europe**. La ligne de conduite retenue est
*« registre où il est, données en France Central »* — le registre ne contient que des
images, la mémoire long terme contiendra des données personnelles.

### Vocabulaire — pourquoi c'est confus

« Azure OpenAI », « Azure AI Services », « Azure AI Foundry » et « Microsoft Foundry »
désignent des couches qui se recouvrent, Microsoft ayant tout renommé en 2025-2026. Ce
qu'il faut, indépendamment du nom : **une ressource exposant un endpoint qui parle l'API
OpenAI**, et **deux déploiements** dessus.

## Étape 2 — déployer les deux modèles

1. Sur la ressource → **`Aller au portail Azure AI Foundry`** (site distinct, nouvel
   onglet).
2. Menu de gauche → **`Déploiements`** (*Deployments* / *Model deployments*).
3. **`+ Déployer un modèle`** → **`Déployer un modèle de base`**.
4. **Déploiement n°1 — le chat** : `gpt-4o-mini` (bon compromis coût/qualité pour du
   support). → **Nom du déploiement à noter EXACTEMENT** : il ira dans `LLM_MODEL`.
5. **Déploiement n°2 — les embeddings** : `text-embedding-3-small`. → son nom ira dans
   `EMBEDDINGS_MODEL`.

> **Pourquoi deux, et pourquoi le second n'est pas optionnel.** L'agent fait du RAG sur la
> FAQ Velmo **et** de la mémoire long terme vectorielle. Le smoke test du bloc 1 l'a
> montré noir sur blanc : *« 2 appels embeddings / 17 vecteurs »* **au démarrage**, avant
> de servir la moindre requête.

## Étape 3 — relever endpoint et clé

Dans le portail Foundry : page de la ressource ou du déploiement → **`Endpoint`** /
**`Clés et point de terminaison`**.

| Relevé | Destination |
|---|---|
| URL de base `https://<ressource>.services.ai.azure.com` | + **`/openai/v1`** → `LLM_INFERENCE_ENDPOINT` |
| **Clé 1** | `LLM_INFERENCE_API_KEY` (ira au Key Vault au bloc 6) |
| Nom du déploiement chat | `LLM_MODEL` |
| Nom du déploiement embeddings | `EMBEDDINGS_MODEL` |

Valeur finale attendue :

```
https://foundry-velmo-xxx.services.ai.azure.com/openai/v1
```

### Le suffixe `/openai/v1` n'est pas décoratif

C'est la route **à versionnement implicite**, celle qui rend l'endpoint compatible avec le
SDK OpenAI standard — donc avec le `init_chat_model(model_provider="openai", base_url=…)`
de `factory.py`. Sans lui : des `404` dans les logs du conteneur.

*Vérifié le 2026-08-24 sur la doc Microsoft (via Context7) : la forme documentée est bien*
*`https://<compte>.services.ai.azure.com/openai/v1`. Le tuto §5.3 affirme que l'hôte*
*`<ressource>.openai.azure.com` marche aussi sur cette route ; **cette seconde forme n'a**
***pas pu être confirmée**. En cas de 404, essayer l'autre hôte avant de chercher ailleurs.*

---

## Étape 4 — les preuves

### Preuve 1 : le playground (5 secondes, au portail)

Portail Foundry → **`Chat`** / playground sur le déploiement → envoyer « bonjour ».
S'il répond, le service d'IA est prêt.

**À faire tout de suite** : cinq secondes ici contre vingt minutes de diagnostic si le
problème n'apparaît qu'au démarrage du conteneur.

### Preuve 2 : depuis la machine, avec les vraies valeurs

Plus fort que le playground : cela teste **l'endpoint, la clé et le nom de déploiement
tels que l'agent les utilisera**, hors de toute UI Azure.

```bash
# La clé est saisie dans le shell, jamais écrite dans un fichier du dépôt.
read -rs AZ_KEY && export AZ_KEY
export AZ_ENDPOINT="https://foundry-velmo-xxx.services.ai.azure.com/openai/v1"

# a) le chat
curl -s "$AZ_ENDPOINT/chat/completions" \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $AZ_KEY" \
  -d '{"model":"<NOM-DU-DEPLOIEMENT-CHAT>","messages":[{"role":"user","content":"dis bonjour"}]}' \
  | head -c 400

# b) les embeddings — celui qui conditionne le DÉMARRAGE du conteneur
curl -s "$AZ_ENDPOINT/embeddings" \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $AZ_KEY" \
  -d '{"model":"<NOM-DU-DEPLOIEMENT-EMBEDDINGS>","input":"test"}' \
  | head -c 200
```

Lecture des réponses :

| Réponse | Signification |
|---|---|
| Un JSON avec `choices` / `data` | ✅ endpoint, clé et déploiement corrects |
| `401` | clé fausse, ou `Bearer` refusé → réessayer avec `-H "api-key: $AZ_KEY"` |
| `404` | le `/openai/v1` manque, ou le mauvais hôte |
| `DeploymentNotFound` | on a mis le nom du **modèle** au lieu du nom du **déploiement** |

Ces deux `curl` sont **le** filet de sécurité du bloc : s'ils passent, un échec au
démarrage de la Web App (bloc 4) ne pourra plus venir du service d'IA.

---

## Journal d'exécution

**Les modèles étaient déjà déployés** sur la ressource Foundry : les étapes 1 et 2
ci-dessus n'ont pas eu à être refaites. Restait le relevé et la vérification.

| Élément | Valeur |
|---|---|
| Nom de la ressource | `slaurentext-5359-resource` |
| Région | **West Europe** |
| Déploiement chat → `LLM_MODEL` | `gpt-5.6-sol` |
| Déploiement rapide → `LLM_FAST_MODEL` | `gpt-5.6-luna` |
| Déploiement embeddings → `EMBEDDINGS_MODEL` | `text-embedding-3-small` |
| `LLM_INFERENCE_ENDPOINT` | `https://slaurentext-5359-resource.openai.azure.com/openai/v1` |
| Dimension des embeddings | **1536** |

### Trois déploiements, pas deux — et le troisième marche tout seul

Le tuto n'en prévoyait que deux (chat + embeddings). Il y en a trois, parce que l'agent a
un **rail « modèle rapide »** : `graph/builder.py:88,134` confie le *routeur* à un modèle
plus petit avant d'engager le modèle fort.

Bonne nouvelle vérifiée dans le code : **aucune variable supplémentaire n'est nécessaire.**

```python
# llm/factory.py:104-107
if not settings.llm_fast_model:
    ...                                    # cascade OFF, tout sur le modèle fort
provider = settings.llm_fast_provider or settings.llm_provider
```

`LLM_FAST_PROVIDER` n'étant pas défini, le rail rapide retombe sur `LLM_PROVIDER`
(`openai_compatible`) et réutilise donc le **même endpoint et la même clé**. Il suffit de
renseigner `LLM_FAST_MODEL`. Seule exigence : que `gpt-5.6-luna` soit un **déploiement**
sur la ressource, pas seulement un modèle disponible au catalogue.

### Décision de région — révisée ici

**Tout en West Europe, Postgres compris**, alors que le brief demande France Central /
Sweden Central.

Le raisonnement, dans l'ordre où il s'est imposé :

1. L'ACR était déjà en West Europe (bloc 1) : sans importance, un registre ne sert qu'au
   pull de l'image, une fois.
2. Foundry s'y trouve aussi : sans importance non plus, l'agent l'appelle en HTTPS.
3. **Mais App Service et Postgres, eux, doivent être co-localisés.** Chaque tour de
   conversation fait plusieurs allers-retours vers la base (mémoire courte, mémoire long
   terme, écriture après réponse). Les séparer ajouterait cette latence à *chaque*
   échange.

Donc : tout dans `slaurentRG`, tout en West Europe, données dans l'UE. L'écart au brief
est un choix cohérent et assumé plutôt qu'une dispersion subie — et il sert le critère de
performance « ressources regroupées, faciles à retrouver et à supprimer ».

**À écrire tel quel dans le dossier de déploiement.**

---

## La vérification, et ce qu'elle a tranché

Script : `check_foundry.py` (bibliothèque standard, clé saisie en interactif via
`getpass` — elle n'entre ni dans le dépôt, ni dans l'historique du shell).

```
Endpoint : https://slaurentext-5359-resource.openai.azure.com/openai/v1
Clé Foundry (saisie masquée) :

  ✅ chat `gpt-5.6-sol` — en-tête `bearer` — Bonjour ! 👋
  ✅ rapide `gpt-5.6-luna` — en-tête `bearer` — Bonjour !
  ✅ embeddings `text-embedding-3-small` — en-tête `bearer` — vecteur de dimension 1536

→ Le service d'IA répond sur les trois déploiements.
```

### Trois enseignements

**1. L'hôte `openai.azure.com` fonctionne sur `/openai/v1`.** À la revue du tuto, cette
affirmation du §5.3 avait été classée « non confirmée » : la documentation Microsoft
consultée ne documentait que `services.ai.azure.com`. **Le test tranche : les deux formes
marchent.** La réserve est levée — et la méthode vaut d'être retenue : une doc silencieuse
n'est pas une doc qui contredit.

**2. L'en-tête `Authorization: Bearer` est accepté**, et c'est celui qui comptait. La doc
Microsoft montre `api-key:` dans ses exemples REST, mais `factory.py` passe par le SDK
OpenAI, lequel envoie `Bearer`. Si seul `api-key` avait fonctionné, un test au `curl`
aurait réussi là où l'agent aurait échoué — un faux positif coûteux. Le script essayait
donc `Bearer` **en premier**, exprès.

**3. La dimension 1536 ne se configure nulle part.**

```python
# memory/long_term.py:94-95
dims = len(embeddings.embed_query("probe"))
index = {"embed": embeddings, "dims": dims, "fields": ["text"]}
```

Le code **découvre** la dimension au démarrage plutôt que de la coder en dur : aucun
risque de désaccord entre le modèle et l'index. Cette ligne 94 est aussi, mot pour mot, la
raison pour laquelle un déploiement d'embeddings joignable est une **condition de
démarrage** : elle s'exécute dans le `lifespan`, avant qu'uvicorn n'ouvre son port.

> ⚠️ **Conséquence pour la suite :** au bloc 3, `PostgresStore.setup()` créera ses tables
> avec `dims=1536`. Changer de modèle d'embeddings **après coup** casserait le store — la
> colonne `vector` a une dimension figée. Un changement de modèle d'embeddings impose donc
> une remise à zéro de la mémoire long terme.

---

## ✅ Bloc 2 terminé

| Preuve attendue | Obtenue |
|---|---|
| Le service d'IA répond | ✅ trois déploiements, en `Bearer` |
| L'endpoint est celui que le code attend | ✅ `/openai/v1` validé sur l'hôte réel |
| Les noms de déploiement sont les bons | ✅ pas de `DeploymentNotFound` |
| La dimension d'embeddings est connue | ✅ 1536, découverte automatiquement |

**Bloc suivant :** PostgreSQL Flexible Server (§7 du tuto), en **West Europe** pour être
co-localisé avec la future App Service. À lancer tôt : 5 à 10 minutes de création.
