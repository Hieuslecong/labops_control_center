# LabOps Standard Workspace Image (Debian 13 + OpenSSH Unprivileged + Python/Git/Node/micromamba)
# KHÔNG cài sudo; container chạy hoàn toàn bằng UID/GID riêng (>= 20000), CapDrop=ALL, no-new-privileges.
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
RUN curl -Ls https://micro.mamba.pm/api/micromamba/linux-64/latest | tar -xvj -C /usr/local bin/micromamba \
    && chmod 0755 /usr/local/bin/micromamba

COPY entrypoint.sh /usr/local/bin/labops-workspace-entrypoint.sh
RUN chmod 0755 /usr/local/bin/labops-workspace-entrypoint.sh

EXPOSE 2222

WORKDIR /workspace
ENTRYPOINT ["/usr/local/bin/labops-workspace-entrypoint.sh"]
