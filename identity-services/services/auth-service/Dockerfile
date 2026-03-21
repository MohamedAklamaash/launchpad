FROM node:22-alpine AS deps
WORKDIR /app
RUN corepack enable && corepack prepare pnpm@10.14.0 --activate
COPY package.json pnpm-workspace.yaml pnpm-lock.yaml ./
COPY packages/common/package.json ./packages/common/
COPY services/auth-service/package.json ./services/auth-service/
RUN pnpm install --frozen-lockfile

FROM deps AS build
COPY tsconfig.json tsconfig.base.json ./
COPY packages/common ./packages/common
RUN pnpm --filter @launchpad/common build

COPY services/auth-service ./services/auth-service
RUN pnpm --filter auth-service build

FROM node:22-alpine AS final
WORKDIR /app
RUN corepack enable && corepack prepare pnpm@10.14.0 --activate
COPY package.json pnpm-workspace.yaml pnpm-lock.yaml ./
COPY packages/common/package.json ./packages/common/
COPY services/auth-service/package.json ./services/auth-service/
RUN pnpm install --frozen-lockfile --prod

COPY --from=build /app/packages/common/dist ./packages/common/dist
COPY --from=build /app/services/auth-service/dist ./services/auth-service/dist

WORKDIR /app/services/auth-service
EXPOSE 3001
CMD ["node", "dist/index.js"]
