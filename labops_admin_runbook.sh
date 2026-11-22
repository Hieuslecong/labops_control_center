#!/usr/bin/env bash
# LabOps Workstation hardening runbook (based on Section 4.2 of handover report).
# Run as a NON-ROOT admin account on aiot-ws1; this script uses sudo selectively.
# No action is performed unless an explicit subcommand is provided.
set -Eeuo pipefail
IFS=$'\n\t'

readonly TARGET_HOST='aiot-ws1'
readonly TARGET_IP='192.168.167.251'
readonly REPO='/home/aiot-ws1/projects/labops_control_center'
readonly BRANCH='feature/storage-resource-allocation'
readonly MOUNT='/mnt/HDD3'

note() { printf '\n[LABOPS] %s\n' "$*" >&2; }
die() { printf '\n[ERROR] %s\n' "$*" >&2; exit 1; }
warn() { printf '\n[WARNING] %s\n' "$*" >&2; }

require_target() {
  [[ "$(hostname -s)" == "$TARGET_HOST" ]] || die "Wrong host: expected $TARGET_HOST; got $(hostname -s)"
  [[ "$EUID" -ne 0 ]] || die 'Run as a regular admin account, not as root; sudo is invoked explicitly.'
  ip -4 -o addr show | grep -Fq "$TARGET_IP/" || die "Expected $TARGET_IP not assigned on this host. Stop and investigate."
}

require_repo() {
  [[ -d "$REPO/.git" ]] || die "Git repository missing: $REPO"
  cd "$REPO"
  [[ "$(git branch --show-current)" == "$BRANCH" ]] || die "Wrong branch: expected $BRANCH"
}

preflight() {
  require_target
  require_repo
  sudo -v
  note 'Git status (pre-existing wg-easy/healthcheck.sh edit must NOT be committed)'
  git status --short
  git log -5 --oneline
  note 'Docker status'
  sudo docker info --format 'Server: {{.ServerVersion}}' >/dev/null || die 'Docker is inaccessible'
  sudo docker ps --format 'table {{.Names}}\t{{.Status}}\t{{.Ports}}'
  note 'Agent and WireGuard HTTP health (read-only)'
  curl --fail --silent --show-error --max-time 4 http://127.0.0.1:9100/health || warn 'Agent health is not yet healthy'
  printf '\n'
  curl --silent --output /dev/null --write-out 'wg-easy HTTP status: %{http_code}\n' --max-time 4 http://127.0.0.1:51821/login || true
  note 'Current admin and legacy SSH sockets'
  sudo ss -ltnp | grep -E ':(1912|22|22003|22004|22005)[[:space:]]' || true
  note 'Storage facts; DO NOT assume device names from previous logs'
  findmnt --mountpoint "$MOUNT" -o TARGET,SOURCE,FSTYPE,OPTIONS || true
  lsblk -o NAME,FSTYPE,UUID,SIZE,MOUNTPOINTS
  grep -nF "$MOUNT" /etc/fstab || true
  note 'Preflight complete; no changes made.'
}

