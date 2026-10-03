#!/usr/bin/env bash
# Basic hardening of a fresh VPS for ClaudeGPT OS (Debian 12/13, Ubuntu 24.04).
# Idempotent: running it again converges to the same state.
#
#   sudo bash deploy/harden.sh
#
# What it does:
#   1. updates the system and enables automatic security updates;
#   2. firewall (ufw): SSH (rate limited), 80/tcp, 443/tcp, 443/udp;
#   3. SSH: keys only, root may log in with a key but never with a password
#      (asks for confirmation first so you cannot lock yourself out);
#   4. optional fail2ban jail for SSH (WITH_FAIL2BAN=1);
#   5. swap file on small machines (< 3.5 GB RAM, no swap yet);
#   6. Docker Engine + Compose plugin from Docker's official apt repository,
#      with log rotation and settings that keep real client IPs. Automatic
#      updates do not cover it (a new Docker release is best installed while
#      you watch): docs/DEPLOYMENT.md, section "Updating", upgrades it monthly.
#
# Environment options:
#   WITH_FAIL2BAN=1   also install fail2ban with an sshd jail
#   ASSUME_YES=1      do not ask for confirmation (the key check still runs)
#   SKIP_SSH=1        leave the SSH configuration untouched
#   SKIP_DOCKER=1     do not install Docker
#   SKIP_SWAP=1       never create a swap file
#
# Docker and ufw: ports published by Docker (`ports:` in docker-compose.yml)
# bypass ufw's rules, because Docker inserts its own iptables rules before
# them. Here only Caddy publishes 80 and 443, which must be public anyway.
# Never publish any other port; to reach a service while debugging, bind it
# to 127.0.0.1 and use an SSH tunnel (ssh -L).
set -Eeuo pipefail

readonly SSHD_DROPIN=/etc/ssh/sshd_config.d/01-claudegpt.conf
readonly SYSCTL_FILE=/etc/sysctl.d/60-claudegpt.conf
readonly SWAP_FILE=/swapfile
export DEBIAN_FRONTEND=noninteractive

say() { printf '\n\033[1m==> %s\033[0m\n' "$*"; }
info() { printf '    %s\n' "$*"; }
warn() { printf '\033[33m[warning]\033[0m %s\n' "$*" >&2; }
die() {
  printf '\033[31m[error]\033[0m %s\n' "$*" >&2
  exit 1
}
trap 'die "Line $LINENO failed. Once the cause is fixed, it is safe to run the script again."' ERR

confirm() {
  [[ "${ASSUME_YES:-0}" == 1 ]] && return 0
  local answer=""
  # Without a terminal (e.g. piped over ssh) the answer is always "no".
  { exec 3</dev/tty; } 2>/dev/null || return 1
  read -r -p "$1 [y/N] " answer <&3 || answer=""
  exec 3<&-
  # s, si and sí: the Catalan yes that earlier versions asked for.
  [[ "${answer,,}" =~ ^(s|si|sí|y|yes)$ ]]
}

apt_install() {
  apt-get install -y --no-install-recommends \
    -o Dpkg::Options::=--force-confdef -o Dpkg::Options::=--force-confold "$@"
}

# ----------------------------------------------------------------- checks
preflight() {
  [[ "${EUID}" -eq 0 ]] || die "Run it as root: sudo bash $0"
  [[ -r /etc/os-release ]] || die "Cannot find /etc/os-release."
  # shellcheck source=/dev/null
  . /etc/os-release
  case "${ID:-}" in
    debian | ubuntu) ;;
    *) die "Unsupported system (${ID:-unknown}). It needs Debian 12/13 or Ubuntu 24.04." ;;
  esac
  OS_ID="${ID}"
  OS_CODENAME="${UBUNTU_CODENAME:-${VERSION_CODENAME:-}}"
  [[ -n "${OS_CODENAME}" ]] || die "Could not tell the system's version."
  info "System: ${PRETTY_NAME:-${OS_ID}}"
}

# ------------------------------------------------------ 1) system updates
system_updates() {
  say "Updating the system and turning on automatic updates"
  apt-get update -qq
  apt-get -y -o Dpkg::Options::=--force-confdef -o Dpkg::Options::=--force-confold upgrade
  apt_install ca-certificates curl gnupg ufw unattended-upgrades apt-listchanges
  cat >/etc/apt/apt.conf.d/20auto-upgrades <<'EOF'
APT::Periodic::Update-Package-Lists "1";
APT::Periodic::Unattended-Upgrade "1";
APT::Periodic::AutocleanInterval "7";
EOF
  # Kernel and libc fixes need a reboot: do it at a quiet hour. The containers
  # come back by themselves (restart: unless-stopped).
  cat >/etc/apt/apt.conf.d/52claudegpt-unattended-upgrades <<'EOF'
Unattended-Upgrade::Automatic-Reboot "true";
Unattended-Upgrade::Automatic-Reboot-Time "04:30";
Unattended-Upgrade::Remove-Unused-Dependencies "true";
EOF
  systemctl enable --now unattended-upgrades >/dev/null
  info "Automatic security updates are on (a reboot, if one is needed, at 04:30)."
}

