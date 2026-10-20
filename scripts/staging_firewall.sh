#!/usr/bin/env bash
# Only Cloudflare may reach staging's port (8080). Anything else that calls the server's IP on that port directly
# (bots scanning addresses) is dropped, so staging is only ever seen through https://staging.barkandambrosiagallery.org.
#
# Docker publishes its ports underneath ufw, so a ufw rule would not stop them: IPv4 is filtered in DOCKER-USER (the
# chain Docker leaves for our own rules), matched on the port people call (8080) rather than the container's (8000).
# IPv6 reaches Docker's port through its proxy, so it is filtered in INPUT. Cloudflare's ranges are fetched from
# cloudflare.com each run. Nothing on this server that talks to staging locally is affected. docs/staging.md.
#
#   sudo bash scripts/staging_firewall.sh          # apply (safe to run again: it refreshes the ranges)
#   sudo bash scripts/staging_firewall.sh --off    # remove the rules
#   sudo bash scripts/staging_firewall.sh --status # show them
set -euo pipefail
PORT="${STAGING_PORT:-8080}"
SET4=cloudflare-v4
SET6=cloudflare-v6

[ "$(id -u)" = 0 ] || { echo "Run as root: sudo bash $0 ${1:-}"; exit 1; }
command -v ipset >/dev/null || { echo "ipset is missing: sudo apt-get install -y ipset"; exit 1; }

RULE4=(-p tcp -m conntrack --ctorigdstport "$PORT" --ctdir ORIGINAL -m set ! --match-set "$SET4" src -j DROP)
RULE6=(-p tcp --dport "$PORT" -m set ! --match-set "$SET6" src -j DROP)

remove_rules() {
  while iptables -C DOCKER-USER "${RULE4[@]}" >/dev/null 2>&1; do iptables -D DOCKER-USER "${RULE4[@]}"; done
  while ip6tables -C INPUT "${RULE6[@]}" >/dev/null 2>&1; do ip6tables -D INPUT "${RULE6[@]}"; done
}

case "${1:-}" in
  --off)
    remove_rules
    ipset destroy "$SET4" 2>/dev/null || true
    ipset destroy "$SET6" 2>/dev/null || true
    echo "Port $PORT is open to everyone again."
    exit 0 ;;
  --status)
    iptables -S DOCKER-USER 2>/dev/null | grep -- "--ctorigdstport $PORT" || echo "IPv4: no rule"
    ip6tables -S INPUT 2>/dev/null | grep -- "--dport $PORT" || echo "IPv6: no rule"
    for s in "$SET4" "$SET6"; do
      echo "$s: $(ipset list "$s" 2>/dev/null | grep -cE '^[0-9a-f:.]+/[0-9]+$' || true) ranges"
    done
    exit 0 ;;
  "") ;;
  *) echo "Unknown option: $1 (use --off or --status)"; exit 1 ;;
esac

# Fill a set from one of Cloudflare's lists. A list that can't be fetched, or looks wrong, leaves everything as it was.
load() {   # load <set> <family> <url> <min ranges>
  local set="$1" family="$2" url="$3" min="$4" ranges count
  ranges="$(curl -fsS --max-time 20 "$url")" || { echo "Could not fetch $url: nothing changed"; exit 1; }
  count="$(grep -cE '^[0-9a-f:.]+/[0-9]+$' <<<"$ranges" || true)"
  [ "$count" -ge "$min" ] || { echo "$url gave $count ranges (expected at least $min): nothing changed"; exit 1; }
  ipset create "$set" hash:net family "$family" -exist
  ipset create "$set-new" hash:net family "$family" -exist
  ipset flush "$set-new"
  grep -E '^[0-9a-f:.]+/[0-9]+$' <<<"$ranges" | while read -r r; do ipset add "$set-new" "$r" -exist; done
  ipset swap "$set-new" "$set"   # the new list takes over in one step
  ipset destroy "$set-new"
  echo "$set: $count ranges"
}
load "$SET4" inet https://www.cloudflare.com/ips-v4 10
load "$SET6" inet6 https://www.cloudflare.com/ips-v6 5

# DOCKER-USER exists once Docker has started; this runs after it (staging-firewall.service)
iptables -N DOCKER-USER 2>/dev/null || true
iptables -C DOCKER-USER "${RULE4[@]}" >/dev/null 2>&1 || iptables -I DOCKER-USER 1 "${RULE4[@]}"
ip6tables -C INPUT "${RULE6[@]}" >/dev/null 2>&1 || ip6tables -I INPUT 1 "${RULE6[@]}"
echo "Port $PORT: only Cloudflare gets through (IPv4 and IPv6)."
