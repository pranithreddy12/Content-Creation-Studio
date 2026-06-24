# Install + build in one stage so pnpm's symlinked node_modules stays intact
# (a cross-stage `COPY node_modules` breaks pnpm's .pnpm virtual store, which is
# what previously left `next/dist/bin/next` unresolvable → MODULE_NOT_FOUND).
FROM node:20-alpine AS builder
WORKDIR /app
RUN apk add --no-cache libc6-compat && npm i -g pnpm
# Copy the pnpm lockfile (the project has no package-lock.json) so the install
# is reproducible and --frozen-lockfile actually has a lockfile to honor.
COPY package.json pnpm-lock.yaml ./
RUN pnpm install --frozen-lockfile
COPY . .
RUN pnpm build

FROM node:20-alpine AS runner
WORKDIR /app
ENV NODE_ENV=production
RUN addgroup -S nodejs && adduser -S next -G nodejs
# Uses Next.js standalone output (next.config.ts: output: "standalone").
COPY --from=builder /app/.next/standalone ./
COPY --from=builder /app/.next/static ./.next/static
COPY --from=builder /app/public ./public
USER next
EXPOSE 3000
CMD ["node", "server.js"]
