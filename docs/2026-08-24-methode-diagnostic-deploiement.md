# Diagnostiquer un déploiement qui refuse de démarrer

*2026-08-24 — tiré du bloc 5 du déploiement Azure. Journaux de référence :
[bloc 5](deployement-journaling/2026-08-24-bloc-5-bascule-postgres.md) et
[les commandes de test](deployement-journaling/2026-08-24-tests-curl-deploiement.md).
Code cité : `memory/postgres_conn.py`, `memory/short_term.py`, `server.py`.*

> **Objectif de cette note.** Le bloc 5 consistait à changer **une variable**
> (`PERSISTENCE_BACKEND: memory → postgres`). Il a pris trois heures, à cause d'un mot de
> passe. Ce qui vaut d'être retenu n'est pas la panne — elle est banale — mais **la façon
> dont on est passé d'un écran blanc à une ligne d'erreur nommée.** Six réflexes, chacun
> illustré par ce qui s'est réellement produit ce soir-là.

---

## 1. Un suspect à la fois — la décision qui a tout rendu lisible

Avant même la panne, une décision de découpage : au bloc 4, l'agent avait été déployé avec
`PERSISTENCE_BACKEND=memory`, **alors que la base était prête depuis le bloc 3**.

Pourquoi se priver d'une étape ? Parce qu'un échec « la page affiche Application Error »
peut venir de cinq endroits : l'architecture de l'image, le port d'entrée, le fournisseur
d'IA, les secrets, ou la base. **Cinq pistes pour un symptôme unique.**

En branchant la base *après* que tout le reste ait été prouvé en ligne, on obtient ceci :

> Si le bloc 5 casse, la cause est **nécessairement** la base.

C'est le principe de la panne électrique : on ne remet pas tous les disjoncteurs d'un coup,
on les remonte un par un pour savoir lequel saute. Ça ne coûte rien ici — le geste du
bloc 5 est *une variable* — et ça transforme une enquête en vérification.

**Le diagnostic était donc écrit d'avance.** Quand le conteneur a refusé de démarrer, on
savait déjà *dans quelle pièce* chercher. Restait à trouver l'objet.

---

## 2. La couche qui parle n'est pas celle qui échoue

Azure affichait :

```
Container exited with exit code 3 during startup after 50.6s.
Please inspect your container logs for more details.
```

Ce message ne dit rien. Il est même trompeur : dans d'autres cas, App Service annonce
*« Container didn't respond to HTTP pings on port: 8000 »*, ce qui fait chercher un
problème de port là où il n'y en a aucun.

La raison tient en une phrase, et elle est écrite dans le code :

```python
# server.py — lifespan
"""Refuse an insecure start, then warm the agent up before serving traffic.
...
uvicorn opens its port only once this returns, so whatever fails here must say
why in the log.
"""
```

**Le port ne s'ouvre qu'à la fin du démarrage.** Tout ce qui échoue *pendant* — connexion
à la base, sonde des embeddings, configuration absente — se produit avant qu'Azure ait quoi
que ce soit à observer. Azure ne peut donc que constater l'absence : « le port ne s'est
jamais ouvert ». C'est un symptôme, jamais une cause.

**Réflexe : quand une plateforme décrit une absence, la cause est une couche au-dessus.**
Ne pas chercher dans le vocabulaire de celui qui constate, mais dans celui qui agit.

Le corollaire vaut pour le code qu'on écrit : `require_database_url()` échoue **au
démarrage**, délibérément.

```python
# memory/postgres_conn.py
"""Return the configured `DATABASE_URL`, or fail with an actionable message.

Called at boot by both memory factories. Failing here — loudly, before the
graph is built — is the point: a missing connection string discovered inside
a customer's request is the same bug, found at the worst possible moment.
"""
```

Échouer tôt et bruyamment, c'est ce qui rend une panne *diagnosticable*. Une configuration
invalide découverte dans la requête d'un client, c'est le même bug — au pire moment.

---

## 3. Une durée n'est une preuve que si l'on sait ce qui se passe pendant

Premier raisonnement, séduisant :

> Le timeout de connexion vaut 30 secondes (`_CONNECT_TIMEOUT_S = 30.0`), l'échauffement
> mesuré au bloc 4 valait ~20 s. **20 + 30 ≈ 50**, la durée observée. Donc c'est un timeout
> réseau. Donc c'est le pare-feu.

L'arithmétique tombait juste. Le raisonnement était faux.

```python
# memory/postgres_conn.py
pool.open(wait=True, timeout=_CONNECT_TIMEOUT_S)
```

`pool.open(wait=True, …)` ne tente **pas** une connexion : il en tente autant que
possible jusqu'à expiration du délai. Le journal, obtenu plus tard, l'a montré noir sur
blanc — cinq tentatives à intervalle croissant :

