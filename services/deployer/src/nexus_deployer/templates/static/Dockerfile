# House template: a static web app (Vite or plain files) served by unprivileged nginx on 8080.
# Built with no network: node_modules come from the workbench, where the team installed them.
FROM node:22.23.4-bookworm-slim AS build
WORKDIR /app
COPY src/ ./
RUN if [ -f package.json ] && [ "$(npm pkg get scripts.build)" != "{}" ]; then \
      npm run build && test -d dist; \
    else \
      mkdir -p /tmp/site && cp -r . /tmp/site && rm -rf /tmp/site/node_modules && mv /tmp/site dist; \
    fi

FROM nginxinc/nginx-unprivileged:1.27.5-alpine
COPY --from=build /app/dist /usr/share/nginx/html
COPY nginx.conf /etc/nginx/conf.d/default.conf
USER 101
EXPOSE 8080
