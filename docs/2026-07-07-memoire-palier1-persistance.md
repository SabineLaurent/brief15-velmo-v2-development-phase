# Mémoire — Palier 1 : l'étage de persistance

_Note pédagogique, 2026-07-07. Fichier concerné : `src/velmo/memory/store.py`._

> Objectif de cette note : expliquer, du « pourquoi » vers le « comment », la
> fondation sur laquelle repose toute la mémoire de l'agent. Ce palier ne contient
> aucune intelligence de rappel — juste un rangement fiable et **isolé par
> client**. L'intelligence (quoi remonter, budget de tokens) vient au-dessus, dans
> `MemoryManager` (paliers 2 et 3).

---

## 1. À quoi sert ce fichier ?

`store.py` est **l'étage de rangement** de la mémoire. Son seul travail : *écrire*
et *relire* des données de façon fiable, en garantissant qu'un client ne voit
jamais la mémoire d'un autre.

La mémoire est séparée en **deux tables**, comme le veut la note d'architecture
(deux étages complémentaires, pas deux options) :

| Table | Contient | Question à laquelle elle répond |
|-------|----------|--------------------------------|
| `memory_facts` | faits durables clé→valeur (`pointure=L`) | « quelle taille prend ce client ? » |
| `memory_episodes` | journal des échanges, tour par tour | « m'a-t-il déjà parlé de… ? » |

La distinction est structurante :

- un **fait** est **exact et écrasable** — la pointure change, on remplace ;
- un **épisode** est **un souvenir daté qu'on n'écrase jamais** — on empile.

D'où deux structures de données différentes, traitées différemment.

---

## 2. Décrire une table en Python : l'ORM

On n'écrit pas de SQL à la main. On utilise **SQLAlchemy**, un *ORM*
(Object-Relational Mapper) : il fait le pont entre une **classe Python** et une
**table SQL**.

```python
class MemoryFact(Base):
    __tablename__ = "memory_facts"
    user_id: Mapped[str] = mapped_column(String, primary_key=True)
    key:     Mapped[str] = mapped_column(String, primary_key=True)
    value:   Mapped[str] = mapped_column(String)
```

À lire ainsi : « il existe une table `memory_facts` avec 3 colonnes texte ».

Le point important est la **clé primaire composée** (`user_id` **et** `key` en
`primary_key=True`). Traduction métier : *un utilisateur n'a qu'une seule valeur
par clé*. Marc n'a qu'une `pointure`. C'est ce qui :

1. rend l'écrasement propre (un fait = un état courant, pas un historique) ;
2. pose le **premier verrou d'isolation** : la donnée est indexée par `user_id`.

Pour les épisodes, la clé primaire est un `id` **auto-incrémenté** (1, 2, 3…).
Pourquoi ? Parce qu'on empile des souvenirs et qu'on veut **retrouver l'ordre
chronologique** : trier par `id` croissant = ordre d'arrivée. C'est ce qui
garantira, au palier 3, qu'on sait quel tour était « le premier ».

---

## 3. Où vivent réellement les données ? Le choix d'*engine*

Une classe Python ne stocke rien seule. Il faut une **base concrète** derrière.
C'est le rôle de `_default_url` :

```python
def _default_url() -> str:
    env = os.getenv("MEMORY_DB_URL")
    if env:
        return env                       # Postgres en prod, si fourni
    path = Path.home() / ".velmo" / "memory.db"
    path.parent.mkdir(parents=True, exist_ok=True)
    return f"sqlite:///{path}"           # sinon : fichier local, hors-ligne
```

C'est le **motif de repli** qu'on retrouve partout dans ce repo (`get_kb`, `db`) :
*si un vrai service est branché, on l'utilise ; sinon on tombe sur une version
locale qui marche sans rien installer*.

- **En production** : `MEMORY_DB_URL` pointe vers Postgres → source de vérité
  partagée.
- **Hors-ligne (tests, démo)** : un **fichier SQLite** sur le disque.

### Le point décisif : fichier, pas RAM

C'est le choix qui fait passer `test_cross_session_persistence`. Une base « en
mémoire vive » disparaît à l'arrêt du programme, et deux objets `MemoryManager`
distincts auraient chacun *leur* RAM — donc aucune mémoire partagée. Un
**fichier** est partagé : deux instances, deux exécutions, deux jours plus tard →
même fichier, mêmes données. C'est la définition littérale de « persistance
multi-session ».

---

## 4. Le cache d'*engines* : un détail qui compte

