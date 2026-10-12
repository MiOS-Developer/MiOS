# /usr/share/mios/webtools/firecrawl.Containerfile
# 'MiOS' firecrawl image -- self-contained build of firecrawl v1.0.0's apps/api.
#
# Operator directive 2026-05-24 "make it a pod" + "built from firecrawl's OWN
# apps/api/Dockerfile, pinned v1.0.0 (their tested image, NOT a hand-rolled
# multi-runtime)". This is the "thin wrapper that clones v1.0.0 + builds
# apps/api" option: it CLONES the pinned tag and then reproduces firecrawl's
# OWN apps/api/Dockerfile stages VERBATIM (base -> prod-deps -> build ->
# go-base -> final), so the resulting image is byte-for-byte the build path
# firecrawl ships and tests, with two MiOS-only deltas called out inline.
#
# Why a wrapper rather than `podman build -f apps/api/Dockerfile apps/api`:
#   * The upstream apps/api/Dockerfile assumes the build CONTEXT is the
#     apps/api directory (it does `COPY . /app`; its pnpm-lock.yaml +
#     src/lib/go-html-to-md both live under apps/api). You COULD clone the repo
#     and `podman build -f apps/api/Dockerfile <repo>/apps/api`, but the
#     upstream Dockerfile also uses BuildKit `--mount=type=secret,id=SENTRY...`
#     which requires `--secret` plumbing we don't have offline. This wrapper
#     does the clone INSIDE the build + drops the secret mount (we always build
#     no-sentry), so it builds with a PLAIN `podman build` -- no BuildKit
#     secret, no context juggling, fully offline after the git clone.
#
# Build (see the design report for the full ordered command):
#   podman build --no-cache \
#     -f usr/share/mios/webtools/firecrawl.Containerfile \
#     -t localhost/mios-firecrawl:v1.0.0 \
#     usr/share/mios/webtools
# Override the tag at build with --build-arg FIRECRAWL_REF=<tag>.

# Pinned firecrawl release. v1.0.0 is the LAST simple architecture
# (redis + api + worker + optional playwright-service); main/v2+ added
# RabbitMQ + Postgres (nuq) which is NOT co-locatable cleanly.
ARG FIRECRAWL_REF=v1.0.0

# ── Stage 0: fetch -- shallow clone the pinned tag, expose apps/api as /ctx ──
# node:20-slim is firecrawl's own apps/api base image; reuse it so the clone +
# all build stages share one base layer.
FROM docker.io/library/node:20-slim AS fetch
ARG FIRECRAWL_REF
# Force apt + glibc over IPv4 (MiOS build host = WSL2, whose NAT has no working
# IPv6). deb.debian.org / github.com resolve to IPv6 (fastly) addresses; without
# this the apt fetch + git clone hang/fail with "does not have a Release file"
# (operator 2026-06-12).
RUN printf 'Acquire::ForceIPv4 "true";\n' > /etc/apt/apt.conf.d/99force-ipv4 \
    && printf 'label ::1/128 0\nlabel ::/0 1\nlabel 2002::/16 2\nlabel ::/96 3\nlabel ::ffff:0:0/96 4\nprecedence ::1/128 50\nprecedence ::/0 40\nprecedence 2002::/16 30\nprecedence ::/96 20\nprecedence ::ffff:0:0/96 100\n' >> /etc/gai.conf
RUN apt-get update -qq \
    && apt-get install --no-install-recommends -y git ca-certificates \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /clone
RUN git clone --depth 1 --branch "${FIRECRAWL_REF}" \
        https://github.com/mendableai/firecrawl.git . \
    # /ctx becomes the EXACT build context firecrawl's apps/api/Dockerfile
    # expects (its own directory, where COPY . /app + pnpm-lock.yaml live).
    && cp -a apps/api /ctx

# ── Stage 1: base -- VERBATIM from firecrawl v1.0.0 apps/api/Dockerfile ─────
FROM docker.io/library/node:20-slim AS base
# Force apt + glibc over IPv4 (WSL2 NAT has no IPv6); inherited by the build +
# final stages so their apt steps don't hang on deb.debian.org's IPv6 address.
RUN printf 'Acquire::ForceIPv4 "true";\n' > /etc/apt/apt.conf.d/99force-ipv4 \
    && printf 'label ::1/128 0\nlabel ::/0 1\nlabel 2002::/16 2\nlabel ::/96 3\nlabel ::ffff:0:0/96 4\nprecedence ::1/128 50\nprecedence ::/0 40\nprecedence 2002::/16 30\nprecedence ::/96 20\nprecedence ::ffff:0:0/96 100\n' >> /etc/gai.conf
