# Raspberry Pi Deployment Guide (Production)

This guide deploys `medirater` on Raspberry Pi OS with:

- `systemd` for process supervision
- `caddy` for HTTPS and reverse proxy
- Domain-based access required by WebAuthn/passkeys

## Target architecture

- Public traffic: `https://<APP_DOMAIN>` on ports `443` and `80`
- Reverse proxy: Caddy
- App process: `uvicorn` bound to `127.0.0.1:8000` only
- Database: SQLite file in project folder (`<APP_DIR>/app.db`)

## 1) Collect required values first

Decide these values before running commands:

- `<APP_DOMAIN>`: your app domain/subdomain (for example `app.example.com`)
- `<PUBLIC_IP>`: fixed public IP for your building/network
- `<PI_USER>`: Linux user running the app
- `<APP_DIR>`: app checkout path, for example `/home/<PI_USER>/Projects/medirater`

## 2) Prepare Raspberry Pi OS and required tools

Check `systemd` first:

```bash
ps -p 1 -o comm=
systemctl --version | head -n 1
```

Install base packages only when missing:

```bash
sudo apt update
sudo apt upgrade -y
sudo apt install -y ca-certificates gnupg

command -v curl >/dev/null || sudo apt install -y curl
command -v git >/dev/null || sudo apt install -y git
command -v sqlite3 >/dev/null || sudo apt install -y sqlite3
command -v openssl >/dev/null || sudo apt install -y openssl
command -v dig >/dev/null || sudo apt install -y dnsutils
command -v ufw >/dev/null || sudo apt install -y ufw

curl --version | head -n 1
git --version
sqlite3 --version
openssl version
dig -v | head -n 1
ufw --version
```

## 3) Install `uv` (Python package manager/runner)

Check first. If missing, install. If installed, print version:

```bash
if command -v uv >/dev/null; then
  uv --version
else
  curl -LsSf https://astral.sh/uv/install.sh | sh
  echo 'export PATH="$HOME/.local/bin:$PATH"' >> ~/.profile
  source ~/.profile
  uv --version
fi
```

If `uv` is not found after restart, log out/in and run:

```bash
echo "$PATH"
command -v uv
```

## 4) Get app code and dependencies

```bash
mkdir -p ~/Projects
cd ~/Projects
git clone <YOUR_REPO_URL> medirater
cd ~/Projects/medirater
uv sync --frozen
```

If you do not use a locked environment yet, use:

```bash
uv sync
```

## 5) Configure production environment

```bash
cd ~/Projects/medirater
cp .env.example .env
openssl rand -hex 32
```

Use the generated random string as `APP_TOKEN_PEPPER` in `.env`.

Edit `.env`:

```bash
nano .env
```

Set at least:

```env
APP_TOKEN_PEPPER=<long-random-secret>

APP_WEBAUTHN_ORIGIN=https://<APP_DOMAIN>
APP_WEBAUTHN_RP_ID=<APP_DOMAIN>
APP_WEBAUTHN_RP_NAME=Medirater

APP_SESSION_COOKIE_SECURE=true
APP_CORS_ALLOW_ORIGINS=https://<APP_DOMAIN>
APP_INSECURE_DEV_WEBAUTHN=false
```

Notes:

- Keep `APP_DATABASE_URL=sqlite:///./app.db` to store SQLite in the repo folder. No change needed for DB location.
- `APP_WEBAUTHN_ORIGIN` must exactly match the public origin users open in browser.
- `APP_WEBAUTHN_RP_ID` must be a valid domain suffix for that origin. Using exact host is the safest default.
- Do not use raw IP for passkeys.

## 6) Create `systemd` service for app

Find your `uv` path:

```bash
command -v uv
```

Create unit file:

```bash
sudo nano /etc/systemd/system/medirater.service
```

Paste and adjust placeholders:

```ini
[Unit]
Description=Medirater FastAPI
After=network.target

[Service]
Type=simple
User=<PI_USER>
Group=<PI_USER>
WorkingDirectory=/home/<PI_USER>/Projects/medirater
EnvironmentFile=/home/<PI_USER>/Projects/medirater/.env
Environment=PATH=/home/<PI_USER>/.local/bin:/usr/local/bin:/usr/bin:/bin
ExecStart=/home/<PI_USER>/.local/bin/uv run uvicorn app.main:app --host 127.0.0.1 --port 8000 --proxy-headers --forwarded-allow-ips=127.0.0.1
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
```

Enable and start:

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now medirater
sudo systemctl status medirater --no-pager
curl -i http://127.0.0.1:8000/health
```

Expected health response:

```text
HTTP/1.1 200 OK
{"ok":true}
```

## 7) Install Caddy (official package)

Check first. If missing, install. If installed, print version:

```bash
if command -v caddy >/dev/null; then
  caddy version
else
  sudo apt install -y debian-keyring debian-archive-keyring apt-transport-https curl
  curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' | sudo gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
  curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' | sudo tee /etc/apt/sources.list.d/caddy-stable.list >/dev/null
  sudo chmod o+r /usr/share/keyrings/caddy-stable-archive-keyring.gpg
  sudo chmod o+r /etc/apt/sources.list.d/caddy-stable.list
  sudo apt update
  sudo apt install -y caddy
  caddy version
