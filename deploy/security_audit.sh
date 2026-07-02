#!/usr/bin/env bash
# Read-only security audit for the Debian 12 host running diytracker.
# Reports findings only — it never changes anything. Run as root for full coverage:
#   sudo bash deploy/security_audit.sh
set -u

PASS=0 WARN=0
ok()   { printf '  \033[32m[ OK ]\033[0m %s\n' "$1"; PASS=$((PASS+1)); }
warn() { printf '  \033[33m[WARN]\033[0m %s\n' "$1"; WARN=$((WARN+1)); }
section() { printf '\n\033[1m== %s ==\033[0m\n' "$1"; }

[ "$(id -u)" -eq 0 ] || printf '\033[33mNote: not running as root — some checks will be incomplete.\033[0m\n'

section "System & updates"
printf '  Kernel: %s | Uptime:%s\n' "$(uname -r)" "$(uptime -p | sed 's/up//')"
updates=$(apt-get -s upgrade 2>/dev/null | grep -c '^Inst ' || true)
[ "${updates:-0}" -eq 0 ] && ok "No pending package updates" || warn "$updates packages have pending updates (run: apt update && apt upgrade)"
sec_updates=$(apt-get -s upgrade 2>/dev/null | grep '^Inst ' | grep -ci security || true)
[ "${sec_updates:-0}" -gt 0 ] && warn "$sec_updates of those are SECURITY updates — apply promptly"
dpkg -s unattended-upgrades >/dev/null 2>&1 && ok "unattended-upgrades installed" \
  || warn "unattended-upgrades not installed (apt install unattended-upgrades)"
[ -f /var/run/reboot-required ] && warn "Reboot required to apply updates"

section "SSH daemon"
if [ -r /etc/ssh/sshd_config ]; then
  sshcfg=$(sshd -T 2>/dev/null || cat /etc/ssh/sshd_config /etc/ssh/sshd_config.d/*.conf 2>/dev/null)
  check_ssh() { # key, safe_value, message_if_bad
    val=$(printf '%s\n' "$sshcfg" | grep -i "^${1} " | tail -1 | awk '{print tolower($2)}')
    if [ "${val:-$3}" = "$2" ]; then ok "sshd: $1 = $2"; else warn "sshd: $1 is '${val:-default}' — set to '$2'"; fi
  }
  check_ssh permitrootlogin no yes
  check_ssh passwordauthentication no yes
  check_ssh permitemptypasswords no no
  check_ssh x11forwarding no yes
  check_ssh maxauthtries 3 6
else
  warn "Cannot read sshd config (run as root, or ssh not installed)"
fi

section "Firewall"
if command -v ufw >/dev/null 2>&1; then
  ufw status 2>/dev/null | grep -q 'Status: active' && ok "ufw is active" || warn "ufw installed but INACTIVE (ufw enable)"
elif command -v nft >/dev/null 2>&1 && [ -n "$(nft list ruleset 2>/dev/null)" ]; then
  ok "nftables ruleset present"
else
  warn "No active firewall found (apt install ufw; ufw default deny incoming; ufw allow ssh,http,https; ufw enable)"
fi

section "Brute-force protection"
if systemctl is-active --quiet fail2ban 2>/dev/null; then
  ok "fail2ban is running ($(fail2ban-client status 2>/dev/null | grep 'Jail list' | cut -d: -f2 | xargs))"
else
  warn "fail2ban not running (apt install fail2ban)"
fi

section "Accounts & sudo"
empty_pw=$(awk -F: '($2 == "") {print $1}' /etc/shadow 2>/dev/null)
[ -z "$empty_pw" ] && ok "No accounts with empty passwords" || warn "Accounts with EMPTY passwords: $empty_pw"
extra_root=$(awk -F: '($3 == 0 && $1 != "root") {print $1}' /etc/passwd)
[ -z "$extra_root" ] && ok "Only 'root' has UID 0" || warn "Extra UID-0 accounts: $extra_root"
nopasswd=$(grep -rh NOPASSWD /etc/sudoers /etc/sudoers.d/ 2>/dev/null | grep -v '^#' || true)
[ -z "$nopasswd" ] && ok "No NOPASSWD sudo rules" || warn "NOPASSWD sudo rules exist:"$'\n'"$nopasswd"
shell_users=$(awk -F: '($7 ~ /(bash|zsh|sh)$/ && $3 >= 1000) {print $1}' /etc/passwd | xargs)
printf '  Login-capable users: %s\n' "${shell_users:-none}"

section "Listening services"
printf '  Anything here that is not ssh/nginx/gunicorn deserves a second look:\n'
ss -tulnpH 2>/dev/null | awk '{printf "    %-6s %-25s %s\n", $1, $5, $7}' | sort -u
ss -tlnH 2>/dev/null | awk '$4 ~ /(0\.0\.0\.0|\[::\]):/ {print $4}' | grep -vE ':(22|80|443)$' | while read -r addr; do
  warn "Listening on all interfaces: $addr — bind to 127.0.0.1 if it sits behind a reverse proxy"
done

section "Kernel hardening (sysctl)"
check_sysctl() {
  cur=$(sysctl -n "$1" 2>/dev/null)
  [ "$cur" = "$2" ] && ok "$1 = $2" || warn "$1 is '${cur:-unset}' — recommend $2 (set in /etc/sysctl.d/99-hardening.conf)"
}
check_sysctl net.ipv4.conf.all.rp_filter 1
check_sysctl net.ipv4.conf.all.accept_redirects 0
check_sysctl net.ipv4.conf.all.send_redirects 0
check_sysctl net.ipv4.conf.all.accept_source_route 0
check_sysctl net.ipv4.tcp_syncookies 1
check_sysctl kernel.kptr_restrict 1
check_sysctl kernel.dmesg_restrict 1
check_sysctl fs.protected_hardlinks 1
check_sysctl fs.protected_symlinks 1

section "File permissions"
for f in /etc/shadow:640 /etc/gshadow:640 /etc/ssh/sshd_config:600; do
  path=${f%:*}; want=${f#*:}
  mode=$(stat -c '%a' "$path" 2>/dev/null)
  [ -z "$mode" ] && continue
  [ "$mode" -le "$want" ] && ok "$path mode $mode" || warn "$path mode is $mode — tighten to $want"
done
ww=$(find /etc /usr/local -xdev -type f -perm -0002 2>/dev/null | head -5)
[ -z "$ww" ] && ok "No world-writable files in /etc or /usr/local" || warn "World-writable files found:"$'\n'"$ww"

printf '\n\033[1mDone: %d OK, %d warnings.\033[0m\n' "$PASS" "$WARN"
[ "$WARN" -eq 0 ]
