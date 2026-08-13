.DEFAULT_GOAL := help
COMPOSE := docker compose

.PHONY: help up down logs migrate revision test test-cov lint fmt codegen clean

help: ## Show this help
	@grep -hE '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) \
		| awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-12s\033[0m %s\n", $$1, $$2}'

up: ## Start the full stack (api, worker, web, postgres, redis)
	@test -f .env || cp .env.example .env
	$(COMPOSE) up --build

down: ## Stop the stack and remove volumes
	$(COMPOSE) down -v

logs: ## Tail logs from the api and worker
	$(COMPOSE) logs -f api worker

migrate: ## Apply migrations inside the stack
	$(COMPOSE) run --rm migrate

revision: ## Autogenerate a migration: make revision m="add foo"
	$(COMPOSE) run --rm migrate alembic revision --autogenerate -m "$(m)"

test: ## Run the backend suite (offline; database tests skip)
	cd backend && pytest -m "not live"

test-cov: ## Run the backend suite with coverage
	cd backend && pytest -m "not live" --cov --cov-report=term-missing

lint: ## Lint backend and frontend
	cd backend && ruff check .
	cd frontend && npm run lint && npm run typecheck

fmt: ## Autofix lint findings
	cd backend && ruff check --fix . && ruff format .

codegen: ## Regenerate frontend/lib/errors.ts from the Python taxonomy
	cd backend && python scripts/gen_error_codes.py

clean: ## Remove caches and build output
	find . -type d -name __pycache__ -prune -exec rm -rf {} +
	rm -rf backend/.pytest_cache backend/.ruff_cache backend/.coverage frontend/.next