fi
```

## 8) Configure Caddy reverse proxy

```bash
sudo nano /etc/caddy/Caddyfile
```

Use:

```caddyfile
<APP_DOMAIN> {
    reverse_proxy 127.0.0.1:8000
}
```

Apply config:

```bash
sudo caddy validate --config /etc/caddy/Caddyfile
sudo systemctl enable --now caddy
sudo systemctl reload caddy
sudo systemctl status caddy --no-pager
```

## 9) Configure DNS at your domain registrar

In your registrar DNS panel:

1. Ensure your domain uses the nameservers where you manage records.
2. Create an `A` record:
   - Name/Host: the subdomain label (for `app.example.com`, host is `app`)
   - Value: `<PUBLIC_IP>`
3. Remove conflicting old `A`/`CNAME` records for the same host.
4. If you do not use IPv6 on this network, remove conflicting `AAAA` for the same host.

Verify DNS propagation:

```bash
dig +short A <APP_DOMAIN> @1.1.1.1
dig +short A <APP_DOMAIN> @8.8.8.8
```

Both should return `<PUBLIC_IP>`.

## 10) Router/NAT and network rules

Only needed if your Pi is behind NAT/router. If your domain already works from public cellular network, this is already done.

Forward:

- TCP `80` -> Pi LAN IP
- TCP `443` -> Pi LAN IP

Do not forward `8000`.

## 11) Host firewall (optional hardening)

If `ufw` is missing, install it first. If installed, apply rules:

```bash
command -v ufw >/dev/null || sudo apt install -y ufw
sudo ufw --version
sudo ufw allow OpenSSH
sudo ufw allow 80/tcp
sudo ufw allow 443/tcp
sudo ufw --force enable
sudo ufw status verbose
```

Optional egress lockdown (recommended if you want to reduce abuse risk from a compromised Pi):

```bash
sudo ufw default deny outgoing
sudo ufw allow out 53/udp
sudo ufw allow out 53/tcp
sudo ufw allow out 80/tcp
sudo ufw allow out 443/tcp
sudo ufw allow out 123/udp
sudo ufw reload
sudo ufw status verbose
```

What these outbound rules cover:

- `53/tcp` + `53/udp`: DNS lookups
- `80/tcp` + `443/tcp`: package downloads, git over HTTPS, certificate operations
- `123/udp`: clock sync (recommended for TLS reliability)

Quick validation after enabling outbound deny:

```bash
dig +short example.com @1.1.1.1
curl -I https://<APP_DOMAIN>/health
sudo apt update
```

## 12) Final verification checklist

Check service states:

```bash
sudo systemctl is-enabled medirater caddy
sudo systemctl is-active medirater caddy
```

Check local app:

```bash
curl -i http://127.0.0.1:8000/health
```

Check public HTTPS:

```bash
curl -i https://<APP_DOMAIN>/health
```

Check runtime config loaded from `.env`:

```bash
cd ~/Projects/medirater
uv run python -c "from app.config import get_settings; s=get_settings(); print(s.webauthn_origin, s.webauthn_rp_id, s.session_cookie_secure, s.insecure_dev_webauthn, s.cors_allow_origins)"
```

Expected shape:

```text
https://<APP_DOMAIN> <APP_DOMAIN> True False https://<APP_DOMAIN>
```

Check certificate served for your host:

```bash
echo | openssl s_client -servername <APP_DOMAIN> -connect <APP_DOMAIN>:443 2>/dev/null | openssl x509 -noout -issuer -subject -dates
```

## 13) Bootstrap first superadmin

```bash
cd ~/Projects/medirater
uv run python scripts/create_bootstrap_token.py --expires-in-minutes 120
```

Open:

- `https://<APP_DOMAIN>/admin/signup`

Use the token once.

## 14) Routine operations

Restart services:

```bash
sudo systemctl restart medirater
sudo systemctl restart caddy
```

Tail logs:

```bash
sudo journalctl -u medirater -f
sudo journalctl -u caddy -f
```

Update app:

```bash
cd ~/Projects/medirater
git pull
uv sync --frozen
sudo systemctl restart medirater
```

Create SQLite backup:

```bash
mkdir -p ~/Projects/medirater/backups
backup="$HOME/Projects/medirater/backups/app-$(date +%F-%H%M%S).db"
sqlite3 ~/Projects/medirater/app.db ".backup $backup"
```

## 15) Common problems and fixes

### Caddy returns `502 Bad Gateway`

Usually Caddy cannot reach app upstream.

Check:

```bash
sudo systemctl status medirater --no-pager
ss -ltnp | grep :8000
curl -i http://127.0.0.1:8000/health
sudo journalctl -u caddy -n 100 --no-pager
```

### Public page is empty

Check app and browser errors:

```bash
sudo journalctl -u medirater -n 120 --no-pager
sudo journalctl -u caddy -n 120 --no-pager
```

Also verify `.env` values match your real HTTPS host and restart `medirater`.

### Passkey registration/login fails with domain/origin errors

Most common causes:

- Using raw IP instead of HTTPS domain
- `APP_WEBAUTHN_ORIGIN` mismatch with browser URL
- `APP_WEBAUTHN_RP_ID` mismatch
- Old passkeys created under a different origin

If origin changed, re-register passkeys under the current domain.

## References

- uv installation: https://docs.astral.sh/uv/getting-started/installation/
- Caddy install (Debian/Ubuntu/Raspbian): https://caddyserver.com/docs/install
- Caddy + Caddyfile docs: https://caddyserver.com/docs/caddyfile
