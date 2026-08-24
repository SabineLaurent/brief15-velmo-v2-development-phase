# Déplacer des commits vers une autre branche, sans risque

*2026-08-24 — note technique. Situation réelle : deux commits faits sur `restitution`
alors qu'ils appartenaient à un travail distinct. Aucun n'avait été poussé.*

> **Objectif.** Comprendre pourquoi cette opération, qui semble être un « déménagement de
> commits », ne déplace en réalité **rien du tout** — et pourquoi c'est précisément ce qui
> la rend sûre. Deux commandes, aucun fichier touché, un filet de sécurité derrière.

---

## 1. Le concept qui rend tout le reste évident : une branche est une étiquette

C'est l'idée à retenir, et elle est contre-intuitive quand on vient d'autres outils.

**Une branche Git ne contient pas de commits.** C'est un fichier de 41 octets qui contient
l'identifiant d'**un seul** commit — le dernier de la lignée. Chaque commit, lui, connaît
son parent. La « branche » qu'on croit voir est la chaîne qu'on obtient en remontant de
parent en parent depuis ce point de départ.

```
9ce1275 ← e708289 ← d208c63
                       ↑
                  restitution
```

Une analogie : les commits sont les wagons d'un train déjà formé, soudés les uns aux
autres. Une branche n'est pas le train — c'est **l'étiquette collée sur le dernier
wagon**. Déplacer l'étiquette ne déplace aucun wagon.

Conséquence directe : « déplacer des commits d'une branche à l'autre » est une phrase
trompeuse. Ce qu'on fait réellement, c'est :

1. **coller une deuxième étiquette** sur le dernier wagon (`brief16-deploiement-azure`) ;
2. **reculer la première** de deux wagons (`restitution`).

Les commits ne bougent pas d'un octet. Seuls deux pointeurs changent.

---

## 2. Les trois zones — savoir ce qu'une commande touche

Git manipule trois espaces distincts, et **c'est là que se joue la sûreté d'une
opération** :

| Zone | Ce que c'est | Ce qu'on perd si on l'écrase |
|---|---|---|
| **HEAD** | Où je suis — quelle branche est courante | Rien : c'est un pointeur |
| **Index** (*staging*) | Ce qui est préparé pour le prochain commit | Le travail indexé, non commité |
| **Répertoire de travail** | Les fichiers sur le disque | **Le travail en cours, non commité — irrécupérable** |

Une opération est sans risque exactement quand elle ne touche **ni l'index, ni le
répertoire de travail**. C'est le critère à appliquer avant de taper une commande Git.

---

## 3. La manipulation — deux commandes

```bash
git switch -c brief16-deploiement-azure
git branch -f restitution origin/restitution
```

### `git switch -c <nom>` — poser la deuxième étiquette

Crée une branche **au commit courant**, puis y bascule.

Le point crucial : la branche pointe sur le commit **où l'on est déjà**. Changer de branche
sans changer de commit ne demande aucune modification de fichier — Git se contente de
réécrire `HEAD`. Le disque n'est pas touché.

