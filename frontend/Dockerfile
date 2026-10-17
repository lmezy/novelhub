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

# Listen port is templated into nginx.conf at container start (the nginx
# entrypoint runs envsubst on /etc/nginx/templates/*.template). Default keeps
# the historical port 5173 so the local bridge-network compose is unchanged;
# the NAS host-network deployment sets FRONTEND_PORT=15173.
ENV FRONTEND_PORT=5173

COPY --from=build /app/dist /usr/share/nginx/html
COPY nginx.conf /etc/nginx/templates/default.conf.template

EXPOSE 5173
