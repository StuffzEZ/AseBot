# AseBot 🎨

A Discord bot that converts PNG/WebP/JPG images to `.aseprite` files — directly in Discord with a button control panel.

[![Build & Push Docker Image](https://github.com/StuffzEZ/AseBot/actions/workflows/docker.yml/badge.svg)](https://github.com/StuffzEZ/AseBot/actions/workflows/docker.yml)
![Platforms](https://img.shields.io/badge/platform-amd64%20%7C%20arm64-blue)

## What it does

1. **Watches for image uploads** in your configured channels (or all channels)
2. **Creates a thread** on the image message automatically
3. **Processes the image** through a pixel art pipeline:
   - Edge-connected background removal
   - Specific colour removal (e.g. remove white `#ffffff`)
   - Logical pixel grid detection & snapping (detects upscale factor)
   - Palette compression (reduce to N colours)
   - Transparent edge trimming + padding
   - Output resize to any target dimensions
4. **Posts both** a preview PNG and the `.aseprite` file in the thread
5. **Shows a button panel** so you can tweak settings and re-process

---

## Control Panel Buttons

| Button | What it does |
|--------|-------------|
| ▶ **Process** | Re-run the pipeline with current settings |
| ↩ **Reset** | Reset all settings back to defaults |
| 📋 **Settings** | Show current settings (ephemeral) |
| 🗑️ **Toggle Remove BG** | Toggle edge-connected background removal on/off |
| ⚙️ **BG Tolerance** | Set background flood-fill tolerance and alpha threshold |
| ❌ **Remove Colour** | Enter a hex colour to remove from the image |
| 🧹 **Clear Removed Colours** | Clear the colour removal list |
| 🎨 **Palette Size** | Select max number of colours (4 / 8 / 16 / 32 / 64 / 128 / 256) |
| 📐 **Resize** | Set output width × height in pixels (0 = auto) |
| 🔲 **Toggle Pixel Snap** | Toggle automatic logical pixel grid detection |
| 🔍 **Resample Mode** | Choose nearest / bilinear / lanczos resampling |
| ✂️ **Toggle Trim Edges** | Toggle transparent edge trimming |

---

## Slash Commands

| Command | Description |
|---------|-------------|
| `/settings` | Show current settings for this thread |
| `/removecolour #ffffff 30` | Add a colour to the removal list with tolerance |
| `/resize 64 64` | Set output dimensions |
| `/process` | Re-process with current settings |

---

## Setup

### 1. Create a Discord bot

1. Go to [discord.com/developers/applications](https://discord.com/developers/applications)
2. **New Application** → name it → go to **Bot**
3. **Reset Token** → copy it
4. Enable **Privileged Gateway Intents**: ✅ Message Content Intent
5. **OAuth2 → URL Generator** — scopes: `bot` + `applications.commands`
   - Permissions: Send Messages, Create Public Threads, Send Messages in Threads, Attach Files, Read Message History, View Channels
6. Open the generated URL to invite the bot to your server

### 2. Get your IDs

Enable Developer Mode in Discord (User Settings → Advanced → Developer Mode), then:
- **Server ID**: right-click your server icon → Copy Server ID
- **Channel ID**: right-click a channel → Copy Channel ID

### 3. Deploy with Docker (CasaOS / Portainer / any host)

```yaml
services:
  asebot:
    image: ghcr.io/stuffzez/asebot:latest
    restart: unless-stopped
    environment:
      - DISCORD_TOKEN=your_bot_token_here
      - GUILD_ID=                     # your server ID (blank = all servers)
      - CHANNEL_IDS=                  # comma-separated channel IDs (blank = all)
```

Or run the `docker-compose.yml` from this repo — edit the env values directly, no `.env` file needed.

```bash
docker compose up -d
docker compose logs -f
```

---

## Building from source

```bash
git clone https://github.com/StuffzEZ/AseBot
cd AseBot
docker build -t asebot .
```

Supported platforms: `linux/amd64`, `linux/arm64` (Raspberry Pi 4/5, CasaOS on ARM, etc.)

---

## CI / CD

Pushing to `main` or tagging a release (`v1.0.0`) triggers the GitHub Actions workflow which:

1. Builds natively on `ubuntu-latest` (amd64) and `ubuntu-24.04-arm` (arm64) in parallel
2. Merges both into a single multi-arch manifest at `ghcr.io/stuffzez/asebot`
3. Tags: `latest` (main branch), `v1.2.3`, `v1.2`, `sha-abc1234`

No QEMU emulation — both arches build natively on GitHub's runners, so builds are fast.

---

## Environment variables

| Variable | Required | Description |
|----------|----------|-------------|
| `DISCORD_TOKEN` | ✅ | Bot token from Discord Developer Portal |
| `GUILD_ID` | ❌ | Server ID to restrict to. Blank = all servers |
| `CHANNEL_IDS` | ❌ | Comma-separated channel IDs. Blank = all channels |

---

## Default processing settings

| Setting | Default |
|---------|---------|
| Remove background | ✅ On |
| BG tolerance | 80 |
| Alpha threshold | 12 |
| Remove colours | None |
| Colour tolerance | 30 |
| Pixel snap | ✅ On |
| Palette max colours | 256 (unlimited) |
| Trim edges | ✅ On |
| Crop padding | 1px |
| Output size | Auto |
| Resampling | Nearest |

---

## Notes

- Sessions are in-memory — if the bot restarts, thread sessions are lost. Re-upload the image to start a new session.
- Only the first image attachment per message is processed.
- Supported input formats: PNG, WebP, JPEG.
- The bot does not respond to images posted inside threads (to avoid loops).