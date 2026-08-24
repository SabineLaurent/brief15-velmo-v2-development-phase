# Makefile — porte d'entrée unique des commandes du projet.
# `##  <texte>` après une cible = sa ligne d'aide ; `##@ <texte>` = un titre de
# section dans `make help`.

.DEFAULT_GOAL := help

UV := uv

.PHONY: help \
        setup install \
        run serve ui \
        test lint format check score \
        eval latency \
        seed consolidate memory \
        docker-build docker-smoke docker-up docker-logs docker-down \
        wake \
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

##@ Boutique de démo — une DOUBLURE jetable du SI marchand

seed: ## Peuple la boutique SQL (idempotent ; ARGS=--reset pour tout reconstruire)
	$(UV) run python -m support_agent.actions.sql.seed $(ARGS)

##@ Lancer l'agent

run: ## Lance l'agent de support en CLI interactif
	$(UV) run python -m support_agent.agent

serve: ## Expose l'agent en HTTP (API SSE sur :8000)
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

score: ## Note l'agent, écrit le rapport, BLOQUE sous le seuil (ARGS=--live|--degraded|--update-baseline|--require-postgres)
	$(UV) run python -m support_agent.eval.mlops $(ARGS)

##@ Mesure — appelle un VRAI LLM : coûte des tokens et exige les clés du .env

eval: ## Évalue l'agent sur LangSmith (dataset + evaluators)
	$(UV) run python -m support_agent.eval.run

latency: ## Mesure le TTFT + la durée par nœud (ARGS='[question] [--runs N]')
	$(UV) run python -m support_agent.latency $(ARGS)

##@ Mémoire — tâches de maintenance, hors du tour client ; À BLANC par défaut (--write pour écrire)

consolidate: ## Distille les fils terminés en épisodes (à blanc ; ARGS=--write pour écrire)
	$(UV) run python -m support_agent.memory.consolidate $(ARGS)

memory: ## Inspecte / efface la mémoire d'un client (R5-R6) — ARGS='--user-id X [--forget "..."|--erase] [--write]'
	$(UV) run python -m support_agent.memory.audit $(ARGS)

##@ Docker — ports 81xx (disjoints du dev local en 80xx, pour ne pas confondre les deux piles)

# Architecture cible du build. Vide = celle de la machine ; `PLATFORM=linux/amd64`
# pour une cible Linux amd64.
PLATFORM ?=
DOCKER_PLATFORM := $(if $(PLATFORM),--platform $(PLATFORM),)

docker-build: ## Construit les images (agent + client ; ARGS ignoré, PLATFORM=linux/amd64 pour Azure)
	docker build $(DOCKER_PLATFORM) -f packages/support-agent/Dockerfile -t support-agent:dev .
	docker build $(DOCKER_PLATFORM) -f packages/client/Dockerfile -t client-chainlit:dev .

docker-smoke: docker-build ## Construit PUIS vérifie les deux images (découplage + démarrage réel)
	$(UV) run --no-project python scripts/docker_smoke.py

docker-up: ## Démarre la pile conteneurisée en arrière-plan (construit si besoin)
	docker compose up --build -d
	@echo "→ UI sur http://localhost:8101  ·  agent sur http://localhost:8100  (ports 81xx = conteneurs ; 80xx = dev local)"
	@echo "  (make docker-logs pour suivre le démarrage)"

docker-logs: ## Suit les logs de la pile (Ctrl-C pour sortir, les conteneurs continuent)
	docker compose logs -f

docker-down: ## Arrête la pile (le volume d'état est CONSERVÉ)
	docker compose down

##@ Déploiement — l'agent EN LIGNE (URL via AGENT_API_URL ou ARGS)

wake: ## Réveille l'agent déployé et mesure /ready puis /health
	@scripts/wake.sh $(ARGS)

##@ Entretien

clean: ## Supprime les caches Python et outils
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	rm -rf .pytest_cache .ruff_cache .mypy_cache
	@echo "→ caches nettoyés."
