# Makefile — porte d'entrée unique des commandes du projet.
# Tape `make` (ou `make help`) pour voir toutes les cibles disponibles.

.DEFAULT_GOAL := help

# Toutes les commandes Python passent par uv (env reproductible).
UV := uv

.PHONY: help setup install run serve eval latency test lint format check clean

help: ## Affiche cette aide
	@echo "Agnostic Support AI Agent — commandes disponibles :"
	@echo ""
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) \
		| awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-12s\033[0m %s\n", $$1, $$2}'
	@echo ""

setup: install ## Installe les deps ET crée le .env s'il manque
	@test -f .env || (cp .env.example .env && echo "→ .env créé depuis .env.example (pense à renseigner tes clés).")

install: ## Installe/synchronise les dépendances (uv sync)
	$(UV) sync

run: ## Lance l'agent de support
	$(UV) run python -m support_agent.agent

serve: ## Expose l'agent en HTTP (API SSE sur :8000, cf. docs/plan-deploiement-2026-07-25.md)
	$(UV) run --extra server uvicorn support_agent.server:app --reload --port 8000

eval: ## Évalue l'agent sur LangSmith (dataset + evaluators)
	$(UV) run python -m support_agent.eval.run

latency: ## Mesure le TTFT + la durée par nœud (cf. docs/latence.md)
	$(UV) run python -m support_agent.latency

test: ## Lance les tests (pytest)
	$(UV) run pytest

lint: ## Vérifie le style du code (ruff)
	$(UV) run ruff check .

format: ## Formate le code (ruff)
	$(UV) run ruff format .

check: lint test ## Contrôle qualité complet (lint + tests)

clean: ## Supprime les caches Python et outils
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	rm -rf .pytest_cache .ruff_cache .mypy_cache
	@echo "→ caches nettoyés."
