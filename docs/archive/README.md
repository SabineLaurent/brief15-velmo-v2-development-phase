# 🗄️ docs/archive — les documents figés

Ce dossier contient des documents **datés**, conservés pour mémoire mais qui **ne
décrivent plus l'état courant** du projet.

## Pourquoi un dossier séparé

Un document dérivé du code (compte-rendu, audit, mesure) est vrai **le jour où il
est écrit**, puis diverge à chaque commit — sans que personne ne pense à le mettre
à jour, puisque personne ne le relit. Le laisser au milieu des docs vivantes le
fait passer pour une source fiable ; le supprimer perdrait son contenu.

D'où la troisième voie : on le déplace ici, avec un **bandeau d'avertissement** en
tête indiquant sa date et ce qu'il faut lire à la place.

## Règle

- Les docs de `docs/` sont **vivantes** : on les corrige quand le code change.
- Les docs d'ici sont **figées** : on n'y touche plus. Si le contenu redevient
  utile, on le réécrit dans une doc vivante plutôt que de le rafraîchir ici.
- Tout fichier archivé porte un bandeau : date, raison, où trouver l'info à jour.

## Contenu

| Document | Instantané de | Remplacé par |
|---|---|---|
| `COMPTE-RENDU-FONCTIONNEMENT_26-07-17.md` | le code au commit `64c3049` (fin Phase 12), 17/07/2026 | `docs/architecture.md` + le code |
| `audit-code-2026-07-19.md` | les défauts du code au 19/07/2026 — **7 findings, tous clos** le 29/07/2026 | `docs/revue-code-2026-07-25.md` (revue plus récente) + `TODO_priorities.md` |

> Note : l'audit est le premier document archivé pour **épuisement** et non pour
> divergence. Il n'est pas devenu faux, il est devenu **fini** — et une liste de
> tâches vide au milieu des docs vivantes se relit comme du travail restant.
