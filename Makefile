# One entry point for both toolchains: pnpm + Turborepo (TypeScript) and uv (Python).
# Recipes stay shell-neutral: from PowerShell make runs them with cmd.exe, from Git Bash or CI with sh.

-include .env
export
# Only dev-ts (gateway and web) gets the approver key; the Python services, tests and tools never do.
unexport APPROVER_API_KEY
# Only the agent gets the model provider keys; tests use fake models and never see them.
unexport GEMINI_API_KEY NVIDIA_API_KEY OPENROUTER_API_KEY ANTHROPIC_API_KEY
# Only the eval harness gets the sim's admin address: its state names each scenario's answer.
unexport SIM_ADMIN_URL

COMPOSE = docker compose -f infra/docker-compose.yml

.PHONY: install infra-up infra-down dev dev-ts dev-agent dev-executor dev-sim \
	build test test-int lint typecheck codegen contracts-check contracts-breaking migrate seed eval \
	scenarios-check dlq-replay embed-model runbooks smoke

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

# The agent's reloader watches the whole repo; for evals pass AGENT_RELOAD= so no edit restarts it.
AGENT_RELOAD ?= --reload
dev-agent: export GEMINI_API_KEY := $(GEMINI_API_KEY)
dev-agent: export NVIDIA_API_KEY := $(NVIDIA_API_KEY)
dev-agent: export OPENROUTER_API_KEY := $(OPENROUTER_API_KEY)
dev-agent: export ANTHROPIC_API_KEY := $(ANTHROPIC_API_KEY)
dev-agent:
	uv run agent $(AGENT_RELOAD)

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
# safe.directory: git in the container refuses a .git owned by another user, as on CI runners.
OASDIFF = docker run --rm --network none -w /repo \
	-e GIT_CONFIG_COUNT=1 -e GIT_CONFIG_KEY_0=safe.directory -e GIT_CONFIG_VALUE_0=/repo \
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

# The eval (evals/harness/eval.py) against the running stack: make dev with AI_ENABLED=true and
# LLM_FALLBACK_ON_RATE_LIMIT=false. SPLIT=dev|test, SUBSET=ci|full, RUNS (1 for the ci subset,
# else 3); ESTIMATE=1 only estimates the quota days, SET_FLOORS=1 writes the scope's floors.
# Exit 1: a floor or gate broke; 2: the day's quota ran out, run it again tomorrow to go on.
SPLIT ?= dev
SUBSET ?= full
RUNS ?= $(if $(filter ci,$(SUBSET)),1,3)
eval: export SIM_ADMIN_URL := $(SIM_ADMIN_URL)
eval:
	uv run python evals/harness/eval.py --split $(SPLIT) --subset $(SUBSET) --runs $(RUNS) \
		$(if $(ESTIMATE),--estimate) $(if $(SET_FLOORS),--set-floors) \
		$(if $(ALLOW_LOOSER),--allow-looser)

# Every scenario set passes the validator, and a released set never changes: a file of a
# v<N> set that exists at SCENARIOS_BASE (default HEAD; CI passes the target branch) must be
# unchanged. A fix ships as a new set.
SCENARIOS_BASE ?= HEAD
scenarios-check:
	uv run python -m sim.validate $(wildcard evals/scenarios/v*/)
	git diff --exit-code --stat --diff-filter=MD $(SCENARIOS_BASE) -- "evals/scenarios/v*/*"

# The embedding model for search_runbooks, into FASTEMBED_CACHE_PATH; the only command that
# downloads it (about 0.5 GB). Everything else loads it offline.
embed-model:
	uv run python -m agent.runbooks download

# Embeds runbooks/ into agent.runbook_chunks.
runbooks:
	uv run python -m agent.runbooks ingest

# One scenario against the running stack (make dev with AI_ENABLED=true); SCENARIO=bad-deploy-1.
SCENARIO ?= bad-deploy-1
smoke: export SIM_ADMIN_URL := $(SIM_ADMIN_URL)
smoke:
	uv run python evals/harness/run.py $(SCENARIO)

# Replays every failure store: parked gateway outbox messages (the running gateway sends them),
# agent-events-dlq.fifo back into agent-events.fifo (the running gateway stores them), then
# investigations-dlq.fifo and the agent's dead_letters rows back into investigations.fifo.
dlq-replay:
	pnpm --filter @triage/gateway outbox:replay
	pnpm --filter @triage/gateway events:replay
	uv run python -m agent.dlq replay
