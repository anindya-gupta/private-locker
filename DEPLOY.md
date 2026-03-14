# Deploy Vault to Google Cloud

Get your Vault running 24/7 on Google Cloud so you can access it from your phone, tablet, or any browser — anywhere in the world.

**Cost:** Free (GCP e2-micro is in the Always Free tier) + ~$10/year for a domain name.

---

## Prerequisites

- A Google account
- A domain name (buy one at [Namecheap](https://namecheap.com) for ~$10/year, or get a free one at [DuckDNS](https://duckdns.org))
- Your OpenAI API key

---

## Step 1: Create a Google Cloud VM

1. Go to [Google Cloud Console](https://console.cloud.google.com)
2. Create a new project (or use the default one)
3. Enable the Compute Engine API if prompted
4. Go to **Compute Engine > VM Instances > Create Instance**
5. Configure:
   - **Name:** `vault`
   - **Region:** `us-central1` (or `us-west1` / `us-east1` — these qualify for free tier)
   - **Machine type:** `e2-micro` (2 vCPU shared, 1 GB RAM) — **free tier**
   - **Boot disk:** Click "Change"
     - OS: `Ubuntu 22.04 LTS`
     - Size: `30 GB` (free tier includes 30 GB)
   - **Firewall:** Check both "Allow HTTP traffic" and "Allow HTTPS traffic"
6. Click **Create**

Note the **External IP** address shown after the VM starts (e.g., `34.123.45.67`).

---

## Step 2: Point your domain

Go to your domain registrar (Namecheap, Google Domains, DuckDNS, etc.) and add an **A record**:

```
Type: A
Name: vault (or @ for root domain)
Value: 34.123.45.67  (your VM's external IP)
TTL: 300
```

This makes `vault.yourdomain.com` point to your Google Cloud VM.

---

## Step 3: SSH into the VM and install Docker

In the Google Cloud Console, click the **SSH** button next to your VM instance. A browser terminal opens.

Run these commands:

```bash
# Update the system
sudo apt update && sudo apt upgrade -y

# Install Docker
curl -fsSL https://get.docker.com | sudo sh
sudo usermod -aG docker $USER

# Install Docker Compose plugin
sudo apt install -y docker-compose-plugin

# Log out and back in for group changes
exit
```

SSH back in, then verify:

```bash
docker --version
docker compose version
```

---

## Step 4: Deploy Vault

```bash
# Clone the repo
git clone https://github.com/YOUR_USERNAME/vault.git
cd vault

# Create environment file with your OpenAI key
echo "OPENAI_API_KEY=sk-your-actual-key-here" > .env

# Set your domain in the Caddyfile
sed -i 's/vault.yourdomain.com/vault.YOUR-ACTUAL-DOMAIN.com/' Caddyfile

# Also set it as an env var for Caddy
echo "VAULT_DOMAIN=vault.YOUR-ACTUAL-DOMAIN.com" >> .env

# Build and start everything
docker compose up -d --build
```

First build takes 5-10 minutes (downloading Python packages + ML models). Subsequent starts are instant.

Check that everything is running:

```bash
docker compose ps
docker compose logs -f vault
```

---

## Step 5: Initialize Vault

Open `https://vault.yourdomain.com` in your browser. You should see the setup page.

- Create your master password (at least 8 characters)
- Click "Initialize Vault"

That's it — Vault is live.

---

## Step 6: Add to your phone as an app

### iPhone (Safari)
1. Open `https://vault.yourdomain.com` in Safari
2. Tap the Share button (square with arrow)
3. Scroll down and tap **"Add to Home Screen"**
4. Tap **Add**

### Android (Chrome)
1. Open `https://vault.yourdomain.com` in Chrome
2. Tap the three-dot menu
3. Tap **"Add to Home screen"** or **"Install app"**
4. Tap **Add**

The app will appear on your home screen with the Vault icon. It opens in full-screen mode — no browser chrome.

---

## Managing your deployment

### View logs
```bash
docker compose logs -f
```

### Restart
```bash
docker compose restart
```

### Update Vault (after pulling new code)
```bash
git pull
docker compose up -d --build
```

### Stop everything
```bash
docker compose down
```

### Backup your data
The encrypted data lives in a Docker volume. To back it up:
```bash
docker compose exec vault vault backup
# Or copy the volume directly:
docker run --rm -v vault_vault_data:/data -v $(pwd):/backup alpine tar czf /backup/vault-backup.tar.gz /data
```

---

## Security notes

- All data is AES-256-GCM encrypted at rest — even if the VM is compromised, data is unreadable without your master password
- All traffic is HTTPS (Caddy auto-provisions Let's Encrypt certificates)
- Unlock endpoint is rate-limited (5 attempts per minute) to prevent brute-force
- Security headers (X-Frame-Options, HSTS, etc.) are set on all responses
- The VM firewall only allows ports 80 and 443
- Consider enabling [OS Login](https://cloud.google.com/compute/docs/instances/managing-instance-access) for SSH access to your VM (uses your Google account instead of SSH keys)

---

## Troubleshooting

**"Site can't be reached"**
- DNS may not have propagated yet. Wait 5-10 minutes after setting the A record.
- Check that firewall rules allow HTTP/HTTPS: `gcloud compute firewall-rules list`

**"502 Bad Gateway"**
- Vault container might still be starting. Check: `docker compose logs vault`

**"Certificate error"**
- Caddy needs ports 80 and 443 to be open for Let's Encrypt verification.
- Make sure no other service is using those ports: `sudo lsof -i :80`

**Out of memory on e2-micro**
- sentence-transformers loads an ML model into RAM. If 1GB isn't enough:
  - Add a 1GB swap file: `sudo fallocate -l 1G /swapfile && sudo chmod 600 /swapfile && sudo mkswap /swapfile && sudo swapon /swapfile`
  - Add to /etc/fstab for persistence: `echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab`
