# ── Stage 1: Build React SPA ─────────────────────────────────────────────────
FROM node:20-alpine AS builder

WORKDIR /ui
COPY package.json ./
RUN npm install

COPY . .
RUN npm run build

# ── Stage 2: Serve static files + proxy API in Docker network ───────────────
FROM nginx:1.27-alpine

COPY --from=builder /ui/dist /usr/share/nginx/html
COPY nginx.conf /etc/nginx/conf.d/default.conf

EXPOSE 3000
CMD ["nginx", "-g", "daemon off;"]
