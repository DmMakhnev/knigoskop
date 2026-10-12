# syntax=docker/dockerfile:1
FROM node:22-alpine
WORKDIR /app
COPY package*.json ./
RUN --mount=type=secret,id=trust_bundle \
    if [ -f /run/secrets/trust_bundle ]; then export NODE_EXTRA_CA_CERTS=/run/secrets/trust_bundle; fi; \
    npm ci
COPY . .
RUN chown -R node:node /app
USER node
CMD ["npm", "run", "dev", "--", "--port", "5173"]
