FROM node:22-alpine


WORKDIR /app


# ``package*.json`` matches both package.json and the tracked
# package-lock.json, and a COPY with several sources needs a directory
# destination that says so -- ``.`` alone is rejected by current BuildKit
# ("When using COPY with more than one source file, the destination must be a
# directory and end with a /").
COPY package*.json ./


RUN npm config set registry https://registry.npmmirror.com && npm install


COPY . .


CMD ["npm","run","dev"]