image() {
  require_target
  require_repo
  sudo -v
  [[ -n "${LABOPS_REGISTRY_IMAGE_TAG:-}" ]] || die 'Set LABOPS_REGISTRY_IMAGE_TAG to an approved, fully qualified registry image:tag.'
  [[ "$LABOPS_REGISTRY_IMAGE_TAG" == */*:* ]] || die 'Expected a registry/repository:tag reference.'
  [[ "$LABOPS_REGISTRY_IMAGE_TAG" != *@* ]] || die 'Provide a push target by tag, not by digest.'
  note "Building immutable workspace image source as labops-workspace:v2"
  sudo docker build -f infrastructure/workspace-image/Dockerfile -t labops-workspace:v2 .
  sudo docker tag labops-workspace:v2 "$LABOPS_REGISTRY_IMAGE_TAG"
  note 'Pushing image to the selected registry (separate from git push).'
  sudo docker push "$LABOPS_REGISTRY_IMAGE_TAG"
  local repository expected ref
  repository="${LABOPS_REGISTRY_IMAGE_TAG%:*}"
  expected="$repository@sha256:"
  ref="$(sudo docker image inspect --format '{{range .RepoDigests}}{{println .}}{{end}}' "$LABOPS_REGISTRY_IMAGE_TAG" | awk -v prefix="$expected" 'index($0,prefix)==1 { print; exit }')"
  [[ "$ref" =~ ^.+@sha256:[0-9a-f]{64}$ ]] || die 'No matching manifest RepoDigest found. DO NOT replace it with Docker image .Id/config digest.'
  sudo docker image inspect "$ref" >/dev/null || die 'Pinned image is not resolvable on this Docker host.'
  note 'Copy these NON-SECRET values to the correct backend and agent environments:'
  printf 'LABOPS_WORKSPACE_IMAGE_REF=%s\n' "$ref"
  printf 'LABOPS_WORKSPACE_IMAGE_DIGEST=%s\n' "${ref##*@}"
  printf 'LABOPS_WORKSPACE_BIND_IP=%s\n' "$TARGET_IP"
  printf 'LABOPS_LOCAL_AGENT_URL=http://127.0.0.1:9100\n'
  note 'Set the SAME LABOPS_NODE_AGENT_TOKEN in backend and agent; never store it in Git or print it in logs.'
}

install_agent() {
  require_target
  require_repo
  sudo -v
  [[ -f infrastructure/systemd/install-workstation-agent.sh ]] || die 'Installer script missing.'
  sudo test -s /etc/labops/agent.env || die '/etc/labops/agent.env missing or empty. Configure it first.'
  sudo grep -Eq '^LABOPS_AGENT_SECRET=.' /etc/labops/agent.env || die 'Missing LABOPS_AGENT_SECRET in agent.env.'
  sudo grep -Eq '^LABOPS_NODE_AGENT_TOKEN=.' /etc/labops/agent.env || die 'Missing LABOPS_NODE_AGENT_TOKEN in agent.env.'
  sudo grep -Eq '^LABOPS_WORKSPACE_IMAGE_REF=.' /etc/labops/agent.env || die 'Missing LABOPS_WORKSPACE_IMAGE_REF in agent.env.'
  sudo grep -Eq '^LABOPS_WORKSPACE_IMAGE_DIGEST=.' /etc/labops/agent.env || die 'Missing LABOPS_WORKSPACE_IMAGE_DIGEST in agent.env.'
  note 'Review installer and /etc/labops/agent.env BEFORE installing; this step changes a privileged daemon.'
  read -r -p 'Type INSTALL AGENT after review: ' confirmation
  [[ "$confirmation" == 'INSTALL AGENT' ]] || die 'Operator did not authorize install.'
  sudo bash "$REPO/infrastructure/systemd/install-workstation-agent.sh"
  sudo systemctl is-active --quiet labops-agent.service || die 'labops-agent.service is not active.'
  sudo systemctl show labops-agent.service --property=User,SupplementaryGroups,ExecStart --no-pager
  local groups
  groups="$(sudo systemctl show labops-agent.service --property=SupplementaryGroups --value)"
  [[ " $groups " == *' docker '* ]] || die 'labops-agent.service missing SupplementaryGroups=docker.'
  curl --fail --silent --show-error --max-time 5 http://127.0.0.1:9100/health
  printf '\n'
  note 'Agent service is running; backend->agent authenticated execution still requires an integration test.'
  note 'The docker group gives the agent root-equivalent Docker control; verify API auth/bind before production.'
}

quota_preflight() {
  require_target
  sudo -v
  local src fs opts
  src="$(findmnt -n -o SOURCE --mountpoint "$MOUNT")" || die "$MOUNT is not a mounted filesystem"
  fs="$(findmnt -n -o FSTYPE --mountpoint "$MOUNT")"
  opts="$(findmnt -n -o OPTIONS --mountpoint "$MOUNT")"
  [[ "$fs" == 'ext4' ]] || die "Unexpected filesystem $fs; STOP."
  note "Mounted source: $src; FS: $fs; mount options: $opts"
  sudo tune2fs -l "$src" | grep -Ei 'Filesystem features:|Inode size:|Project quota inode:' || true
  note 'Open files/processes on the filesystem (do not kill them):'
  sudo fuser -vm "$MOUNT" || true
  note 'fstab entry and current quota status'
  grep -nF "$MOUNT" /etc/fstab || true
  sudo repquota -P "$MOUNT" || warn 'Project quota not yet active or readable.'
  note 'No filesystem changes made. Perform the OFFLINE quota procedure separately in a maintenance window.'
}

ssh_cleanup() {
  require_target
  sudo -v
  note 'Confirm another independent admin SSH session to port 1912 is ALREADY OPEN and tested.'
  sudo ss -H -ltnp 'sport = :1912' | grep -q . || die 'Admin SSH port 1912 is not listening; do not proceed.'
  sudo docker ps --format '{{.Names}}' | grep -Fxq labops-wg-easy || die 'labops-wg-easy is not running; do not proceed.'
  local code
  code="$(curl --silent --output /dev/null --write-out '%{http_code}' --max-time 5 http://127.0.0.1:51821/login || true)"
  [[ "$code" == '200' ]] || die "wg-easy login health unexpected: HTTP $code"
  note 'Legacy listener/process pairs from report; all must still match before termination:'
  local ports=(22003 22004 22005)
  local pids=(3677306 3824760 4092758)
  local i line pid comm
  for i in 0 1 2; do
    pid="${pids[$i]}"
    line="$(sudo ss -H -ltnp "sport = :${ports[$i]}")"
    printf '%s\n' "$line"
    [[ "$line" == *"pid=$pid,"* ]] || die "PID $pid is NOT the verified listener on ${ports[$i]}; refuse stale-PID kill."
    comm="$(ps -p "$pid" -o comm= | tr -d '[:space:]')"
    [[ "$comm" == sshd* ]] || die "PID $pid is now '$comm', not sshd; refuse termination."
  done
  read -r -p 'Type STOP OLD HOST SSHD to terminate only these 3 verified daemons: ' confirmation
  [[ "$confirmation" == 'STOP OLD HOST SSHD' ]] || die 'Operator did not authorize SSH cleanup.'
  for i in 0 1 2; do
    pid="${pids[$i]}"
    # Re-check immediately before signaling, in case PIDs/sockets were recycled.
    line="$(sudo ss -H -ltnp "sport = :${ports[$i]}")"
    [[ "$line" == *"pid=$pid,"* ]] || die "Listener changed on port ${ports[$i]} during cleanup; stop."
    sudo kill -TERM "$pid"
  done
  note 'Check admin and workspace sockets below; DO NOT terminate :1912 or :22.'
  sudo ss -ltnp | grep -E ':(1912|22|22003|22004|22005)[[:space:]]' || true
  note 'Confirm also that legacy services are not configured to respawn at reboot.'
}

verify() {
  require_target
  require_repo
  sudo -v
  note 'Agent and Docker runtime'
  sudo systemctl is-active --quiet labops-agent.service || die 'Agent systemd unit inactive'
  curl --fail --silent --show-error --max-time 5 http://127.0.0.1:9100/health
  printf '\n'
  sudo docker image inspect labops-workspace:v2 --format='Local image ID: {{.Id}}' || die 'Missing Docker workspace image'
  note 'Quota enforcement (must be enabled, not merely advertised)'
  local opts
  opts="$(findmnt -n -o OPTIONS --mountpoint "$MOUNT")" || die 'Storage mount absent.'
  [[ ",$opts," == *',prjquota,'* || ",$opts," == *',pquota,'* ]] || die 'prjquota/pquota mount option missing.'
  sudo repquota -P "$MOUNT" || die 'repquota -P failed.'
  note 'Protected ports and WireGuard'
  sudo ss -H -ltnp 'sport = :1912' | grep -q . || die 'Admin SSH 1912 missing.'
  sudo docker ps --format '{{.Names}}' | grep -Fxq labops-wg-easy || die 'wg-easy container missing.'
  sudo ss -ltnp | grep -E ':(1912|22|22003|22004|22005|9100)[[:space:]]' || true
  note 'Docker workspaces, capabilities, UID/GID, SSH authorized_keys, actual quota limit, and user E2E SSH MUST be manually tested.'
  note 'Read-only checks passed. This is NOT sufficient by itself to declare PRODUCTION_READY.'
}

push_reviewed() {
  require_target
  require_repo
  [[ "$(id -un)" == 'aiot-ws1' ]] || die 'Push as Git repository owner aiot-ws1, not root or another identity.'
  note 'Review recent commits and the complete working tree before pushing.'
  git log -8 --oneline
  git status --porcelain=v1 --untracked-files=all
  local line path
  while IFS= read -r line; do
    [[ -z "$line" ]] && continue
    path="${line:3}"
    [[ "$path" == 'infrastructure/wg-easy/healthcheck.sh' ]] || die "Unexpected uncommitted/untracked file: $path. Review first."
  done < <(git status --porcelain=v1 --untracked-files=all)
  note 'Dry-run push: will not modify remote.'
  git push --dry-run origin "HEAD:refs/heads/$BRANCH"
  note 'Do not approve unless the full host acceptance checklist and tests pass, including real quota enforcement.'
  read -r -p "Type PUSH $BRANCH to approve Git push: " confirmation
  [[ "$confirmation" == "PUSH $BRANCH" ]] || die 'Admin did not approve git push.'
  git push origin "HEAD:refs/heads/$BRANCH"
  note 'Push complete. Verify remote commit and save acceptance evidence.'
}

usage() {
  cat <<'EOF'
Usage: bash labops_admin_runbook.sh <command>

  preflight      Read-only inventory of this host, repository and critical services
  image          Build and push immutable Docker image to an APPROVED registry
                 Requires LABOPS_REGISTRY_IMAGE_TAG=registry.example/labops-workspace:v2
  install-agent  Install systemd agent from reviewed repo (explicit confirmation)
                 First manually synchronize backend and /etc/labops/agent.env
  quota-check    Read-only quota preflight: offline conversion is manual
  ssh-cleanup    Safely stop ONLY the 3 verified legacy host sshd listeners
  verify         Verify basic service, project quota, and reserved-port health
  push           Dry-run, show status, require admin approval, then git push

CAUTION: The ext4 quota feature change requires offline e2fsck/tune2fs,
proper /etc/fstab edits, verified backups, and a maintenance window.
It is intentionally not automated here.
EOF
}

case "${1:-}" in
  preflight) preflight ;;
  image) image ;;
  install-agent) install_agent ;;
  quota-check) quota_preflight ;;
  ssh-cleanup) ssh_cleanup ;;
  verify) verify ;;
  push) push_reviewed ;;
  -h|--help|help|'') usage ;;
  *) usage; die "Unknown subcommand: $1" ;;
esac
