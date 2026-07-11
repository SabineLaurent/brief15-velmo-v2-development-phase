# Projet Velmo2.0

## Overview

- On part de la recommendation de l'expert @docs/reco_expert.md. Ce fichier est notre ligne directrice.

- Le fichier @docs/first-sight.md donne la vision du repo dans l'état initial dans lequel je l'ai récupéré.

- Le fichier @docs/architecture-agent-support.md est une note de référence agnostique sur la construction classique d'un agent de support (briques, orchestration, mémoire à étages, garde-fous, boucle d'éval).
  
## Bonnes pratiques

- DRY, SoC, YAGNI, KISS, etc.
- Clean code
- PEPs

## Nommage

Nommage explicite et en anglais.

### Fonctions

- verbe d'action + _ + object de l'action
    exemple: pour une fonction qui va saluer un utilisateur --> say_hi_to_user()

### Variables

- nom commun explicite
    exemple: une variable qui stocke le nom d'un utilisateur --> user_name

## Architecture

- suivre la note de référence agnostique @docs/architecture-agent-support.md. Si ce n'est pas possible pour une raison valable (mauvaise pratique, faille de sécurité, obsolescence de code, etc.) m'en informer dans le terminal avant d'adopter une solution alternative.

## Spec

- suivre la note de l'expert @docs/reco_expert.md

- Etapes:

  1. la memoire

     - court terme
     - long terme épisodique
     - long terme factuelle
     - garder la procédurale en stand by
  
  2. les gardes-fous

     - entrée
     - sortie
     - cadrage interne: à garder en stand by

  3. La CI sera en bonus si le temps le permet.


## Consignes complémentaires

- Les implementations se feront avec du code simple, explicite et minimaliste.
- Chaque implémentation se fera étape par étape
- Chaque implémentation sera accompagnée d'une note de cours explicative, à visée pédagogique pour des débutants en python et code d'agents IA. La note sera au format markdown aura un intitulé explicite avec la date et l'étape d'implémentation dont il est question. Le .md sera enregistré dans @docs

## Tests

- Les tests d'acceptance en place sont minimalistes @tests/acceptance. Des tests complémentaires sont à écrire.
- Les tests complémentaires seront enregistrés dans @tests, et devront couvrir les exigences de la note de l'expert ainsi que toute fonctionnalité mise en place.
- Les tests doivent:
  1. vérifier que le comportement du code est celui attendu
  2. permettre de s'assurer de la non régression