# ------------------------------------------------------------- 2) firewall
# sshd refuses to print or test its configuration without this directory,
# which is missing on Ubuntu while ssh.socket has not started the daemon yet.
sshd_ready() { install -d -m 0755 /run/sshd; }

ssh_ports() {
  local ports=""
  if command -v sshd >/dev/null 2>&1; then
    sshd_ready
    ports="$(sshd -T 2>/dev/null | awk '$1 == "port" { print $2 }' | sort -u || true)"
  fi
  printf '%s\n' "${ports:-22}"
}

firewall() {
  say "Setting up the firewall (ufw)"
  ufw default deny incoming >/dev/null
  ufw default allow outgoing >/dev/null
  # Allow every port sshd listens on before enabling, or a custom port locks you out.
  local port ports
  ports="$(ssh_ports)"
  for port in ${ports}; do
    ufw limit "${port}/tcp" comment 'SSH (rate limited)' >/dev/null
  done
  ufw allow 80/tcp comment 'HTTP: certificate + redirect' >/dev/null
  ufw allow 443/tcp comment 'HTTPS' >/dev/null
  ufw allow 443/udp comment 'HTTP/3 (QUIC)' >/dev/null
  ufw --force enable >/dev/null
  info "Open: SSH (${ports//$'\n'/, }), 80/tcp, 443/tcp and 443/udp. Everything else is closed."
}

# ------------------------------------------------------------------ 3) SSH
has_key() {
  local home
  home="$(getent passwd "$1" | cut -d: -f6)"
  [[ -n "${home}" && -s "${home}/.ssh/authorized_keys" ]] &&
    grep -Eq '^[^#]*(ssh-|ecdsa-|sk-)' "${home}/.ssh/authorized_keys"
}

harden_ssh() {
  say "SSH: keys only"
  if [[ "${SKIP_SSH:-0}" == 1 ]]; then
    info "Skipped (SKIP_SSH=1)."
    return
  fi
  local login_user="${SUDO_USER:-root}"
  if ! has_key "${login_user}"; then
    warn "The user '${login_user}' has no key in ~/.ssh/authorized_keys."
    warn "Passwords stay on, or you would be locked out. Add your key"
    warn "(ssh-copy-id from your computer) and run the script again."
    return
  fi
  info "The user '${login_user}' has an authorized SSH key."
  if ! confirm "Have you checked that you can log in with the key, and is another SSH session open?"; then
    warn "SSH is unchanged. Run the script again once you have checked it."
    return
  fi
  # sshd keeps the FIRST value it reads and drop-ins are read in name order,
  # so this file must sort before others such as 50-cloud-init.conf.
  install -d -m 0755 /etc/ssh/sshd_config.d
  cat >"${SSHD_DROPIN}" <<'EOF'
# Managed by ClaudeGPT OS deploy/harden.sh
PubkeyAuthentication yes
PasswordAuthentication no
KbdInteractiveAuthentication no
PermitEmptyPasswords no
PermitRootLogin prohibit-password
LoginGraceTime 30
X11Forwarding no
EOF
  sshd_ready
  if ! sshd -t; then
    rm -f "${SSHD_DROPIN}"
    die "The SSH configuration is not valid; it was undone without being applied."
  fi
  if ! grep -Eqsi '^[[:space:]]*Include[[:space:]]+/etc/ssh/sshd_config\.d/' /etc/ssh/sshd_config; then
    warn "/etc/ssh/sshd_config does not include sshd_config.d/: check it by hand."
  fi
  systemctl try-reload-or-restart ssh.service
  info "In effect now: $(sshd -T 2>/dev/null | grep -Ei '^(passwordauthentication|permitrootlogin) ' | paste -sd, - || true)"
  info "Try it from another terminal before you close this session."
}

# ------------------------------------------------------------- 4) fail2ban
fail2ban_sshd() {
  [[ "${WITH_FAIL2BAN:-0}" == 1 ]] || return 0
  say "fail2ban for SSH"
  apt_install fail2ban python3-systemd
  install -d -m 0755 /etc/fail2ban/jail.d
  cat >/etc/fail2ban/jail.d/claudegpt-sshd.local <<EOF
# Managed by ClaudeGPT OS deploy/harden.sh
[sshd]
enabled  = true
backend  = systemd
port     = $(ssh_ports | paste -sd, -)
maxretry = 5
findtime = 10m
bantime  = 1h
EOF
  systemctl enable fail2ban >/dev/null
  systemctl restart fail2ban
  info "A 1 h ban after 5 failed attempts in 10 min."
}