```python
_SESSIONMAKERS: dict[str, sessionmaker] = {}

def _sessionmaker_for(url: str) -> sessionmaker:
    sm = _SESSIONMAKERS.get(url)
    if sm is None:
        engine = create_engine(url, future=True)
        Base.metadata.create_all(engine)   # crée les tables si absentes
        sm = sessionmaker(bind=engine, expire_on_commit=False, future=True)
        _SESSIONMAKERS[url] = sm
    return sm
```

Ouvrir une connexion à une base coûte cher. Ce dictionnaire dit : « pour une URL
donnée, ne crée l'*engine* **qu'une fois**, puis réutilise-le ». Deux
`MemoryStore()` dans le même programme partagent donc le même moteur.

`create_all` est **idempotent** : il crée les tables si elles n'existent pas, ne
fait rien sinon. On peut l'appeler cent fois sans casse.

---

## 5. Les méthodes : les gestes de base

`MemoryStore` n'expose que le strict nécessaire. Deux idées transversales à
retenir.

### a) La session comme transaction

```python
with self._sm() as s:
    s.add(MemoryEpisode(...))
    s.commit()
```

Une *session* est un espace de travail temporaire : on y prépare des changements,
puis `commit()` les **grave** dans la base. Le `with` garantit une fermeture
propre même en cas d'erreur. **Sans `commit`, rien n'est sauvé** — c'est le
brouillon jeté.

### b) L'isolation, partout

Chaque lecture filtre par utilisateur, sans exception :

```python
select(MemoryFact).where(MemoryFact.user_id == user_id)
```

Aucune requête ne lit sans `WHERE user_id`. C'est la règle non négociable de la
note d'archi : une fuite mémoire entre clients est la faute la plus grave. Elle
est tenue **par construction**, au niveau du rangement — pas en espérant que la
couche du dessus fasse attention.

### Le cas `upsert_fact` (*update-or-insert*)

```python
row = s.get(MemoryFact, (user_id, key))   # existe déjà ?
if row is None:
    s.add(MemoryFact(...))                 # non → insérer
else:
    row.value = value                      # oui → écraser
```

« Marc prend du L » puis « finalement du XL » → **une seule ligne**, mise à jour.
C'est ce qui distingue un fait (état courant) d'un épisode (historique).

### Le cas `forget` (droit à l'oubli)

```python
if needle in e.content.lower():
    s.delete(e)
    removed += 1
```

On cherche le mot cible (`adresse`), insensible à la casse, dans les faits **et**
les épisodes de *cet* utilisateur ; on supprime ; on **compte**. Le compteur
n'est pas cosmétique : le test exige `removed >= 1`, une preuve qu'un effacement a
bien eu lieu — pas un `forget` qui ment.

---

## 6. Ce que le round-trip a prouvé

Le script de validation testait, ligne à ligne, une exigence du cahier des
charges :

| Sortie observée | Ce que ça démontre |
|-----------------|--------------------|
| `facts u1 : {'pointure': 'XL'}` | l'upsert écrase (fait ≠ empilement) |
| `facts u2 : {'pointure': 'M'}` | **isolation** : u2 ne voit pas u1 (R3) |
| `b.facts(...)` voit ce que `a` a écrit | **persistance multi-session** (R2) |
| `forget adr: 1` puis « rue des Lilas » absent | **droit à l'oubli** (R5) |

---

## 7. Ce qui manque encore (volontairement)

Ce palier ne fait **que ranger**. Aujourd'hui `episodes()` rend **tout**
l'historique, brut. Il manque encore :

- **la lecture intelligente** : ne remonter que le pertinent ;
- **la tenue du budget de tokens** (`token_budget=2000`) ;
- **la fusion** faits + souvenirs dans un contexte injectable.

Ce cerveau-là, c'est `MemoryManager` :

- **Palier 2** — les faits (`remember_fact`, `inspect`, chargement dans `read`) →
  valide `test_cross_session_persistence` et `test_isolation_between_customers`.
- **Palier 3** — l'épisodique (`write`, récupération par recouvrement lexical,
  `forget`, budget) → valide `test_recall_over_30_turns` et
  `test_right_to_be_forgotten`.

### Point d'accroche pour plus tard

L'interface (`facts` / `episodes` / `forget`) est pensée pour qu'un étage
épisodique **sémantique (Chroma)** vienne se brancher derrière, sans toucher au
reste : on remplacerait la récupération lexicale par une recherche par
similarité, en gardant la même signature.
