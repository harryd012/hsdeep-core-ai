# HSDEEP CORE AI — Frontend Dockerfile
#
# Multi-stage build for Next.js 16 App Router production deployment.
# PRODUCTION_READINESS.md P0-4

# --- Stage 1: Dependencies ---
FROM node:24-alpine AS deps
WORKDIR /app

# Copy manifest files.
# pnpm-workspace.yaml is required: it carries the `allowBuilds` policy that
# authorizes only the dependency build scripts the app needs (sharp,
# unrs-resolver). Without it pnpm 11/12 refuses the install with
# ERR_PNPM_IGNORED_BUILDS because the lockfile's packages have
# unapproved lifecycle scripts (PRODUCTION_READINESS.md P0-4).
COPY package.json package-lock.json* pnpm-lock.yaml* pnpm-workspace.yaml* ./

# Install dependencies (prefer lockfile, fall back to npm install).
# pnpm is pinned so the install stays byte-for-byte reproducible and so a
# future pnpm major cannot silently change dependency-build policy handling.
RUN \
  if [ -f pnpm-lock.yaml ]; then npm install -g pnpm@12.6.0 && pnpm install --frozen-lockfile; \
  elif [ -f package-lock.json ]; then npm ci; \
  else npm install; \
  fi

# --- Stage 2: Builder ---
FROM node:24-alpine AS builder
WORKDIR /app

COPY --from=deps /app/node_modules ./node_modules
COPY . .

# Build-time env vars (baked into the client bundle)
ARG NEXT_PUBLIC_API_URL=http://localhost:8000
ARG NEXT_PUBLIC_DEFAULT_TENANT_ID=00000000-0000-0000-0000-000000000000
ENV NEXT_PUBLIC_API_URL=$NEXT_PUBLIC_API_URL
ENV NEXT_PUBLIC_DEFAULT_TENANT_ID=$NEXT_PUBLIC_DEFAULT_TENANT_ID
ENV NODE_ENV=production

RUN npm run build

# --- Stage 3: Runner ---
FROM node:24-alpine AS runner
WORKDIR /app

ENV NODE_ENV=production
ENV NEXT_TELEMETRY_DISABLED=1

# Create non-root user
RUN addgroup --system --gid 1001 nodejs \
    && adduser --system --uid 1001 nextjs

# Copy standalone output (server.js is at root of standalone/)
COPY --from=builder /app/.next/standalone ./
# Static assets (branding, images, media) are not part of the standalone bundle.
COPY --from=builder /app/public ./public
COPY --from=builder /app/.next/static ./.next/static

USER nextjs

EXPOSE 3000

ENV PORT=3000
ENV HOSTNAME="0.0.0.0"

# Health check must probe the IPv4 loopback explicitly: the server binds
# HOSTNAME=0.0.0.0 (IPv4 only), while inside the container `localhost`
# resolves to IPv6 [::1] first, which made the container report `unhealthy`.
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD wget --no-verbose --tries=1 --spider http://127.0.0.1:3000/ || exit 1

CMD ["node", "server.js"]