*(Le vieil équivalent est `git checkout -b`. `switch` a été introduit pour séparer deux
usages que `checkout` confondait : changer de branche, et restaurer des fichiers. Le second
est destructeur, le premier non ; leur donner le même nom était une source d'accidents.)*

### `git branch -f <branche> <cible>` — reculer la première étiquette

Force une branche à pointer ailleurs. `-f` (*force*) est nécessaire parce que la branche
existe déjà.

Ce qui rend la commande sûre : **elle agit sur une branche qu'on n'occupe pas.** Git refuse
d'ailleurs de l'appliquer à la branche courante — il n'y a pas de checkout, donc rien à
reconstruire sur le disque. C'est une écriture de pointeur, rien de plus.

`origin/restitution` désigne « l'état du serveur tel que je l'ai vu au dernier
`fetch` ». On remet donc la branche locale exactement là où le distant la croit — ce qui
supprime toute divergence.

Git confirme d'ailleurs le suivi au passage :

```
la branche 'restitution' est paramétrée pour suivre 'origin/restitution'.
```

---

## 4. Ce qu'on a évité : `reset --hard`

L'autre chemin, plus connu, aurait été :

```bash
git branch brief16-deploiement-azure     # poser l'étiquette
git reset --hard origin/restitution      # ⚠️ reculer restitution
git switch brief16-deploiement-azure
```

Il aboutit au même endroit. Mais `--hard` **écrase les trois zones** : HEAD, l'index, et
le répertoire de travail. Tout ce qui n'était pas commité disparaît sans confirmation et
sans trace — le reflog ne garde que les commits, jamais le travail non commité.

| | Zones touchées | Travail non commité |
|---|---|---|
| `switch -c` + `branch -f` | HEAD seulement | **Intact** |
| `reset --hard` | HEAD + index + disque | **Détruit** |

Le résultat identique masque une différence de nature. Dans le cas présent, un fichier non
suivi (`Brief_deployement-sur-azure.md`) attendait dans le répertoire — `--hard` l'aurait
épargné, car il ignore les fichiers non suivis, mais aurait emporté toute modification en
cours sur un fichier déjà suivi.

**Règle : préférer la commande dont on peut nommer les zones touchées.** Quand deux chemins
mènent au même état, prendre celui qui touche le moins.

---

## 5. La condition qui rendait tout cela légitime

```bash
git log --oneline origin/restitution..restitution
```
```
d208c63 feat(make): ajouter `make wake` …
e708289 docs: journaliser le déploiement Azure …
```

Cette commande liste ce que **j'ai** et que **le serveur n'a pas**. Deux commits, donc rien
n'était poussé.

C'est la condition qui autorise à récrire l'histoire locale : **on ne réécrit que ce que
personne d'autre n'a vu.** Un commit déjà poussé et récupéré par quelqu'un fait partie de
l'histoire commune ; le retirer obligerait tout le monde à réparer son dépôt.

| Situation | Geste correct |
|---|---|
| Commit **local** uniquement | Déplacer le pointeur (cette note) |
| Commit **déjà poussé** | `git revert` — ajouter un commit qui annule, sans effacer |

`revert` ne récrit rien : il ajoute un wagon qui défait le précédent. Moins élégant,
mais c'est le seul geste honnête sur une histoire partagée.

---

## 6. Le filet — le reflog garde tout

Git journalise **chaque déplacement de pointeur**, y compris ceux qu'on regrette.

```bash
git reflog show restitution
```
```
9ce1275 restitution@{0}: branch: Reset to origin/restitution
d208c63 restitution@{1}: commit: feat(make): ajouter `make wake` …
e708289 restitution@{2}: commit: docs: journaliser le déploiement Azure …
9ce1275 restitution@{3}: clone: from github.com:…
```

On lit l'opération de ce soir à l'entrée `{0}`, et **l'état d'avant est toujours là** en
`{1}`. Rétablir la branche prendrait une commande :

```bash
git branch -f restitution restitution@{1}
```

Les commits « perdus » ne le sont donc pas : ils restent atteignables une trentaine de
jours, jusqu'au passage du ramasse-miettes. **Tant qu'un commit a existé, le reflog sait où
il était.**

C'est ce qui distingue une erreur de pointeur — réparable en dix secondes — d'un
`reset --hard` sur du travail non commité, qui, lui, n'a jamais existé pour Git.

---

## La preuve — avant / après

| Vérification | Sortie observée | Ce qu'elle démontre |
|---|---|---|
| `git log --oneline -1` | `d208c63 feat(make): …` | La nouvelle branche porte bien les deux commits |
| `git log --oneline -1 restitution` | `9ce1275 docs: point the review references…` | `restitution` est revenue à l'état du distant |
| `git branch` | `* brief16-deploiement-azure` / `restitution` | On se trouve sur la nouvelle branche |
| `git status --short` | `?? Brief_deployement-sur-azure.md` | **Le répertoire de travail est intact** — le fichier non suivi n'a pas bougé |
| `git reflog show restitution` | `{1}` pointe encore sur `d208c63` | L'opération est réversible |

La quatrième ligne est celle qui compte : elle prouve que la manipulation a été purement
symbolique. Aucun fichier n'a été lu, écrit ni supprimé.

---

## Le résumé, en trois phrases

1. **Une branche est une étiquette sur un commit**, pas un contenant : la déplacer ne
   déplace aucun fichier.
2. **`switch -c` puis `branch -f` ne touchent que des pointeurs** ; `reset --hard` touche
   aussi le disque — à résultat égal, choisir le premier.
3. **On ne récrit que ce qui n'est pas poussé** ; au-delà, c'est `revert`. Et le reflog
   rattrape les erreurs de pointeur, jamais le travail non commité.

---

## Ce que cette note ne couvre pas — volontairement

- **`git cherry-pick`** : utile pour transporter *quelques* commits choisis vers une
  branche existante. Ici les deux commits étaient les derniers et allaient ensemble, donc
  déplacer le pointeur suffisait — et ne créait pas de doublons.
- **`git rebase`** : rejoue des commits ailleurs, donc en **fabrique de nouveaux**, avec
  de nouveaux identifiants. Nécessaire quand la destination a divergé ; inutile ici,
  puisque les commits étaient déjà au bon endroit dans le graphe.
- **La résolution de conflits** : aucune n'est possible dans cette manipulation, justement
  parce qu'aucun contenu n'est fusionné.
- **Le cas du travail partagé** : tout ce qui précède suppose une histoire locale. Dès
  qu'un commit est poussé et récupéré par quelqu'un, les règles changent — voir §5.
