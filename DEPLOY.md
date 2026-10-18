# Deploying the TieTrace public demo (AWS EC2, Ubuntu 24.04)

*Last updated by Aaryan, Oct 18 2026: first version (public mode `TIETRACE_PUBLIC=1`, Caddy, systemd; W7 in `WebApp.md`).*

Target: one EC2 **m7i-flex.large** (2 vCPU, 8 GB) running Ubuntu 24.04, user `ubuntu`, repo at `/home/ubuntu/TieTrace`.
Everything listens on 127.0.0.1 except Caddy (80/443). Visitors bring their own model API key; the server has no
model key and no Claude login.

**Before you start:** public mode is on branch `phase2`. Merge it into `main` and push, or the server will not have it.

## What public mode does (`TIETRACE_PUBLIC=1`)

Enforced by the server (`web/app.py`), not only hidden on the page:

- `/api/ask` (local `claude -p`), `/api/login` and `/api/auth` return 403 "Not available on the public demo: bring your own key"; the providers list says `local_claude: false`; the host's own Gemini key is never used.
- Every `/api/case/*` (the case wizard) returns 403; the "+ New case" item is hidden.
- `/api/confirm`, `/api/approve_note` and `/api/record_verdict` return 403; the page says "Approvals are turned off on the public demo" instead of the buttons.
- The app refuses to start unless `TIETRACE_NEWSROOM` starts with `demo` (never `alpha`).
- `/api/agent/ask`: at most 10 runs per hour and 1 at once per client IP (in memory, reset on restart; 1 is below the app's 2 global run slots, so one visitor never holds both); 429 with a readable message. With `TIETRACE_TRUST_PROXY=1` the IP is the **last** `X-Forwarded-For` hop (the one Caddy adds) and the scheme is the last `X-Forwarded-Proto` hop; without it both headers are ignored. That is only true because the service runs uvicorn with `--no-proxy-headers`: uvicorn's default trusts these headers from 127.0.0.1 and rewrites the client address itself, which anyone on an SSH tunnel could forge.
- Host and Origin checks allow 127.0.0.1, localhost and the exact names in `TIETRACE_ALLOWED_HOSTS` (comma-separated, no wildcard).
- A banner: "Public demo: bring your own API key. Your key and story are not saved on the server; check results go into a shared demo memory that may be reset."

## 1. Launch the instance and the security group

- AMI: Ubuntu Server 24.04 LTS (x86_64). Type: m7i-flex.large. Disk: 30 GB gp3 is enough (graphs and caches are about 3 GB).
- Security group, inbound rules:
  - 22 (SSH) from **My IP** only;
  - 80 and 443 from anywhere (0.0.0.0/0 and ::/0), only if you use Caddy (skip both for the SSH-tunnel option);
  - **never** open 6379 (FalkorDB, no password), 3001 (FalkorDB Browser) or 8000 (the app without basic auth).
- With a domain: point an A record at the instance's public IP. The public IP changes on every stop/start unless you attach an Elastic IP (which is billed too, see section 10).

```bash
ssh -i ~/path/to/key.pem ubuntu@<PUBLIC_IP>
```

## 2. Install Docker (official apt repository, docs.docker.com/engine/install/ubuntu)

```bash
sudo apt update
sudo apt install -y ca-certificates curl unzip git
sudo install -m 0755 -d /etc/apt/keyrings
sudo curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
sudo chmod a+r /etc/apt/keyrings/docker.asc
sudo tee /etc/apt/sources.list.d/docker.sources <<EOF
Types: deb
URIs: https://download.docker.com/linux/ubuntu
Suites: $(. /etc/os-release && echo "${UBUNTU_CODENAME:-$VERSION_CODENAME}")
Components: stable
Architectures: $(dpkg --print-architecture)
Signed-By: /etc/apt/keyrings/docker.asc
EOF
sudo apt update
sudo apt install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
sudo docker run --rm hello-world
```

The commands below use `sudo docker`, so the `ubuntu` user does not need to join the `docker` group.

## 3. Install uv (docs.astral.sh/uv)

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
source ~/.local/bin/env      # or log out and in again; uv is now ~/.local/bin/uv
uv --version
```

## 4. Install Caddy (official apt repository, caddyserver.com/docs/install)

Skip this for the SSH-tunnel option.

```bash
sudo apt install -y debian-keyring debian-archive-keyring apt-transport-https curl
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' | sudo gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' | sudo tee /etc/apt/sources.list.d/caddy-stable.list
sudo chmod o+r /usr/share/keyrings/caddy-stable-archive-keyring.gpg
sudo chmod o+r /etc/apt/sources.list.d/caddy-stable.list
sudo apt update
sudo apt install -y caddy
```

The package starts a `caddy` systemd service at once (with its default page on port 80).

## 5. Clone the repo and install the Python packages

```bash
cd ~
git clone https://github.com/Aaryan-Degama/TieTrace.git
cd ~/TieTrace
git checkout main
uv sync          # downloads Python 3.13 if the system has none, then the packages
```

If the repo is private, clone with a GitHub token or a deploy key.

## 6. FalkorDB (the CLAUDE.md command: 127.0.0.1 only, AOF on, pinned v4.22.1)

```bash
sudo docker run -d --name falkordb -p 127.0.0.1:6379:6379 -p 127.0.0.1:3001:3000 -v falkordb-data:/var/lib/falkordb/data -e REDIS_ARGS="--appendonly yes --appendfsync everysec" --stop-timeout 120 falkordb/falkordb:v4.22.1
sudo docker exec falkordb redis-cli CONFIG GET appendonly        # must print: appendonly / yes
sudo docker update --restart unless-stopped falkordb             # comes back after an instance stop/start
```

Never `:latest`. The Browser on 3001 is reachable only through SSH (`ssh -L 3001:127.0.0.1:3001 ...`).

## 7. Copy the raw caches up and rebuild both cases

From **your own machine**, in the repo folder:

```bash
scp -i ~/path/to/key.pem data/tietrace-data.zip data/tietrace-pmi-raw.zip ubuntu@<PUBLIC_IP>:~/TieTrace/data/
```

On the server (create `data/` first if scp complains: `mkdir -p ~/TieTrace/data`). Both zips hold paths relative to
the repo root (`data/raw/...` for obesity-coke, `cases/philip-morris-lung/raw/...` for Philip Morris), which is
where the loader reads its cache, so unzip them **from the repo root**:

```bash
cd ~/TieTrace
unzip -o data/tietrace-data.zip          # -> data/raw/*.jsonl            (23 entries)
unzip -o data/tietrace-pmi-raw.zip       # -> cases/philip-morris-lung/raw/*.jsonl  (23 entries)
```

**The loader needs a `.env` file even for a cache-only rebuild:** it reads `OPENALEX_API_KEY` from `.env` before it
builds, and stops if the file is missing. With complete caches it makes zero API calls, so a placeholder is enough
and keeps the real key off the server:

```bash
cd ~/TieTrace
install -m 600 /dev/null .env
echo 'OPENALEX_API_KEY=cache-only-no-key' > .env
```

Only if a cache is missing (the build then calls OpenAlex) put the real key there instead: open `.env` with
`nano .env` and write `OPENALEX_API_KEY=<your key>` (the file stays `chmod 600`, owner `ubuntu`). Never commit it,
never paste it into a command line (it would land in the shell history).

Rebuild, one case at a time (each graph is about 0.8 GB in RAM):

```bash
cd ~/TieTrace
FALKORDB_PORT=6379 uv run python loader.py --case obesity-coke --rebuild
FALKORDB_PORT=6379 uv run python loader.py --case philip-morris-lung --rebuild
```

Each run ends with "API calls this run: 0" when the cache was complete.

## 8. The web app as a systemd service

```bash
cd ~/TieTrace
sudo cp deploy/tietrace.service /etc/systemd/system/tietrace.service
sudo systemctl edit tietrace
```

In the editor that opens, add (use your domain, or the public IP for the IP-only option):

```ini
[Service]
Environment=TIETRACE_ALLOWED_HOSTS=tietrace.example.org
```

With a domain (HTTPS), also add these two lines: the app then refuses an API key that arrived over plain http.

```ini
Environment=TIETRACE_DEPLOYED=1
Environment=TIETRACE_ALLOWED_ORIGIN=https://tietrace.example.org
```

(Leave these two out for the IP-only option and for the SSH tunnel: those are plain http.)

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now tietrace
systemctl status tietrace --no-pager
curl -s http://127.0.0.1:8000/api/agent/providers     # must show "public":true and "local_claude":false
journalctl -u tietrace -n 50 --no-pager                # logs; no key is ever logged
```

The service runs as `ubuntu` (not root) with `TIETRACE_PUBLIC=1`, `TIETRACE_NEWSROOM=demo`, `FALKORDB_PORT=6379`,
`TIETRACE_TRUST_PROXY=1`, on 127.0.0.1:8000, and starts uvicorn with `--no-proxy-headers` (see the top of this file).

## 9. Caddy: HTTPS and basic auth

The basic-auth hash goes **straight into `/etc/caddy/Caddyfile`**, never into Caddy's environment: the apt
`caddy.service` starts `caddy run --environ`, which prints its whole environment to the journal, and the `ubuntu`
user can read the journal (group `adm`). **Choose a strong password: 16 or more random characters** (the command
below makes one with 24). The repo's `deploy/Caddyfile` and `deploy/Caddyfile.ip-only` are templates with
`DOMAIN_HERE`, `USER_HERE` and `HASH_HERE`; these commands fill them in.

```bash
PW=$(openssl rand -base64 18)                          # 24 random characters
echo "Basic-auth password (save it now, it is not stored anywhere): $PW"
HASH=$(printf '%s\n' "$PW" | caddy hash-password)      # bcrypt; the password goes on stdin, not on a command line
unset PW
AUTH_USER=judge
DOMAIN=tietrace.example.org                            # your domain (not used by the IP-only file)
```

Then pick one Caddyfile:

- **With a domain (recommended):** automatic HTTPS (Let's Encrypt; ports 80 and 443 must be open and the A record must point here).
  ```bash
  sed -e "s|DOMAIN_HERE|$DOMAIN|" -e "s|USER_HERE|$AUTH_USER|" -e "s|HASH_HERE|$HASH|" ~/TieTrace/deploy/Caddyfile | sudo tee /etc/caddy/Caddyfile >/dev/null
  ```
- **IP only, no domain:** plain HTTP on port 80. **Warning: unencrypted.** The basic-auth password and every visitor's API key cross the network in clear text. Prefer the SSH tunnel (section 11). If you still use it, set `TIETRACE_ALLOWED_HOSTS` to the public IP (section 8) and use throwaway keys with a spend limit.
  ```bash
  sed -e "s|USER_HERE|$AUTH_USER|" -e "s|HASH_HERE|$HASH|" ~/TieTrace/deploy/Caddyfile.ip-only | sudo tee /etc/caddy/Caddyfile >/dev/null
  ```

(A bcrypt hash uses only `$ . / A-Z a-z 0-9`, so the `|` in the sed commands is safe.)

```bash
unset HASH
sudo chown root:caddy /etc/caddy/Caddyfile
sudo chmod 640 /etc/caddy/Caddyfile
sudo caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile
sudo systemctl restart caddy
journalctl -u caddy -n 50 --no-pager
```

Open `https://tietrace.example.org` (or `http://<PUBLIC_IP>`): the browser asks for the user and password, then
the page shows the public-demo banner. SSE (the live steps) is not buffered: Caddy flushes `text/event-stream`
responses at once by itself. (No `flush_interval -1`: its low-latency mode would also keep a check running on the
server after the visitor closed the page.)

## 10. Stop it afterwards (billing)

- **Stop** the instance when the demo is over: EC2 console → Instances → select → Instance state → **Stop instance** (or `aws ec2 stop-instances --instance-ids <id>`). A stopped instance bills no compute.
- A stopped instance **still bills its EBS disk** (per GB-month; the free tier covers only 30 GB of gp2/gp3 for 12 months) and any Elastic IP or public IPv4 address kept for it.
- To end all charges: **Terminate** the instance (its root disk is deleted if "Delete on termination" is on, the default), delete leftover volumes and snapshots, and release any Elastic IP.
- After a stop/start: FalkorDB comes back by itself (restart policy from section 6), and so do `tietrace` and `caddy` (enabled services). The public IP changes unless you use an Elastic IP; update the DNS record and `TIETRACE_ALLOWED_HOSTS` if it does.

## 11. Alternative: SSH tunnel only (no Caddy, nothing public)

Skip sections 4 and 9, leave ports 80 and 443 closed (only 22 from your IP), and run section 8 without
`TIETRACE_ALLOWED_HOSTS` or `TIETRACE_DEPLOYED`, but with `Environment=TIETRACE_TRUST_PROXY=0` in the override:
with no proxy in front, an `X-Forwarded-For` header would come from the visitor. From your machine:

```bash
ssh -i ~/path/to/key.pem -N -L 8000:127.0.0.1:8000 ubuntu@<PUBLIC_IP>
```

Then open http://127.0.0.1:8000. The traffic is encrypted by SSH and only people with the key can reach it. If port
8000 is busy on your own machine, use another local port: `-L 8001:127.0.0.1:8000` and open http://127.0.0.1:8001
(127.0.0.1 is allowed on any port).

## Known limits

- **Check results are saved in a shared demo memory, as the banner says.** No key and no story file is saved, but the model's allowed tools `open_case`, `check_case`, `recheck` and `draft_note` write cases, checks and draft notes into the memory graph `newsroom:demo` in FalkorDB, which every visitor shares. Reset it after the demo, or whenever needed (in redis-cli: `GRAPH.DELETE newsroom:demo`).
- The rate limit is in memory, per process: a restart resets it, and many visitors behind one NAT share one limit.
- **The hourly quota counts refused and failed runs too:** a run is counted as soon as the app accepts it, also when it is then refused as BUSY (both global run slots taken) or fails at the provider (a wrong key, a quota error). Only requests rejected before that (no key, bad story, 403, 429) are not counted.
- The agent loop runs at most 8 tool calls per model turn and 40 per check; extra calls get a "too many tool calls" error result, and a model that keeps asking after 40 is stopped.
