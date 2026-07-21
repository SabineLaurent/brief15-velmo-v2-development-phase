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