```
19:14:18  WARNING  psycopg.pool | error connecting …
19:14:21  (+3 s)
19:14:25  (+4 s)
19:14:31  (+6 s)
19:14:42  (+11 s)
19:14:46  psycopg_pool.PoolTimeout: pool initialization incomplete after 30.0 sec
```

Conséquence : **un mot de passe refusé, une base inexistante et un pare-feu fermé
produisent tous les trois exactement la même durée.** Le chronomètre ne discriminait rien.

C'est le piège de l'analogie du micro-ondes : trente secondes de bruit ne disent pas si
l'appareil chauffe ou tourne à vide. Il faut ouvrir la porte.

**Réflexe : une durée compatible avec une hypothèse n'est pas une preuve de cette
hypothèse.** Demander toujours : que fait le programme *pendant* ce temps ? Si la réponse
est « il réessaie », la durée est celle de l'abandon, pas celle de la cause.

---

## 4. Le silence et le refus ne disent pas la même chose

Deux échecs, à trois quarts d'heure d'intervalle, avec la même durée et le même code de
sortie :

| Échec | Ce que dit le journal | Ce que ça signifie |
|---|---|---|
| 19:14 | `FATAL: password authentication failed for user "velmoadmin"` | **On atteint le serveur.** Il répond, il refuse |
| 19:57 | *(rien)* puis `PoolTimeout` | **On n'atteint pas le serveur.** Personne ne répond |

Cette distinction a tranché **les deux** incidents de la soirée.

Le premier message, en apparence mauvais, était une excellente nouvelle : il contenait
`connection to server at "23.101.64.194", port 5432` — donc TCP, TLS et pare-feu étaient
traversés. Un refus applicatif **prouve** que tout le chemin réseau fonctionne. Il ne reste
qu'un seul suspect au lieu de quatre.

Le second, muet, disait l'inverse : le paquet n'arrivait nulle part. Cause réseau,
nécessairement.

**Réflexe : classer une erreur avant de la lire.** Refus nommé → le chemin est bon, le
problème est applicatif. Silence → le chemin est coupé, inutile de vérifier les
identifiants.

---

## 5. Écarter n'est pas trouver — la cause n'apparaît que là où le code la nomme

Chronologie réelle du premier incident :

| Étape | Résultat |
|---|---|
| Écran `Instances` | Un fait dur : `exit code 3`, 50,6 s. Aucun activation requise |
| Arithmétique des durées | ❌ Hypothèse non concluante (§3) |
| Inspection du pare-feu | ✅ Case cochée → hypothèse **écartée** |
| Journal de plateforme (Kudu) | Le conteneur démarre, meurt six fois de suite. Systématique, pas un aléa |
| **Journalisation applicative activée** | ✅ **La cause, nommée en une ligne** |

Quatre étapes ont **écarté** des hypothèses. Aucune n'en a confirmé une. C'est le geste le
moins spectaculaire — cocher `Journal des applications` = `Système de fichiers` — qui a
produit la réponse.

Et c'est logique : sur App Service Linux, la journalisation applicative est **désactivée
par défaut**. Tant qu'elle l'est, la sortie du programme n'existe nulle part. On peut
inspecter tous les écrans du portail : ils décrivent la plateforme, pas l'application.

**Réflexe : avant de raisonner sur une panne, s'assurer qu'on peut la lire.** Une heure
d'hypothèses coûte plus cher que trente secondes de configuration de logs.

---

## 6. Vérifier avant d'expliquer

Le travers s'est produit **deux fois dans la même soirée**, et il mérite d'être nommé.

**Premier épisode** — les 50 secondes : construire une explication arithmétique élégante
au lieu de lire le journal (§3).

**Second épisode** — après avoir restreint le pare-feu aux adresses sortantes d'App
Service, la connexion échoue. Explication produite dans la foulée :

> « Le trafic App Service → service Azure de la même région n'emprunte pas les adresses
> publiques mais le réseau interne. Filtrer par IP ne peut donc pas fonctionner. »

Plausible, structurelle, savante — **et fausse**. La vraie cause : une des trois règles de
pare-feu n'avait pas été enregistrée. Elle était visible à l'écran. Cinq des trente-deux
adresses tombaient précisément dans la plage manquante.

Règle rétablie → la connexion passe. Le filtrage fonctionne parfaitement.

**Réflexe : quand une explication est plus complexe que « quelque chose manque », vérifier
d'abord que rien ne manque.** Une théorie coûte cinq minutes à construire et peut coûter
une heure à défaire ; un coup d'œil à l'écran coûte cinq secondes.

