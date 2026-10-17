# ---- Build stage: compile the production bundle ----
FROM node:22-alpine AS build

WORKDIR /app

# ``package*.json`` matches both package.json and the tracked
# package-lock.json; npm ci installs exactly what the lock file pins.
COPY package*.json ./

RUN npm config set registry https://registry.npmmirror.com && npm ci

COPY . .

RUN npm run build


# ---- Run stage: serve the static bundle with nginx ----
FROM nginx:alpine

COPY --from=build /app/dist /usr/share/nginx/html
COPY nginx.conf /etc/nginx/conf.d/default.conf

# Keep the historical container port so docker-compose and the gateway
# upstream (frontend:5173) do not change.
EXPOSE 5173
