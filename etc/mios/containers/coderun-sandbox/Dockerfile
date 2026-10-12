# AI-hint: Defines the Dockerfile for the coderun-sandbox container, providing a restricted, Landlock-hardened environment with a pre-built toolchain (gcc, python, node) for agents to execute untrusted code safely.
# AI-related: /etc/mios/containers/coderun-sandbox/Dockerfile, /etc/mios/containers/coderun-sandbox/, mios-coderun-sandbox

FROM docker.io/library/alpine:3.20 AS build

RUN apk add --no-cache build-base linux-headers
COPY exec-init.c /build/exec-init.c
RUN gcc -static -Wall -Wextra -O2 -o /build/exec-init /build/exec-init.c \
    && strip /build/exec-init

FROM localhost/mios-base:latest

RUN dnf5 install -y --setopt=install_weak_deps=0 \
        bash coreutils \
        ripgrep fd-find \
        git git-lfs \
        python3 python3-pip python3-virtualenv \
        nodejs npm \
        make gcc glibc-devel kernel-headers \
        jq curl wget \
        file procps-ng htop \
        openssh-clients \
    && dnf5 clean all

COPY --from=build /build/exec-init /usr/local/bin/exec-init
RUN chmod 0755 /usr/local/bin/exec-init

RUN mkdir -p /work /work-upper && chmod 0755 /work /work-upper

ENV MIOS_CODE_MODE_SOCKET=/run/coderun.sock
ENV PYTHONPATH=/usr/local/lib/mios
COPY mios_tools.py /usr/local/lib/mios/mios_tools.py

CMD ["/bin/sleep", "infinity"]
