# ==============================================================================
# Enterprise Knowledge Assistant — Frontend Dockerfile (React 18 + TypeScript + Vite)
# Multi-stage build: Node 20 Alpine builder -> Nginx 1.27 Alpine SPA + /api proxy
# Build context: repository root (".") as configured in docker-compose.yml
# ==============================================================================

# ------------------------------------------------------------------------------
# Stage 1: Build React + TypeScript Single Page Application
# ------------------------------------------------------------------------------
FROM node:20-alpine AS builder

WORKDIR /app

ARG VITE_API_BASE_URL=/api/v1
ENV VITE_API_BASE_URL=${VITE_API_BASE_URL}

# Copy only package manifests first for clean Linux npm install & layer caching
# (Immune to host Windows node_modules/ or dist/ contamination)
COPY frontend/package.json frontend/package-lock.json* /app/

RUN if [ -f package-lock.json ]; then \
        npm ci --no-audit --no-fund; \
    else \
        npm install --no-audit --no-fund; \
    fi

# Copy explicit frontend configuration and source files
COPY frontend/index.html frontend/tsconfig.json frontend/tsconfig.node.json frontend/vite.config.ts /app/
COPY frontend/src /app/src

RUN npm run build

# ------------------------------------------------------------------------------
# Stage 2: Production Nginx Server (SPA + Reverse Proxy to backend:8000)
# ------------------------------------------------------------------------------
FROM nginx:1.27-alpine AS runtime

LABEL maintainer="Enterprise Knowledge Assistant DevOps" \
      service="frontend"

RUN apk add --no-cache curl

# Copy built static assets from builder stage & Nginx configuration
COPY --from=builder /app/dist /usr/share/nginx/html
COPY infra/nginx.conf /etc/nginx/conf.d/default.conf

EXPOSE 80

HEALTHCHECK --interval=15s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -fsS http://localhost:80/healthz >/dev/null || exit 1

CMD ["nginx", "-g", "daemon off;"]
