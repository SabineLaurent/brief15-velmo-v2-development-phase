# 📦 Anatomie d'un package dans le workspace — noms & structure

> Note de référence pour la **mise en place**. Quand on crée un membre du
> workspace, on tombe sur deux choses qui semblent redondantes : trois niveaux de
> dossiers emboîtés (`support-agent/src/support_agent/`) et le **même mot écrit
> deux fois**, une fois avec un tiret, une fois avec un underscore. Rien n'est du
> bruit : chaque détail répond à une règle précise. Ce doc explique **le pourquoi**.
> Complète [`architecture.md`](architecture.md) §9 (la *mécanique* workspace) et
> [`workspace-et-deploiement.md`](workspace-et-deploiement.md) (le *runtime*).

## La forme qu'on rencontre

```
packages/support-agent/        ① l'unité de déploiement (le « membre » du workspace)
├── pyproject.toml             #    → ses dépendances, son nom de distribution
├── README.md
├── tests/
└── src/                       ② le « src layout » (une convention de packaging)
    └── support_agent/         ③ le package Python réellement importable
        ├── __init__.py
        ├── config.py
        └── …
```

Trois niveaux, **trois questions différentes**. On les prend un par un.

## Le piège des noms : `support-agent` (tiret) vs `support_agent` (underscore)

Ce ne sont **pas** deux orthographes du même nom. Ce sont **deux noms**, dans deux
mondes distincts :

| Nom | C'est quoi | Où il vit | La règle |
|---|---|---|---|
| `support-agent` (**tiret**) | le **nom de distribution** — le « projet », ce qu'on installerait (`uv add` / `pip install`) | `[project] name` du `pyproject.toml` ; nom du dossier membre | c'est une **chaîne**, pas du code. Convention (PEP 503/508) : le tiret. |
| `support_agent` (**underscore**) | le **nom du package importable** | `from support_agent import …` ; dossier `src/support_agent/` | un **identifiant Python** ne peut pas contenir de tiret. |

**Le point-clé (à retenir absolument) :** `import support-agent` serait une **erreur
de syntaxe** — Python lirait `support` **moins** `agent`, une soustraction. Un
module importable est un identifiant : **underscore obligatoire**.

C'est une convention Python **universelle** — tu l'utilises déjà sans le savoir :

| On installe (tiret) | On importe (underscore ou autre) |
|---|---|
| `uv add python-dotenv` | `import dotenv` |
| `pip install scikit-learn` | `import sklearn` |
| `uv add support-agent` | `import support_agent` |

> 💡 Les outils (uv, pip) **normalisent** tiret ↔ underscore côté *distribution* :
> `support-agent` et `support_agent` y sont traités comme équivalents. La
> distinction stricte ne s'applique qu'au **nom importable** (le dossier `③`), qui
> doit être un identifiant valide. Ici on a volontairement gardé les deux
> **parallèles** (`support-agent` / `support_agent`) pour la lisibilité — voir ①.

## Les trois niveaux, un par un

### ① `packages/support-agent/` — **nécessaire** (c'est le choix mono-repo)

`members = ["packages/*"]` (dans le `pyproject.toml` racine) : chaque **membre** du
workspace est un dossier doté de **son propre `pyproject.toml`**. C'est *lui* qui
fait de l'agent une **tranche indépendante** — installable seule
(`uv sync --package support-agent`), conteneurisable seule (un lockfile → N images,
cf. [`workspace-et-deploiement.md`](workspace-et-deploiement.md)).

Sans ce niveau, **pas de scopes découplés**. Et ce dossier n'est pas que du code :
il porte aussi le `README`, les `tests/`, un futur `Dockerfile` **propres à ce
scope**.

> Le nom du dossier membre est **libre** : on pourrait avoir `packages/agent/…`. On
> a choisi `support-agent` pour qu'un lecteur relie tout de suite le dossier au
> package. La « répétition » du mot est donc un **choix de lisibilité**, pas une
> obligation technique.

### ② `src/` — **pas obligatoire, mais bonne pratique** (le « src layout »)

On *pourrait* l'enlever (« flat layout » : `packages/support-agent/support_agent/`).
Pourquoi on le garde quand même — deux bénéfices concrets :

1. **Il force à tester le package *installé*, pas les fichiers sources** qui
   traînent par hasard dans `sys.path`. Sans `src/`, `import support_agent` marche
   depuis la racine *même si le package est mal déclaré* (un fichier oublié dans le
   wheel) → le bug de packaging ne se voit qu'en prod. Avec `src/`, la racine n'est
   **pas** importable : on teste ce qui sera **réellement livré**.
2. **Frontière nette** : seul le code va dans `src/`. Tests, config, `data/` ne
   risquent pas d'être aspirés dans le wheel. C'est déclaré explicitement dans le
   `pyproject.toml` du package :
   ```toml
   [tool.hatch.build.targets.wheel]
   packages = ["src/support_agent"]
   ```

### ③ `support_agent/` — **nécessaire**

C'est le package qu'on importe partout : `from support_agent.llm.factory import …`.
Son nom **doit** être un identifiant Python (donc underscore — voir le piège des
noms plus haut).

## Peut-on simplifier ? (le verdict)

Par ordre de « à éviter » à « libre » :

| Simplification envisagée | Verdict |
|---|---|
| Aplatir en `packages/support_agent/` (un seul niveau) | ❌ casse ① → casse le principe des scopes découplés. |
| Enlever le `src/` (flat layout) | ⚠️ possible, mais on **perd** le garde-fou packaging du ②. Déconseillé pour un projet visant le « prod-grade ». |
| Renommer le dossier membre (① ≠ nom du package) | ✅ libre — la seule vraie redondance *cosmétique*, qu'on **assume** pour la clarté. |

## En une phrase

Le **tiret/underscore** est une **règle du langage** (nom de distribution vs nom
importable), pas un choix ; et sur les trois niveaux de dossiers, un seul est
« optionnel » (le `src/`) — mais c'est précisément celui qui **protège la qualité
de packaging**, donc on le garde volontairement.

---

> 📌 À lire aussi : [`architecture.md`](architecture.md) §9 (mécanique uv
> workspace : `uv.lock` partagé, `.venv` unique, `workspace = true`) et
> [`workspace-et-deploiement.md`](workspace-et-deploiement.md) (pourquoi un
> lockfile mutualisé n'enferme dans aucune topologie de déploiement).
