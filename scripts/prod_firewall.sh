#!/usr/bin/env bash
# Only Cloudflare may reach the live site. Docker publishes the site on two ports: 80 (docker-compose.prod.yml, the one
# Cloudflare calls) and 8000 (docker-compose.yml, for local development; Compose adds the two lists together). Anything
# that calls the server's IP directly on port 80 without coming from Cloudflare, and anything at all on port 8000, is
# dropped. On 2026-11-09 one bot calling the server's IP directly held 80 connections open and took most of gunicorn's
# 36 request slots, so the site hung for everyone; through Cloudflare it can't do that.
#
# Same approach as scripts/staging_firewall.sh: Docker publishes its ports underneath ufw, so a ufw rule would not stop
# them. IPv4 is filtered in DOCKER-USER (the chain Docker leaves for our own rules), matched on the port people call
# rather than the container's. IPv6 is filtered in INPUT (Docker's proxy) and, if Docker filters IPv6 itself, in its
# DOCKER-USER chain too. Only traffic arriving on the internet-facing interface is filtered: the server talking to the
# site locally, and the containers talking to each other, are untouched. So is SSH. docs/production_setup.md.
#
#   sudo bash scripts/prod_firewall.sh          # apply (safe to run again: it refreshes Cloudflare's ranges)
#   sudo bash scripts/prod_firewall.sh --off    # remove the rules
#   sudo bash scripts/prod_firewall.sh --status # show them
set -euo pipefail
WEB_PORT="${PROD_PORT:-80}"        # Cloudflare only
DIRECT_PORT="${PROD_DIRECT_PORT:-8000}"   # nobody from outside
SET4=cloudflare-v4                 # shared with scripts/staging_firewall.sh: the same ranges
SET6=cloudflare-v6

[ "$(id -u)" = 0 ] || { echo "Run as root: sudo bash $0 ${1:-}"; exit 1; }
command -v ipset >/dev/null || { echo "ipset is missing: sudo apt-get install -y ipset"; exit 1; }

# The interface the internet arrives on (the default route's), unless given: PROD_IFACE=eth0
IFACE="${PROD_IFACE:-$(ip route show default | awk '{for (i = 1; i < NF; i++) if ($i == "dev") { print $(i + 1); exit }}')}"
[ -n "$IFACE" ] || { echo "Could not find the internet-facing interface: run with PROD_IFACE=<name>"; exit 1; }
IFACE6="${PROD_IFACE6:-$(ip -6 route show default 2>/dev/null | awk '{for (i = 1; i < NF; i++) if ($i == "dev") { print $(i + 1); exit }}')}"
IFACE6="${IFACE6:-$IFACE}"

WEB4=(-i "$IFACE" -p tcp -m conntrack --ctorigdstport "$WEB_PORT" --ctdir ORIGINAL -m set ! --match-set "$SET4" src -j DROP)
DIRECT4=(-i "$IFACE" -p tcp -m conntrack --ctorigdstport "$DIRECT_PORT" --ctdir ORIGINAL -j DROP)
WEB6_IN=(-i "$IFACE6" -p tcp --dport "$WEB_PORT" -m set ! --match-set "$SET6" src -j DROP)
DIRECT6_IN=(-i "$IFACE6" -p tcp --dport "$DIRECT_PORT" -j DROP)
WEB6_FWD=(-i "$IFACE6" -p tcp -m conntrack --ctorigdstport "$WEB_PORT" --ctdir ORIGINAL -m set ! --match-set "$SET6" src -j DROP)
DIRECT6_FWD=(-i "$IFACE6" -p tcp -m conntrack --ctorigdstport "$DIRECT_PORT" --ctdir ORIGINAL -j DROP)

docker6() { ip6tables -S DOCKER-USER >/dev/null 2>&1; }   # Docker filters IPv6 itself (newer Docker)

add() {    # add <iptables|ip6tables> <chain> <rule...>: once, at the top
  local cmd="$1" chain="$2"; shift 2
  "$cmd" -C "$chain" "$@" >/dev/null 2>&1 || "$cmd" -I "$chain" 1 "$@"
}
drop() {   # drop <iptables|ip6tables> <chain> <rule...>: every copy
  local cmd="$1" chain="$2"; shift 2
  while "$cmd" -C "$chain" "$@" >/dev/null 2>&1; do "$cmd" -D "$chain" "$@"; done
}

remove_rules() {
  drop iptables DOCKER-USER "${WEB4[@]}"
  drop iptables DOCKER-USER "${DIRECT4[@]}"
  drop ip6tables INPUT "${WEB6_IN[@]}"
  drop ip6tables INPUT "${DIRECT6_IN[@]}"
  if docker6; then
    drop ip6tables DOCKER-USER "${WEB6_FWD[@]}"
    drop ip6tables DOCKER-USER "${DIRECT6_FWD[@]}"
  fi
}

case "${1:-}" in
  --off)
    remove_rules
    # The sets stay while staging's firewall still uses them (ipset refuses to destroy a set in use)
    ipset destroy "$SET4" 2>/dev/null || true
    ipset destroy "$SET6" 2>/dev/null || true
    echo "Ports $WEB_PORT and $DIRECT_PORT are open to everyone again."
    exit 0 ;;
  --status)
    echo "Internet-facing interface: $IFACE (IPv6: $IFACE6)"
    iptables -S DOCKER-USER 2>/dev/null | grep -E -- "--ctorigdstport ($WEB_PORT|$DIRECT_PORT) " || echo "IPv4: no rules"
    ip6tables -S INPUT 2>/dev/null | grep -E -- "--dport ($WEB_PORT|$DIRECT_PORT) " || echo "IPv6 (INPUT): no rules"
    if docker6; then
      ip6tables -S DOCKER-USER | grep -E -- "--ctorigdstport ($WEB_PORT|$DIRECT_PORT) " || echo "IPv6 (DOCKER-USER): no rules"
    fi
    for s in "$SET4" "$SET6"; do
      echo "$s: $(ipset list "$s" 2>/dev/null | grep -cE '^[0-9a-f:.]+/[0-9]+$' || true) ranges"
    done
    exit 0 ;;
  "") ;;
  *) echo "Unknown option: $1 (use --off or --status)"; exit 1 ;;
esac

# Fill a set from one of Cloudflare's lists. A list that can't be fetched, or looks wrong, leaves everything as it was
# (that matters here: an empty list would shut Cloudflare out too, and with it every visitor).
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

# DOCKER-USER exists once Docker has started; this runs after it (prod-firewall.service)
iptables -N DOCKER-USER 2>/dev/null || true
add iptables DOCKER-USER "${WEB4[@]}"
add iptables DOCKER-USER "${DIRECT4[@]}"
add ip6tables INPUT "${WEB6_IN[@]}"
add ip6tables INPUT "${DIRECT6_IN[@]}"
if docker6; then
  add ip6tables DOCKER-USER "${WEB6_FWD[@]}"
  add ip6tables DOCKER-USER "${DIRECT6_FWD[@]}"
fi
echo "Port $WEB_PORT: only Cloudflare gets through. Port $DIRECT_PORT: closed to the internet. (IPv4 and IPv6, on $IFACE)"
