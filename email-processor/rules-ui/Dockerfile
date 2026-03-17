# ── Stage 1: Build React SPA ─────────────────────────────────────────────────
FROM node:20-alpine AS builder

WORKDIR /ui
COPY package.json ./
RUN npm install

COPY . .
RUN npm run build

# ── Stage 2: Serve static files ──────────────────────────────────────────────
FROM node:20-alpine

RUN npm install -g serve

COPY --from=builder /ui/dist /app

EXPOSE 3000
CMD ["serve", "-s", "/app", "-l", "3000"]
