FROM node:22-alpine


WORKDIR /app


COPY package*.json .


RUN npm config set registry https://registry.npmmirror.com && npm install


COPY . .


CMD ["npm","run","dev"]

