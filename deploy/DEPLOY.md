# Deploying Lorehound to Oracle Cloud (Always Free)

This runs Lorehound 24/7 on a free-forever Oracle Cloud **Ampere A1 (ARM)** VM,
supervised by `systemd` so it restarts on crash and comes back after a reboot.
The footprint is tiny (pure-Python bot, ~10 MB index, no ML deps, no paid APIs),
so the Always Free tier is far more than enough.

Everything here is a one-time setup. After it, updates are `git pull` +
`./deploy/setup.sh` (or just `sudo systemctl restart lorehound`).

---

## 0. What you'll need

- An Oracle Cloud account (the Always Free tier — no charge, but sign-up asks for
  a card for identity verification).
- Your **`DISCORD_TOKEN`** (Discord Developer Portal → your app → Bot).
- If you want the rules features (`/lookup`, `/spell`, `/character`, …): your
  **`DRIVE_FOLDER_ID`** and the Google **`service_account.json`** key file.
- An SSH key pair (the console can generate one during VM creation).

---

## 1. Create the Always Free ARM VM

In the Oracle Cloud console:

1. **Menu → Compute → Instances → Create instance.**
2. **Image and shape → Edit:**
   - Image: **Canonical Ubuntu 22.04** (or 24.04).
   - Shape: **Ampere → VM.Standard.A1.Flex**. Set **1 OCPU / 6 GB RAM** (well
     within the Always Free allowance of 4 OCPU / 24 GB — you can bump it later).
3. **Add SSH keys:** paste your public key, or let it generate a pair and save
   the private key.
4. **Networking:** keep the default VCN/subnet with a **public IPv4**. You do
   **not** need to open any inbound ports beyond SSH — the bot only makes
   *outbound* connections to Discord and Google, which are allowed by default.
5. **Create.** When it's `Running`, copy the **Public IP address**.

> If the console says the ARM capacity is temporarily full in your region, retry
> in a bit or pick another availability domain — Always Free ARM is popular.

---

## 2. Connect and install prerequisites

```bash
ssh ubuntu@<PUBLIC_IP>          # -i path/to/your_private_key if not default

sudo apt-get update
sudo apt-get install -y git python3 python3-venv
```

Ubuntu 22.04 ships Python 3.10 and 24.04 ships 3.12 — both are fine (PyMuPDF has
ARM/aarch64 wheels for these, so nothing compiles from source).

---

## 3. Clone the repo

```bash
cd ~
git clone https://github.com/ProofByContradiction/lorehound.git
cd lorehound
```

---

## 4. Provide secrets and (optionally) the prebuilt index

### 4a. `.env` (required)

```bash
cp .env.example .env
nano .env
```

Fill in at minimum `DISCORD_TOKEN`. For rules features also set `DRIVE_FOLDER_ID`
and point `GOOGLE_CREDENTIALS_FILE` at the key file you'll copy next. Leave
`GOOGLE_CREDENTIALS_FILE=service_account.json` as-is if you scp it into the repo
root. If you're user-installing the bot (DMs/group DMs), set
`LOREHOUND_USER_INSTALL=1`.

### 4b. `service_account.json` (required only for rules features)

From your **local machine** (not the VM):

```bash
scp service_account.json ubuntu@<PUBLIC_IP>:~/lorehound/service_account.json
```

### 4c. The index cache — two options

The `cache/` directory (the extracted, searchable index) is gitignored, so a
fresh clone has none. Pick one:

- **Fast (recommended): copy your local cache up.** It's ~10 MB and lets the bot
  serve immediately with no re-extraction. As long as the VM is on the same
  commit as your Mac, the cached versions match and nothing re-extracts:
  ```bash
  # from your local machine:
  scp -r cache ubuntu@<PUBLIC_IP>:~/lorehound/cache
  ```
- **Hands-off: let it build on first boot.** With Drive configured, the bot
  downloads and extracts everything on startup (~30 min, CPU-heavy). It logs
  progress and serves once the first index is ready.

---

## 5. Install and start the service

```bash
./deploy/setup.sh
```

This creates the venv, installs `requirements.txt`, then installs, enables, and
starts the `lorehound` systemd service (it'll prompt for `sudo`). From now on the
bot runs on boot and restarts on crash.

**Verify it's healthy:**

```bash
sudo systemctl status lorehound      # should be "active (running)"
journalctl -u lorehound -f           # live logs — look for "Logged in as …"
```

Optional pre-flight before going live — logs in, syncs commands, then exits:

```bash
LOREHOUND_SMOKE_TEST=1 .venv/bin/python bot.py
```

---

## 6. Updating later

```bash
cd ~/lorehound
git pull
./deploy/setup.sh          # reinstalls deps if changed, restarts the service
```

- **Code/parser/render change:** the restart above is all you need.
- **`MD_VERSION`/`TABLE_VERSION` bump:** the bot re-extracts on next start
  (~30 min, bot briefly serving stale data), or scp a freshly-built `cache/` up
  to skip it — see [[deploy-version-bump-needs-bot-restart]] in project notes.

---

## 7. Troubleshooting

| Symptom | Fix |
| --- | --- |
| Service keeps restarting (`status` shows failures) | `journalctl -u lorehound -n 50` — almost always a missing/invalid `DISCORD_TOKEN` in `.env`, or a bad `service_account.json` path. |
| Crash-loop stops after ~5 tries | Expected: the unit's `StartLimitBurst` guard. Fix the config, then `sudo systemctl reset-failed lorehound && sudo systemctl start lorehound`. |
| Commands don't appear in Discord | Global sync can take ~1h. For instant iteration set `DISCORD_GUILD_ID` in `.env`. For user-install, confirm "User Install" is enabled in the Developer Portal. |
| Rules commands say "not configured" | `DRIVE_FOLDER_ID` + credentials aren't set, or the service account can't see the folder (share the Drive folder with the service-account email). |
| `python3-venv` error on `setup.sh` | `sudo apt-get install -y python3-venv`. |

---

## Alternative: Docker

Not required — systemd on the VM is the leanest path and what this guide targets.
If you'd rather run it as a container (portability across hosts), ask and we can
add a `Dockerfile` + `compose.yaml` with `restart: unless-stopped`; the bot reads
the same `.env`, so it drops in cleanly.
