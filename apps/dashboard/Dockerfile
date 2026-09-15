FROM node:22-slim AS deps
WORKDIR /app
COPY apps/dashboard/package.json apps/dashboard/package-lock.json ./
RUN npm ci

FROM node:22-slim AS builder
WORKDIR /app
COPY --from=deps /app/node_modules ./node_modules
COPY apps/dashboard/ ./

# Build-time-only placeholders so any module-level env reads don't crash
# `next build`'s static analysis; real secrets are injected at container
# runtime via docker-compose's env_file, never baked into the image.
ENV THRESHOLD_API_PASSWORD=build-placeholder
ENV SESSION_SECRET=build-placeholder-build-placeholder-build
RUN npm run build

FROM node:22-slim AS runner
WORKDIR /app
ENV NODE_ENV=production
COPY --from=builder /app/public ./public
COPY --from=builder /app/.next/standalone ./
COPY --from=builder /app/.next/static ./.next/static

EXPOSE 3000
CMD ["node", "server.js"]
