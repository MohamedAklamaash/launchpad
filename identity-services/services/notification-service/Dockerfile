FROM public.ecr.aws/docker/library/node:22-alpine AS deps
WORKDIR /app
RUN corepack enable && corepack prepare pnpm@10.14.0 --activate
COPY package.json pnpm-workspace.yaml pnpm-lock.yaml ./
COPY packages/common/package.json ./packages/common/
COPY services/notification-service/package.json ./services/notification-service/
RUN pnpm install --frozen-lockfile

FROM deps AS build
COPY tsconfig.json tsconfig.base.json ./
COPY packages/common ./packages/common
RUN pnpm --filter @launchpad/common build

COPY services/notification-service ./services/notification-service
RUN pnpm --filter notification-service build

FROM public.ecr.aws/docker/library/node:22-alpine AS final
WORKDIR /app
RUN corepack enable && corepack prepare pnpm@10.14.0 --activate
COPY package.json pnpm-workspace.yaml pnpm-lock.yaml ./
COPY packages/common/package.json ./packages/common/
COPY services/notification-service/package.json ./services/notification-service/
RUN pnpm install --frozen-lockfile --prod

COPY --from=build /app/packages/common/dist ./packages/common/dist
COPY --from=build /app/services/notification-service/dist ./services/notification-service/dist

WORKDIR /app/services/notification-service
EXPOSE 5003
CMD ["node", "dist/index.js"]
