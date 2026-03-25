FROM public.ecr.aws/docker/library/node:22-alpine AS deps
WORKDIR /app
RUN corepack enable && corepack prepare pnpm@10.14.0 --activate
COPY package.json pnpm-workspace.yaml pnpm-lock.yaml ./
COPY packages/common/package.json ./packages/common/
COPY services/user-service/package.json ./services/user-service/
RUN pnpm install --frozen-lockfile

FROM deps AS build
COPY tsconfig.json tsconfig.base.json ./
COPY packages/common ./packages/common
RUN pnpm --filter @launchpad/common build

COPY services/user-service ./services/user-service
RUN pnpm --filter user-service build

FROM node:22-alpine AS final
WORKDIR /app
RUN corepack enable && corepack prepare pnpm@10.14.0 --activate
COPY package.json pnpm-workspace.yaml pnpm-lock.yaml ./
COPY packages/common/package.json ./packages/common/
COPY services/user-service/package.json ./services/user-service/
RUN pnpm install --frozen-lockfile --prod

COPY --from=build /app/packages/common/dist ./packages/common/dist
COPY --from=build /app/services/user-service/dist ./services/user-service/dist

WORKDIR /app/services/user-service
EXPOSE 5002
CMD ["node", "dist/index.js"]
