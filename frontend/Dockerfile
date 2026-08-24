# Phase 1: build the React SPA, serve with nginx (API proxy + SSE unbuffering).
FROM node:24-slim AS build
WORKDIR /app
# install deps in their own layer for cache efficiency (no lockfile by project choice)
COPY package.json ./
RUN npm install --no-audit --no-fund
COPY . .
RUN npm run build

FROM nginx:1.27-alpine
COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/dist /usr/share/nginx/html
# 3000 = locked tunnel target (see docker-compose.yml)
EXPOSE 3000
