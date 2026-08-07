FROM node:22-alpine AS build

WORKDIR /app

COPY frontend/package.json frontend/package-lock.json ./

RUN npm ci

COPY frontend/ ./

RUN npm run build


FROM nginx:alpine

ENV NGINX_ENVSUBST_FILTER=AUNERON_API_KEY

COPY frontend/nginx/default.conf.template \
    /etc/nginx/templates/default.conf.template

COPY --from=build /app/dist /usr/share/nginx/html

EXPOSE 80
