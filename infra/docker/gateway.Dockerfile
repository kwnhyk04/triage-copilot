# The gateway runs its TypeScript on Node 24's type stripping, which refuses files under
# node_modules, so the workspace layout stays and its workspace packages resolve to their folders.
# Built from the repo root: docker build -f infra/docker/gateway.Dockerfile .
# node:24-slim
FROM node:24-slim@sha256:a5f4366e834695d5f363b920c104953a535d3a9864d1bdf74566a0ebe01daee5
ENV COREPACK_ENABLE_DOWNLOAD_PROMPT=0
RUN corepack enable pnpm && mkdir /repo && chown node:node /repo
USER node
WORKDIR /repo
COPY --chown=node:node . .
# The gateway's full dependency set: drizzle-kit, a devDependency, runs db:migrate.
RUN pnpm install --frozen-lockfile --filter @triage/gateway...
WORKDIR /repo/apps/gateway
CMD ["node", "--import", "./src/telemetry.ts", "src/main.ts"]
