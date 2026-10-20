"""
Only Cloudflare reaches staging's port 8080 (scripts/staging_firewall.sh, docs/staging.md): the rule sits in Docker's
DOCKER-USER chain (Docker publishes ports underneath ufw), matches the port people call, keeps Cloudflare's ranges in
ipsets, never touches anything if the ranges can't be fetched, and is applied again at boot by a systemd unit.
"""
from pathlib import Path

from django.test import SimpleTestCase

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = (ROOT / "scripts" / "staging_firewall.sh").read_text(encoding="utf-8")
UNIT = (ROOT / "scripts" / "staging-firewall.service").read_text(encoding="utf-8")


class StagingFirewallTests(SimpleTestCase):
    def test_it_filters_the_published_port_in_dockers_own_chain(self):
        self.assertIn('PORT="${STAGING_PORT:-8080}"', SCRIPT)
        self.assertIn('RULE4=(-p tcp -m conntrack --ctorigdstport "$PORT" --ctdir ORIGINAL -m set ! --match-set "$SET4" src -j DROP)', SCRIPT)
        self.assertIn('iptables -I DOCKER-USER 1 "${RULE4[@]}"', SCRIPT)
        self.assertIn('ip6tables -I INPUT 1 "${RULE6[@]}"', SCRIPT)   # IPv6 reaches Docker's proxy, not FORWARD

    def test_it_lets_only_cloudflares_published_ranges_through(self):
        self.assertIn("https://www.cloudflare.com/ips-v4", SCRIPT)
        self.assertIn("https://www.cloudflare.com/ips-v6", SCRIPT)
        self.assertIn('ipset swap "$set-new" "$set"', SCRIPT)   # the new list takes over in one step

    def test_a_list_it_cannot_fetch_changes_nothing(self):
        self.assertIn('|| { echo "Could not fetch $url: nothing changed"; exit 1; }', SCRIPT)
        self.assertIn('[ "$count" -ge "$min" ] ||', SCRIPT)

    def test_running_it_again_adds_no_second_rule_and_it_can_be_undone(self):
        self.assertIn('iptables -C DOCKER-USER "${RULE4[@]}" >/dev/null 2>&1 ||', SCRIPT)
        self.assertIn("--off)", SCRIPT)
        self.assertIn("--status)", SCRIPT)

    def test_the_boot_unit_runs_it_after_docker_and_removes_it_on_stop(self):
        self.assertIn("After=docker.service network-online.target", UNIT)
        self.assertIn("ExecStart=/bin/bash /opt/barkandambrosia_staging/app/scripts/staging_firewall.sh", UNIT)
        self.assertIn("ExecStop=/bin/bash /opt/barkandambrosia_staging/app/scripts/staging_firewall.sh --off", UNIT)
        self.assertIn("WantedBy=multi-user.target", UNIT)
