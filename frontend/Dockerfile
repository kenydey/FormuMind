FROM node:22-alpine AS build
WORKDIR /app
ARG VITE_API_TOKEN=
ENV VITE_API_TOKEN=$VITE_API_TOKEN

# Install deps from the lockfile for reproducible builds (cached layer).
COPY package.json package-lock.json ./
RUN npm ci

# Build the production bundle.
COPY . .
RUN npm run build

FROM nginx:alpine
COPY --from=build /app/dist /usr/share/nginx/html
COPY nginx.conf /etc/nginx/conf.d/default.conf
EXPOSE 80