# Same WSL2-no-IPv6 reason for Node's resolver: pnpm install would otherwise
# try registry.npmjs.org's AAAA address first and stall. ipv4first is honoured
# by NODE_OPTIONS since Node 16.4 (operator 2026-06-12).
ENV NODE_OPTIONS="--dns-result-order=ipv4first"
ENV PNPM_HOME="/pnpm"
ENV PATH="$PNPM_HOME:$PATH"
LABEL fly_launch_runtime="Node.js"
# Pin pnpm 9 explicitly instead of `corepack enable`: corepack pulls the LATEST
# pnpm (11.3.0), which requires Node 22's built-in node:sqlite and crashes on
# this Node 20 base (ERR_UNKNOWN_BUILTIN_MODULE). pnpm 9 runs on Node 20 and
# reads firecrawl v1.0.0's lockfile. (npm-global, not corepack, so no
# packageManager-field re-download of 11.3.0.)
RUN npm install -g pnpm@9.15.4
# Upstream does `COPY . /app` from the apps/api context; we instead copy the
# cloned apps/api tree from the fetch stage (same bytes, no host context dep).
COPY --from=fetch /ctx /app
WORKDIR /app

# ── Stage 2: prod-deps -- VERBATIM (production node_modules) ────────────────
FROM base AS prod-deps
RUN --mount=type=cache,id=pnpm,target=/pnpm/store pnpm install --prod --frozen-lockfile

# ── Stage 3: build -- VERBATIM EXCEPT the Sentry secret mount is dropped ────
# Upstream: a `--mount=type=secret,id=SENTRY_AUTH_TOKEN` gates build vs
# build:nosentry. We ALWAYS build no-sentry (offline, no token), so we call
# `pnpm run build:nosentry` (= `tsc`) directly -- no BuildKit secret needed.
FROM base AS build
RUN --mount=type=cache,id=pnpm,target=/pnpm/store pnpm install --frozen-lockfile
RUN apt-get update -qq && apt-get install -y ca-certificates && update-ca-certificates
RUN pnpm install
RUN pnpm run build:nosentry

# ── Stage 4: go-base -- VERBATIM (compile the html-to-markdown shared lib) ──
FROM docker.io/library/golang:1.19 AS go-base
# WSL2 has no working IPv6: make Go use libc's resolver + prefer IPv4 so
# `go mod tidy` doesn't stall on proxy.golang.org's IPv6 address.
RUN printf 'label ::1/128 0\nlabel ::/0 1\nlabel 2002::/16 2\nlabel ::/96 3\nlabel ::ffff:0:0/96 4\nprecedence ::1/128 50\nprecedence ::/0 40\nprecedence 2002::/16 30\nprecedence ::/96 20\nprecedence ::ffff:0:0/96 100\n' >> /etc/gai.conf
ENV GODEBUG=netdns=cgo
COPY --from=fetch /ctx/src/lib/go-html-to-md /app/src/lib/go-html-to-md
RUN cd /app/src/lib/go-html-to-md && \
    go mod tidy && \
    go build -o html-to-markdown.so -buildmode=c-shared html-to-markdown.go && \
    chmod +x html-to-markdown.so

# Stage 5 keeps the upstream Node 20/pnpm and application builds, on the global
# MiOS native terminal/MCP base. The original build stages above are retained.
FROM localhost/mios-base:latest
RUN dnf5 install -y chromium libstdc++ && dnf5 clean all
COPY --from=base /usr/local/ /usr/local/
COPY --from=base /pnpm/ /pnpm/
ENV PNPM_HOME=/pnpm PATH=/pnpm:/usr/local/bin:$PATH NODE_OPTIONS=--dns-result-order=ipv4first
WORKDIR /app
COPY --from=prod-deps /app/node_modules /app/node_modules
COPY --from=build /app /app
COPY --from=go-base /app/src/lib/go-html-to-md/html-to-markdown.so /app/dist/src/lib/go-html-to-md/html-to-markdown.so

EXPOSE 8080
ENV PUPPETEER_EXECUTABLE_PATH="/usr/bin/chromium"
# NOTE: firecrawl v1.0.0's image sets NO CMD ("Start the server by default,
# this can be overwritten at runtime"). The pod members supply the command:
#   api    -> pnpm run start:production   (mios-webtools-firecrawl-api.container)
#   worker -> pnpm run workers            (mios-webtools-firecrawl-worker.container)
# WORKDIR is /app (set in the base stage) -- where pnpm + dist/ live.