# -------------------------------------------------------- 5) kernel + swap
kernel_and_swap() {
  say "System settings"
  # quic-go (HTTP/3 in Caddy) wants 7 MiB UDP buffers; the host must allow it.
  cat >"${SYSCTL_FILE}" <<'EOF'
# Managed by ClaudeGPT OS deploy/harden.sh
net.core.rmem_max = 7500000
net.core.wmem_max = 7500000
vm.swappiness = 10
EOF
  if sysctl --quiet --load="${SYSCTL_FILE}" 2>/dev/null; then
    info "Larger UDP buffers for HTTP/3."
  else
    warn "The provider does not allow the settings of ${SYSCTL_FILE}; HTTP/3 will work anyway."
  fi

  [[ "${SKIP_SWAP:-0}" == 1 ]] && return 0
  local mem_kb
  mem_kb="$(awk '/^MemTotal:/ { print $2 }' /proc/meminfo)"
  if [[ -n "$(swapon --noheadings --show=NAME)" ]]; then
    info "There is swap already."
  elif ((mem_kb < 3500000)); then
    if [[ ! -f "${SWAP_FILE}" ]]; then
      fallocate -l 2G "${SWAP_FILE}" 2>/dev/null ||
        dd if=/dev/zero of="${SWAP_FILE}" bs=1M count=2048 status=none
      chmod 600 "${SWAP_FILE}"
      mkswap "${SWAP_FILE}" >/dev/null
    fi
    if ! swapon "${SWAP_FILE}" 2>/dev/null; then
      rm -f "${SWAP_FILE}"
      warn "This VPS does not allow swap; carrying on without it."
      return 0
    fi
    grep -q "^${SWAP_FILE} " /etc/fstab || echo "${SWAP_FILE} none swap sw 0 0" >>/etc/fstab
    info "Created a 2 GB swap file (the machine has little RAM)."
  fi
}

# --------------------------------------------------------------- 6) Docker
docker_engine() {
  say "Docker"
  if [[ "${SKIP_DOCKER:-0}" == 1 ]]; then
    info "Skipped (SKIP_DOCKER=1)."
    return
  fi
  if docker compose version >/dev/null 2>&1; then
    info "Already installed: $(docker --version)."
  else
    if dpkg -s docker.io >/dev/null 2>&1; then
      die "The distribution's docker.io package is installed. Remove it (apt-get remove docker.io) and try again."
    fi
    install -m 0755 -d /etc/apt/keyrings
    curl -fsSL "https://download.docker.com/linux/${OS_ID}/gpg" -o /etc/apt/keyrings/docker.asc
    chmod a+r /etc/apt/keyrings/docker.asc
    cat >/etc/apt/sources.list.d/docker.sources <<EOF
Types: deb
URIs: https://download.docker.com/linux/${OS_ID}
Suites: ${OS_CODENAME}
Components: stable
Architectures: $(dpkg --print-architecture)
Signed-By: /etc/apt/keyrings/docker.asc
EOF
    apt-get update -qq
    apt_install docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
    info "Installed: $(docker --version)."
  fi

  # userland-proxy=false + ip6tables: containers see the visitor's real IP
  # (also over IPv6), which the allowlist and the login throttling rely on.
  local daemon_json=/etc/docker/daemon.json
  if [[ ! -e "${daemon_json}" ]]; then
    install -d -m 0755 /etc/docker
    cat >"${daemon_json}" <<'EOF'
{
  "log-driver": "json-file",
  "log-opts": { "max-size": "10m", "max-file": "3" },
  "live-restore": true,
  "userland-proxy": false,
  "ip6tables": true
}
EOF
    systemctl restart docker
    info "Daemon settings: log rotation and the clients' real IPs."
  elif ! grep -q '"userland-proxy": *false' "${daemon_json}"; then
    warn "${daemon_json} already exists and was left untouched. Recommended: \"userland-proxy\": false."
  fi
  systemctl enable --now docker >/dev/null
  info "No user is added to the docker group: that is the same as being root. Use sudo."
  info "Docker does not update itself: once a month, apt-get update && apt-get upgrade"
  info "(docs/DEPLOYMENT.md, section \"Updating\")."
}

main() {
  preflight
  system_updates
  firewall
  harden_ssh
  fail2ban_sshd
  kernel_and_swap
  docker_engine
  say "Done"
  info "Check it: ufw status verbose · docker compose version"
  info "Next step: docs/DEPLOYMENT.md, section \"3. Configure\"."
}

main "$@"