Une manière de forcer la vérification : **tester hors du système suspect**. Rejouer la
chaîne de connexion en `psql` depuis le poste a pris dix secondes et a fait tomber trois
hypothèses d'un coup — l'espace en trop dans les paramètres, l'encodage du mot de passe, et
un problème propre à Azure. La même erreur hors d'Azure prouve qu'Azure n'y est pour rien.

---

## 7. Ce qui répond n'est pas forcément ce qu'on interroge

Dernier piège, spécifique aux plateformes qui redéploient sans coupure.

**App Service ne fait pas arrêt-puis-démarrage : il fait un chevauchement.**

```
20:04:49  Started server process [1]              ← le nouveau démarre
20:05:16  Application startup complete.            ← il est prêt
20:05:39  [Previous Container] Finished server …   ← l'ancien meurt, 50 s plus tard
```

Pendant ces cinquante secondes, **les deux tournent**. Une sonde HTTP reçoit `200` sans
savoir lequel lui a répondu.

Deux conclusions ont failli être fausses à cause de ça :

| Ce qu'on croyait mesurer | Ce qu'on mesurait |
|---|---|
| « La mémoire a survécu au redémarrage » | Peut-être une réponse de l'ancien processus |
| « Les nouvelles règles de pare-feu laissent passer » | **Rien** : un pool déjà ouvert n'est pas coupé par un changement de règles, qui ne filtre que les connexions neuves |

Le second cas est le plus vicieux : le test *réussit*, et ne prouve rien.

**Réflexe : on valide un changement d'infrastructure par le journal, pas par une sonde.**
Ce qu'il faut y trouver, dans cet ordre :

```
INFO:     Started server process [1]                      ← postérieur au changement
INFO  …postgres_conn | Postgres memory backend ready (…)  ← un pool NEUF s'est ouvert
```

Le préfixe `[Previous Container]` est la signature du recouvrement : sa présence atteste
qu'il y a eu deux processus, et son `Finished server process` donne l'instant à partir
duquel les réponses ne peuvent plus venir que du neuf.

---

## Ce que la méthode a produit — la preuve

| Sortie observée | Ce qu'elle démontre |
|---|---|
| `exit code 3 … after 50.6s` (écran `Instances`) | Le lifespan lève une exception ; ce n'est pas un plantage aléatoire |
| `Site startup probe succeeded after 34.0 s` (avant la bascule) | Le témoin : l'agent démarrait, la régression vient bien du changement |
| `FATAL: password authentication failed` | La cause, nommée — et la preuve que le réseau fonctionne |
| Même erreur en `psql` depuis le poste | La chaîne est en cause, pas Azure |
| `Postgres memory backend ready (schema=agent_state)` | Le pool s'ouvre, le schéma et l'extension existent |
| 8 tables dans `agent_state` | La structure est créée par le code, sans migration manuelle |
| `prefix = memories.sabine` en base | Le cloisonnement par client est **structurel**, pas déclaratif |
| Réponse « le vert » sur un `thread_id` neuf, après `Started server process` postérieur | La mémoire est **relue depuis la base** (R2) |
| « Je n'ai aucune information » sur un autre `user_id` | La mémoire ne fuit pas d'un client à l'autre (R3) |

---

## Les six réflexes, en une page

1. **Un suspect à la fois.** Découper pour que l'échec désigne sa cause.
2. **La couche qui parle n'est pas celle qui échoue.** Une plateforme décrit une absence ;
   la cause est au-dessus.
3. **Une durée n'est une preuve que si l'on sait ce qui se passe pendant.** Un retry rend
   le chronomètre muet.
4. **Silence ≠ refus.** Un refus nommé prouve que le chemin fonctionne.
5. **Vérifier avant d'expliquer.** Et rendre la panne lisible avant de raisonner dessus.
6. **Valider par le journal, pas par la sonde.** Ce qui répond n'est pas forcément ce
   qu'on interroge.

---

## Ce que cette note ne couvre pas — volontairement

- **Les gestes du bloc 5** (quelles variables, quels écrans, dans quel ordre) : c'est
  l'objet du [journal du bloc](deployement-journaling/2026-08-24-bloc-5-bascule-postgres.md),
  qui contient aussi la section « pour reproduire ».
- **Les commandes elles-mêmes** : rassemblées dans
  [la note des tests](deployement-journaling/2026-08-24-tests-curl-deploiement.md), avec
  les URL Kudu et ce que chacune prouve.
- **Les dettes de sécurité identifiées** (accès public de la base, `user_id` non
  authentifié) : consignées en fin de journal du bloc 5. Ce sont des constats
  d'architecture, pas de la méthode de diagnostic.
- **Une méthode générale de débogage.** Ces six réflexes viennent d'**une** soirée sur
  **un** incident. Ils valent ce que vaut leur origine : des habitudes vérifiées une fois,
  à confronter au prochain incident plutôt qu'à appliquer mécaniquement.
