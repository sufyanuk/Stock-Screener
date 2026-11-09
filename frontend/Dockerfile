FROM node:22-alpine AS build
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci
COPY . .
# 1 = pages only show stored data; heavy loads run on GitHub Actions (as on Vercel)
ARG JOBS_ON_GITHUB=
ENV JOBS_ON_GITHUB=$JOBS_ON_GITHUB
# where /api/* is forwarded (fixed into the build by Next.js)
ARG BACKEND_URL=http://api:8000
ENV BACKEND_URL=$BACKEND_URL
RUN npm run build

FROM node:22-alpine
WORKDIR /app
ENV NODE_ENV=production
COPY --from=build /app/.next/standalone ./
COPY --from=build /app/.next/static ./.next/static
USER node
EXPOSE 3000
CMD ["node", "server.js"]
