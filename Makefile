# One entry point for both toolchains: pnpm + Turborepo (TypeScript) and uv (Python).
# Recipes stay shell-neutral: from PowerShell make runs them with cmd.exe, from Git Bash or CI with sh.

-include .env
export

COMPOSE = docker compose -f infra/docker-compose.yml

.PHONY: install infra-up infra-down dev dev-ts dev-agent dev-executor dev-sim \
	build test test-int lint typecheck codegen migrate seed eval

install:
	pnpm install
	uv sync
	uv run pre-commit install

infra-up:
	$(COMPOSE) up -d --wait

# Removes this project's containers and network; the Postgres volume stays.
infra-down:
	$(COMPOSE) down

dev: infra-up
	$(MAKE) -j4 dev-ts dev-agent dev-executor dev-sim

# Loose env mode lets turbo pass the .env variables through to the dev servers.
dev-ts:
	pnpm turbo run dev --env-mode=loose

dev-agent:
	uv run agent --reload

dev-executor:
	uv run executor --reload

dev-sim:
	uv run sim --reload

build:
	pnpm turbo run build
	uv build --all-packages

test:
	pnpm turbo run test
	uv run pytest -m "not integration"

test-int: infra-up
	pnpm -r --if-present run test:int
	uv run pytest -m integration

lint:
	pnpm eslint .
	pnpm prettier --check .
	uv run ruff check .
	uv run ruff format --check .

typecheck:
	pnpm turbo run typecheck
	uv run pyright

codegen:
	pnpm --filter @triage/ts-clients codegen
	uv run datamodel-codegen --input contracts/messages --input-file-type jsonschema --output packages/py-clients/src/py_clients/messages
	uv run datamodel-codegen --input contracts/openapi/gateway.yaml --input-file-type openapi --output packages/py-clients/src/py_clients/gateway.py

migrate: infra-up
	pnpm --filter @triage/gateway db:migrate
	uv run alembic -c services/agent/alembic.ini upgrade head

seed:
	@echo seed: nothing to seed until build step 3, data

eval:
	@echo eval: no harness until build step 6, AI pipeline
