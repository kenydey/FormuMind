FROM node:22-alpine AS build
WORKDIR /app
ARG VITE_API_TOKEN=
ENV VITE_API_TOKEN=$VITE_API_TOKEN

# Install deps. Use `npm install` (not `npm ci`) because:
#  - `npm ci` unpacks every optionalDependencies entry literally (aix, netbsd,
#    win32, …) and rejects the ones that don't match the current OS/arch.
#  - esbuild's optionalDependencies list every platform; our lockfile was
#    hand-sanitized to keep ONLY linux-x64 esbuild. Rollup's native binary
#    (@rollup/rollup-linux-x64-gnu) is also optional and MUST be installed,
#    so we do NOT use --omit=optional. --legacy-peer-deps handles npm 12's
#    stricter peer dep checks.
COPY package.json package-lock.json ./
RUN npm install --legacy-peer-deps --no-audit --no-fund

# Build the production bundle.
COPY . .
RUN npm run build

FROM nginx:alpine
COPY --from=build /app/dist /usr/share/nginx/html
COPY nginx.conf /etc/nginx/conf.d/default.conf
EXPOSE 80
