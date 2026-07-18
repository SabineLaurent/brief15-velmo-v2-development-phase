# 📖 Glossaire — le jargon dev, en clair

> Les mots d'anglais tech qui reviennent dans le projet, expliqués simplement.
> Doc **vivante** : on ajoute une entrée dès qu'un terme mérite d'être retenu.

---

## lean

**Prononciation :** « line ». **Sens littéral :** *maigre*, *sans gras*.

En dev (et au-delà), « lean » qualifie quelque chose de **sobre, minimaliste,
réduit à l'essentiel** — l'inverse de « gras / bloated » (surchargé, redondant).

**Concrètement, un fichier / code / process *lean* est :**
- **court** — que l'essentiel, pas de pavés ;
- **sans duplication** — il *renvoie* vers la source au lieu de la recopier ;
- **ciblé** — rien que ce qui a de la valeur ici ;
- **actionnable** — clair et directement utile, pas de blabla.

**Exemple dans le projet :** le `CLAUDE.md` d'un package est *lean* — il ne dit
que le **local** et renvoie vers `docs/` au lieu de répéter le root. Un fichier
« gras » serait long, répéterait les docs, et noierait l'info importante.

**D'où ça vient (culture générale utile) :** le terme naît du *lean
manufacturing* de Toyota (années 1950) — produire en **éliminant le gaspillage**,
ne garder que ce qui crée de la valeur. L'idée a essaimé partout :
- **lean startup** — construire le minimum pour apprendre vite (le *MVP*), sans
  sur-investir avant d'avoir validé ;
- **lean code** — du code simple et sans superflu ;
- **run lean** — fonctionner avec peu de ressources.

**À retenir en une phrase :** *lean = garder ce qui a de la valeur, couper le
superflu.*

---

<!-- Prochaines entrées : couture (seam), RAG, checkpointer, embedding, boilerplate… -->
