"""
Only Cloudflare reaches the live site (scripts/prod_firewall.sh, docs/production_setup.md). On 2026-11-09 a bot calling
the server's IP directly held 80 connections and took most of gunicorn's request slots. The script lets only
Cloudflare's ranges reach port 80, closes port 8000 (published for local development) to the internet, filters only the
internet-facing interface, changes nothing if the ranges can't be fetched, and is applied again at boot. Gunicorn
replaces each worker after a number of requests, so stuck threads can't pile up for weeks.
"""
from pathlib import Path

from django.test import SimpleTestCase

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = (ROOT / "scripts" / "prod_firewall.sh").read_text(encoding="utf-8")
UNIT = (ROOT / "scripts" / "prod-firewall.service").read_text(encoding="utf-8")
COMPOSE = (ROOT / "docker-compose.prod.yml").read_text(encoding="utf-8")
DOCS = (ROOT / "docs" / "production_setup.md").read_text(encoding="utf-8")


class ProdFirewallTests(SimpleTestCase):
    def test_port_80_lets_only_cloudflare_through_in_dockers_own_chain(self):
        self.assertIn('WEB_PORT="${PROD_PORT:-80}"', SCRIPT)
        self.assertIn('WEB4=(-i "$IFACE" -p tcp -m conntrack --ctorigdstport "$WEB_PORT" --ctdir ORIGINAL '
                      '-m set ! --match-set "$SET4" src -j DROP)', SCRIPT)
        self.assertIn('add iptables DOCKER-USER "${WEB4[@]}"', SCRIPT)
        self.assertIn('add ip6tables INPUT "${WEB6_IN[@]}"', SCRIPT)   # IPv6 through Docker's proxy

    def test_port_8000_is_closed_to_the_internet_for_everyone(self):
        self.assertIn('DIRECT_PORT="${PROD_DIRECT_PORT:-8000}"', SCRIPT)
        self.assertIn('DIRECT4=(-i "$IFACE" -p tcp -m conntrack --ctorigdstport "$DIRECT_PORT" --ctdir ORIGINAL -j DROP)',
                      SCRIPT)
        self.assertIn('add iptables DOCKER-USER "${DIRECT4[@]}"', SCRIPT)
        self.assertIn('add ip6tables INPUT "${DIRECT6_IN[@]}"', SCRIPT)

    def test_ipv6_is_also_filtered_where_docker_filters_it_itself(self):
        self.assertIn("docker6() { ip6tables -S DOCKER-USER >/dev/null 2>&1; }", SCRIPT)
        self.assertIn('add ip6tables DOCKER-USER "${WEB6_FWD[@]}"', SCRIPT)
        self.assertIn('add ip6tables DOCKER-USER "${DIRECT6_FWD[@]}"', SCRIPT)

    def test_only_the_internet_facing_interface_is_filtered(self):
        # the server calling the site locally, and the containers calling each other, are untouched
        self.assertIn('IFACE="${PROD_IFACE:-$(ip route show default', SCRIPT)
        self.assertIn('[ -n "$IFACE" ] || {', SCRIPT)
        for rule in ("WEB4", "DIRECT4"):
            self.assertIn(f'{rule}=(-i "$IFACE" ', SCRIPT)
        for rule in ("WEB6_IN", "DIRECT6_IN", "WEB6_FWD", "DIRECT6_FWD"):
            self.assertIn(f'{rule}=(-i "$IFACE6" ', SCRIPT)

    def test_it_uses_cloudflares_ranges_and_a_list_it_cannot_fetch_changes_nothing(self):
        self.assertIn("https://www.cloudflare.com/ips-v4", SCRIPT)
        self.assertIn("https://www.cloudflare.com/ips-v6", SCRIPT)
        self.assertIn('ipset swap "$set-new" "$set"', SCRIPT)
        self.assertIn('|| { echo "Could not fetch $url: nothing changed"; exit 1; }', SCRIPT)
        self.assertIn('[ "$count" -ge "$min" ] ||', SCRIPT)
        # the ranges are loaded before any rule is added, so a failed fetch never leaves a rule with an empty list
        self.assertLess(SCRIPT.index('load "$SET4"'), SCRIPT.index('add iptables DOCKER-USER "${WEB4[@]}"'))

    def test_running_it_again_adds_no_second_rule_and_it_can_be_undone(self):
        self.assertIn('"$cmd" -C "$chain" "$@" >/dev/null 2>&1 || "$cmd" -I "$chain" 1 "$@"', SCRIPT)
        self.assertIn("--off)", SCRIPT)
        self.assertIn("--status)", SCRIPT)
        self.assertIn('drop iptables DOCKER-USER "${WEB4[@]}"', SCRIPT)
        self.assertIn('drop iptables DOCKER-USER "${DIRECT4[@]}"', SCRIPT)

    def test_it_shares_cloudflares_ranges_with_the_staging_firewall(self):
        staging = (ROOT / "scripts" / "staging_firewall.sh").read_text(encoding="utf-8")
        for line in ("SET4=cloudflare-v4", "SET6=cloudflare-v6"):
            self.assertIn(line, SCRIPT)
            self.assertIn(line, staging)

    def test_the_boot_unit_runs_it_after_docker_and_removes_it_on_stop(self):
        self.assertIn("After=docker.service network-online.target", UNIT)
        self.assertIn("ExecStart=/bin/bash /opt/barkandambrosiagallery/scripts/prod_firewall.sh", UNIT)
        self.assertIn("ExecStop=/bin/bash /opt/barkandambrosiagallery/scripts/prod_firewall.sh --off", UNIT)
        self.assertIn("WantedBy=multi-user.target", UNIT)

    def test_the_docs_say_how_to_install_check_and_undo_it(self):
        self.assertIn("## Only Cloudflare reaches the site", DOCS)
        self.assertIn("sudo bash scripts/prod_firewall.sh", DOCS)
        self.assertIn("sudo systemctl enable --now prod-firewall", DOCS)
        self.assertIn("sudo systemctl disable --now prod-firewall", DOCS)
        self.assertIn("## If the site hangs but the server is idle", DOCS)


class GunicornRecyclingTests(SimpleTestCase):
    def test_workers_are_replaced_after_a_number_of_requests(self):
        self.assertIn('pixi run gunicorn --workers 9 --timeout 600 --max-requests 2000 --max-requests-jitter 200"',
                      COMPOSE)
