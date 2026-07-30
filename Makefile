# Makefile — porte d'entrée unique des commandes du projet.
# Tape `make` (ou `make help`) pour voir toutes les cibles, groupées par usage.
#
# Deux conventions dans ce fichier :
#   · `##  <texte>` après une cible  → la ligne d'aide de cette cible
#   · `##@ <texte>` sur sa propre ligne → un titre de section dans `make help`
# Ajouter une cible sans sa ligne `##` la rend invisible dans l'aide : c'est
# volontaire (une cible interne n'a pas à être annoncée), mais ce n'est jamais
# un oubli acceptable pour une cible destinée à l'utilisatrice.

.DEFAULT_GOAL := help

# Toutes les commandes Python passent par uv (env reproductible, un seul .venv
# partagé par les membres du workspace).
UV := uv

# ⚠️ Tenir cette liste alignée sur les cibles réelles. Une cible absente d'ici
# cesse de tourner le jour où un fichier du même nom apparaît à la racine.
.PHONY: help \
        setup install \
        run serve ui \
        test lint format check score \
        eval latency \
        seed consolidate memory \
        docker-build docker-up docker-logs docker-down \
        clean

help: ## Affiche cette aide
	@echo "Agnostic Support AI Agent — commandes disponibles :"
	@awk 'BEGIN {FS = ":.*## "} \
		/^##@ / { printf "\n\033[1m%s\033[0m\n", substr($$0, 5); next } \
		/^[a-zA-Z_-]+:.*## / { printf "  \033[36m%-14s\033[0m %s\n", $$1, $$2 }' \
		$(MAKEFILE_LIST)
	@echo ""

##@ Installation

setup: install ## Installe les deps ET crée le .env s'il manque
	@test -f .env || (cp .env.example .env && echo "→ .env créé depuis .env.example (pense à renseigner tes clés).")

install: ## Installe/synchronise les dépendances (uv sync)
	$(UV) sync

##@ Boutique de démo — une DOUBLURE jetable du SI marchand (database/README.md)

seed: ## Peuple la boutique SQL (idempotent ; ARGS=--reset pour tout reconstruire)
	$(UV) run python -m support_agent.actions.sql.seed $(ARGS)

##@ Lancer l'agent

run: ## Lance l'agent de support en CLI interactif
	$(UV) run python -m support_agent.agent

serve: ## Expose l'agent en HTTP (API SSE sur :8000, cf. docs/plan-deploiement-2026-07-25.md)
	$(UV) run --extra server uvicorn support_agent.server:app --reload --port 8000

ui: ## Lance l'UI Chainlit sur :8001 (dev local ; la version conteneur est sur :8101)
	@echo "→ l'UI appelle $${AGENT_API_URL:-http://localhost:8000} ; lance 'make serve' dans un autre terminal si ce n'est pas fait."
	$(UV) run chainlit run packages/client/src/client_chainlit/app.py -w --port 8001

##@ Qualité — hors ligne, gratuit, sans clé API (ce que la CI fait tourner)

test: ## Lance les tests (pytest)
	$(UV) run pytest

lint: ## Vérifie le style du code (ruff)
	$(UV) run ruff check .

format: ## Formate le code (ruff)
	$(UV) run ruff format .

check: lint test ## Contrôle qualité complet (lint + tests)

# Pourquoi cette cible est ici, dans la section HORS LIGNE, et pas dans « Mesure » :
# par défaut elle ne note que les deux dimensions DÉTERMINISTES (mémoire,
# garde-fous) — aucun appel LLM, aucune clé, aucun réseau. C'est précisément ce
# qui la rend utilisable comme porte de CI. `ARGS=--live` ajoute la dimension
# QUALITÉ et franchit alors la frontière : ça appelle un vrai modèle et ça coûte
# des tokens. Le seuil et la baseline : TODO_priorities.md §Chantier 7.
#
# ⭐ La dimension MÉMOIRE est notée une fois PAR MOTEUR de persistance. SQLite
# toujours ; Postgres aussi si `EVAL_DATABASE_URL` en désigne un (chantier 8) —
# R3 est l'isolation entre clients, et la requête de similarité qui pourrait
# ramener la ligne du voisin est celle du store, pas la nôtre. Sans base, le
# rapport DIT que Postgres n'a pas été exercé ; `ARGS=--require-postgres` refuse
# ce cas (c'est ce que la CI passe).
score: ## Note l'agent, écrit le rapport, BLOQUE sous le seuil (ARGS=--live|--degraded|--update-baseline|--require-postgres)
	$(UV) run python -m support_agent.eval.mlops $(ARGS)

##@ Mesure — appelle un VRAI LLM : coûte des tokens et exige les clés du .env

eval: ## Évalue l'agent sur LangSmith (dataset + evaluators)
	$(UV) run python -m support_agent.eval.run

latency: ## Mesure le TTFT + la durée par nœud (ARGS='[question] [--runs N]' ; cf. docs/latence.md)
	$(UV) run python -m support_agent.latency $(ARGS)

##@ Mémoire — tâches de maintenance, hors du tour client ; À BLANC par défaut (--write pour écrire)

consolidate: ## Distille les fils terminés en épisodes (à blanc ; ARGS=--write pour écrire)
	$(UV) run python -m support_agent.memory.consolidate $(ARGS)

memory: ## Inspecte / efface la mémoire d'un client (R5-R6) — ARGS='--user-id X [--forget "..."|--erase] [--write]'
	$(UV) run python -m support_agent.memory.audit $(ARGS)

##@ Docker — ports 81xx (disjoints du dev local en 80xx, pour ne pas confondre les deux piles)

docker-build: ## Construit les images (agent + client ; contexte = racine du repo)
	docker build -f packages/support-agent/Dockerfile -t support-agent:dev .
	docker build -f packages/client/Dockerfile -t client-chainlit:dev .

docker-up: ## Démarre la pile conteneurisée en arrière-plan (construit si besoin)
	docker compose up --build -d
	@echo "→ UI sur http://localhost:8101  ·  agent sur http://localhost:8100  (ports 81xx = conteneurs ; 80xx = dev local)"
	@echo "  (make docker-logs pour suivre le démarrage)"

docker-logs: ## Suit les logs de la pile (Ctrl-C pour sortir, les conteneurs continuent)
	docker compose logs -f

docker-down: ## Arrête la pile (le volume d'état est CONSERVÉ)
	docker compose down

##@ Entretien

clean: ## Supprime les caches Python et outils
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	rm -rf .pytest_cache .ruff_cache .mypy_cache
	@echo "→ caches nettoyés."
