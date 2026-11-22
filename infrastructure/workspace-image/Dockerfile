# LabOps Standard Workspace Image (Debian 13 + OpenSSH Unprivileged + Python/Git/Node/micromamba)
# KHÔNG cài sudo; container chạy hoàn toàn bằng UID/GID riêng (>= 20000), CapDrop=ALL, no-new-privileges.
ARG GO_BUILDER_IMAGE=golang:1.26.11-trixie
FROM ${GO_BUILDER_IMAGE} AS cli
WORKDIR /build
COPY go/go.mod go/go.sum ./
COPY go/internal/gitaiotcli/cli.go ./internal/gitaiotcli/cli.go
COPY go/cmd/aiotgit/main.go ./cmd/aiotgit/main.go
RUN CGO_ENABLED=0 go build -trimpath -buildvcs=false -mod=readonly -o /out/aiotgit ./cmd/aiotgit

FROM debian:trixie-slim

ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update && apt-get install -y --no-install-recommends \
    bash \
    ca-certificates \
    curl \
    git \
    nodejs \
    npm \
    openssh-client \
    openssh-server \
    python3 \
    python3-pip \
    python3-venv \
    bzip2 \
    && (apt-get purge -y sudo || true) \
    && rm -f /usr/bin/sudo /etc/sudoers \
    && rm -rf /var/lib/apt/lists/* \
    && mkdir -p /run/sshd /etc/labops-ssh \
    && chmod 1777 /tmp /var/tmp

# Cài micromamba trong /usr/local/bin để Researcher tạo môi trường user-space không cần sudo
ARG TARGETARCH
RUN case "${TARGETARCH}" in amd64) platform=linux-64 ;; arm64) platform=linux-aarch64 ;; *) exit 1 ;; esac \
    && curl --fail --location --silent --show-error "https://micro.mamba.pm/api/micromamba/${platform}/latest" -o /tmp/micromamba.tar.bz2 \
    && tar -xj -f /tmp/micromamba.tar.bz2 -C /usr/local bin/micromamba \
    && rm /tmp/micromamba.tar.bz2 \
    && chmod 0755 /usr/local/bin/micromamba

COPY --from=cli --chmod=0755 /out/aiotgit /usr/local/bin/aiotgit
COPY --chmod=0755 infrastructure/workspace-image/git-wrapper.sh /usr/local/bin/git
COPY --chmod=0755 infrastructure/workspace-image/entrypoint.sh /usr/local/bin/labops-workspace-entrypoint.sh
RUN ln -s aiotgit /usr/local/bin/gitaiot

EXPOSE 2222

WORKDIR /workspace
ENTRYPOINT ["/usr/local/bin/labops-workspace-entrypoint.sh"]
