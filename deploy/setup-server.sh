#!/bin/sh
# One-time server setup (Ubuntu), run as a sudo user after the repo is synced to /opt/tietrace.
set -e
sudo apt-get update
sudo apt-get install -y docker.io docker-compose-v2 nginx certbot python3-certbot-nginx rsync
sudo systemctl enable --now docker nginx
sudo usermod -aG docker "$USER"
sudo cp /opt/tietrace/deploy/nginx.conf /etc/nginx/sites-available/tietrace
sudo ln -sf /etc/nginx/sites-available/tietrace /etc/nginx/sites-enabled/tietrace
sudo rm -f /etc/nginx/sites-enabled/default
sudo nginx -t && sudo systemctl reload nginx
