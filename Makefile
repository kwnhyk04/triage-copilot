# One entry point for both toolchains: pnpm + Turborepo (TypeScript) and uv (Python).
# Recipes stay shell-neutral: from PowerShell make runs them with cmd.exe, from Git Bash or CI with sh.

-include .env
export
# Only dev-ts (gateway and web) gets the approver key; the Python services, tests and tools never do.
unexport APPROVER_API_KEY

COMPOSE = docker compose -f infra/docker-compose.yml

.PHONY: install infra-up infra-down dev dev-ts dev-agent dev-executor dev-sim \
	build test test-int lint typecheck codegen contracts-check contracts-breaking migrate seed eval

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
dev-ts: export APPROVER_API_KEY := $(APPROVER_API_KEY)
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
	uv run python packages/py-clients/scripts/codegen.py

# Fails when codegen changes any generated file, so stale or hand-edited output never lands.
contracts-check:
	uv run python scripts/contracts_check.py $(MAKE) codegen

# Fails on breaking changes against CONTRACTS_BASE, a git ref (CI passes the target branch):
# breaking OpenAPI changes, or an edited or deleted message version (changes ship as a new version).
CONTRACTS_BASE ?= HEAD
# oasdiff sees only the specs and the git history, offline; .env stays out of the container.
OASDIFF = docker run --rm --network none -w /repo \
	--mount "type=bind,src=$(CURDIR)/.git,dst=/repo/.git,readonly" \
	--mount "type=bind,src=$(CURDIR)/contracts,dst=/repo/contracts,readonly" \
	tufin/oasdiff:v1.33.0

# Stops Git Bash from rewriting the container paths; cmd.exe ignores it.
contracts-breaking: export MSYS_NO_PATHCONV = 1
contracts-breaking:
	$(OASDIFF) breaking $(CONTRACTS_BASE):contracts/openapi/gateway.yaml contracts/openapi/gateway.yaml --fail-on ERR
	$(OASDIFF) breaking $(CONTRACTS_BASE):contracts/openapi/agent.yaml contracts/openapi/agent.yaml --fail-on ERR
	git diff --exit-code --stat --diff-filter=MD $(CONTRACTS_BASE) -- "contracts/messages/*.v*.schema.json"

migrate: infra-up
	pnpm --filter @triage/gateway db:migrate
	uv run alembic -c services/agent/alembic.ini upgrade head

# Fake history for both schemas. SEED_ARGS=--reset replaces existing rows; --large adds 2M steps.
seed:
	uv run python scripts/seed.py $(SEED_ARGS)

eval:
	@echo eval: no harness until build step 6, AI pipeline
