# @author lxy
FROM node:20.20.2-alpine AS build

WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci --ignore-scripts --legacy-peer-deps
COPY index.html tsconfig*.json vite.config.ts ./
COPY src ./src
RUN npm run build

FROM node:20.20.2-alpine

ENV NODE_ENV=production \
    PORT=8080 \
    JAVA_BASE_URL=http://java-service:8081
WORKDIR /app
COPY --chown=node:node server.mjs ./server.mjs
COPY --from=build --chown=node:node /app/dist ./dist
USER node
EXPOSE 8080
CMD ["node", "server.mjs"]
