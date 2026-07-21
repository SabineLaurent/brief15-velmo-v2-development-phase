# Makefile — porte d'entrée unique des commandes du projet.
# Tape `make` (ou `make help`) pour voir toutes les cibles disponibles.

.DEFAULT_GOAL := help

# Toutes les commandes Python passent par uv (env reproductible).
UV := uv

.PHONY: help setup install run eval latency reindex test lint format check clean

# Index vectoriel FAQ (doit rester aligné sur KNOWLEDGE_INDEX_DIR / config.py).
KNOWLEDGE_INDEX_DIR ?= ./TEMP/database/chroma

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

eval: ## Évalue l'agent sur LangSmith (dataset + evaluators)
	$(UV) run python -m support_agent.eval.run

latency: ## Mesure le TTFT + la durée par nœud (cf. docs/latence.md)
	$(UV) run python -m support_agent.latency

reindex: ## Force la reconstruction de l'index FAQ (app arrêtée)
	@rm -rf "$(KNOWLEDGE_INDEX_DIR)"
	@echo "→ index FAQ supprimé ($(KNOWLEDGE_INDEX_DIR)) : il sera reconstruit au prochain démarrage."
	@echo "  (l'empreinte vit dans ce dossier, elle part avec — rien à nettoyer d'autre)"

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
