# After deploying: what to do once

Deploying `main` (publish a release, as usual) applies the database migrations by itself and deletes nothing.
Two things need a person, once. A script does both:

```
ssh you@server
cd /opt/barkandambrosiagallery
bash scripts/post_deploy_setup.sh
```

1. **Email for "Request access".** The script checks `.env.prod` for `EMAIL_HOST`, `EMAIL_HOST_USER`, `EMAIL_HOST_PASSWORD` and
   `DEFAULT_FROM_EMAIL`, asks for any that are missing, saves them and restarts the web container. You need an SMTP account
   (university mail, Gmail with an app password, SendGrid ...). Without it **nobody can finish asking for an account or reset a
   password**, because both work through an emailed link. Send yourself a test request before announcing it.
2. **The published interactions dataset** is loaded into the database (`import_pathogen_interactions`). The review page needs it
   to tell reviewers when a claim is already published. It is safe to run again and never touches accepted or uploaded rows.

It also lists who can approve access requests. **Every superuser can**, and every superuser with an email address is emailed when a
request arrives, in addition to `ACCESS_REQUEST_RECIPIENTS` (default `gmarais@ufl.edu`). Make someone a superuser under
*My Account → edit user → role*.

## Only Cloudflare reaches the site

Visitors reach the site through Cloudflare, which absorbs bots and slow or broken connections before they get to the
server. But the server's ports were open too, so anyone who knew its IP address could skip Cloudflare: on port 80
(the one Cloudflare uses) and on port 8000 (published by `docker-compose.yml` for local development; Compose adds it to
production's port 80). On 2026-11-09 one bot doing that held 80 connections open. Gunicorn has 36 request slots
(9 workers × 4 threads), so most of them were taken and the site hung for everyone while the server sat idle.

`scripts/prod_firewall.sh` lets only Cloudflare's published address ranges reach port 80 and closes port 8000 to the
internet (IPv4 and IPv6). `scripts/prod-firewall.service` applies it again at every boot. It uses Docker's
`DOCKER-USER` chain because Docker publishes ports underneath ufw, so a ufw rule would not stop them. SSH, staging and
the server talking to the site locally are untouched. If Cloudflare's list can't be fetched, it changes nothing.

Once, on the server (after a release with this script has been deployed):

```
sudo apt-get install -y ipset
cd /opt/barkandambrosiagallery
sudo bash scripts/prod_firewall.sh
sudo cp scripts/prod-firewall.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now prod-firewall
```

Check it:
- `sudo bash scripts/prod_firewall.sh --status` lists the rules and the number of Cloudflare ranges;
- from your own computer, `curl -m 10 http://<server IP>/` and `curl -m 10 http://<server IP>:8000/` now time out
  (in Windows PowerShell type `curl.exe`);
- https://barkandambrosiagallery.org still opens (it comes through Cloudflare).

Once it is on, a one-off block for a single address is no longer needed:
`sudo iptables -D DOCKER-USER -s <address> -j DROP` removes one.
`sudo systemctl restart prod-firewall` fetches Cloudflare's ranges again (so does a reboot).
To undo: `sudo systemctl disable --now prod-firewall` (it runs `--off`), then
`sudo rm /etc/systemd/system/prod-firewall.service`.

## If the site hangs but the server is idle

Pages time out (Cloudflare shows error 524) while `uptime` and `free -h` look calm: gunicorn's request slots are taken.
These only read:

```
cd /opt/barkandambrosiagallery
PID=$(docker inspect -f '{{.State.Pid}}' barkandambrosiagallery-web-1)
nsenter -t $PID -n ss -tan | awk 'NR>1{print $1}' | sort | uniq -c       # many CLOSE-WAIT: requests waiting in line
nsenter -t $PID -n ss -tn state established | awk 'NR>1{print $4}' | sed 's/:[0-9]*$//' | sort | uniq -c | sort -rn | head
```

The second list shows who holds the connections. Cloudflare's addresses are listed at https://www.cloudflare.com/ips/;
one other address holding dozens means something is bypassing Cloudflare (check `prod_firewall.sh --status`). To see
what each worker thread is waiting on, without changing the site (it starts a throwaway helper container):

```
docker run --rm --pid=container:barkandambrosiagallery-web-1 --cap-add SYS_PTRACE python:3.12-slim sh -c '
pip install -q py-spy 2>/dev/null
for d in /proc/[0-9]*; do p=${d#/proc/}
  if tr "\0" " " < $d/cmdline 2>/dev/null | grep -q "gunicorn beetlesgallery"; then
    echo "===== process $p"; py-spy dump --pid $p 2>&1 | head -60; fi
done' > /root/pyspy.txt 2>&1
```

`docker compose -f docker-compose.yml -f docker-compose.prod.yml restart web` frees every slot (about a minute of
downtime). Gunicorn also replaces each worker after about 2000 requests (`--max-requests` in `docker-compose.prod.yml`),
so stuck workers clear up on their own over time.
