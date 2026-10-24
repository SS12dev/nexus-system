# House template: a Node (Express) back end on 3000, with the node_modules the team installed
# in the workbench. Built with no network.
FROM node:22.23.4-bookworm-slim
WORKDIR /srv/app
COPY src/ ./
RUN test -d node_modules || { echo "No node_modules: run npm install in the back end's folder." >&2; exit 1; }; \
    if [ "$(npm pkg get scripts.build)" != "{}" ]; then npm run build; fi
ENV NODE_ENV=production PORT=3000
USER 1000
EXPOSE 3000
CMD ["npm", "start"]
