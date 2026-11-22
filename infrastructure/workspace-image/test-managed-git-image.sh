#!/bin/bash
set -euo pipefail

host=${LABOPS_STAGING_DOCKER_HOST:-}
case "$host" in
    unix:///*) ;;
    *) echo "BLOCKED_SAFETY: configure a dedicated staging Docker socket" >&2; exit 77 ;;
esac
socket=$(realpath -e -- "${host#unix://}") || {
    echo "BLOCKED_SAFETY: staging Docker socket is unavailable" >&2
    exit 77
}
if [[ ! -S "$socket" || $socket == /run/docker.sock || $socket == /run/docker/* ]]; then
    echo "BLOCKED_SAFETY: production Docker socket is forbidden" >&2
    exit 77
fi

root=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)
unset DOCKER_CONTEXT DOCKER_HOST BUILDKIT_HOST BUILDX_BUILDER
docker=(docker --host "unix://$socket")
tag="labops-managed-git-staging:smoke"
DOCKER_BUILDKIT=1 "${docker[@]}" build -f "$root/infrastructure/workspace-image/Dockerfile" -t "$tag" "$root"
image=$("${docker[@]}" image inspect --format '{{.Id}}' "$tag")
[[ $image =~ ^sha256:[a-f0-9]{64}$ ]]
"${docker[@]}" run --rm --user 20000:20000 --cpus 1 --memory 256m --pids-limit 64 \
    --cap-drop ALL --security-opt no-new-privileges --network none --entrypoint /bin/bash "$image" -ec '
        [[ $(id -u):$(id -g) == 20000:20000 ]]
        [[ $(command -v aiotgit) == /usr/local/bin/aiotgit ]]
        [[ $(command -v gitaiot) == /usr/local/bin/gitaiot ]]
        [[ $(command -v git) == /usr/local/bin/git ]]
        grep -q "^CapEff:[[:space:]]*0000000000000000$" /proc/self/status
        grep -q "^NoNewPrivs:[[:space:]]*1$" /proc/self/status
        [[ ! -e /var/run/docker.sock ]]
        [[ $(git --version) == "$(/usr/bin/git --version)" ]]
        aiotgit --version
        gitaiot --version
        export LABOPS_API_URL=http://127.0.0.1:1/api/v1/repositories LABOPS_TOKEN=staging-fixture
        if git clone https://github.com/owner/repo.git /tmp/clone-out; then exit 1; fi
        [[ ! -e /tmp/clone-out ]]
    '
