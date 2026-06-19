# 🐕 Lorehound

A private Discord helper bot for tabletop RPGs — built first for **Twilight 2000
(4th edition)**, designed to grow into a general RPG assistant you can point at
any rules content.

**What it does today**

- 🎲 **Dice rolling** — generic dice notation (`2d6+1`, `d20`, `3d8-2`,
  `2d6 + 1d8`), quick single-type rolls (d4–d100), and Twilight 2000 mechanics
  (attribute + skill checks, ammo dice).
- 📚 **Rules lookup** — pulls your rulebooks (PDFs, text, and Google Docs) from a
  Google Drive folder and answers `/rule <topic>` with the most relevant
  passages, cited by document and page.

---

## Commands

| Command | What it does |
| --- | --- |
| `/roll dice:2d6+1` | Roll any dice expression. |
| `/d sides:6 count:3` | Quick-roll N dice of one type. |
| `/t2k attribute:B skill:C` | Twilight 2000 check (B=d10, C=d8). Skill is optional. |
| `/ammo count:5` | Roll 5 ammo dice (D6); 6s flagged as extra hits. |
| `/rule query:recoil` | Search the rulebooks for a topic. |
| `/rules_sync` | Re-pull and re-index the docs from Drive. |
| `/sources` | List the documents currently indexed. |

> **Twilight 2000 dice — please double-check me.** I encoded A=d12, B=d10,
> C=d8, D=d6; each die showing **6+** is a success; ammo-die **6s** are extra
> hits. Ammo/round *depletion is intentionally not tracked* — players track
> their own spent rounds; the bot only rolls and reads dice. See
> `lorehound/twilight.py`.

---

## Setup

### 0. Install dependencies

Python 3.11–3.13 is the safe target (3.14 is very new; some libs may lag). From
the project folder:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 1. Create the Discord bot

1. Go to <https://discord.com/developers/applications> → **New Application**.
2. Open the **Bot** tab → **Add Bot** → **Reset Token** and copy the token.
3. Under **Installation** (or **OAuth2 → URL Generator**), select the
   `applications.commands` and `bot` scopes, give it the **Send Messages** /
   **Use Slash Commands** permissions, and use the generated URL to invite it to
   your private server.
4. Copy `.env.example` to `.env` and paste the token into `DISCORD_TOKEN`.
5. (Recommended) Put your server's ID in `DISCORD_GUILD_ID` so slash commands
   appear instantly. Enable Developer Mode in Discord, right-click the server →
   **Copy Server ID**.

You can now run the bot with just dice support:

```bash
python bot.py
```

### 2. Connect Google Drive (rules lookup)

1. Go to <https://console.cloud.google.com/> → create (or pick) a project.
2. **APIs & Services → Library →** enable the **Google Drive API**.
3. **APIs & Services → Credentials → Create Credentials → Service account.**
   Name it (e.g. `lorehound-reader`), create it, then open it → **Keys → Add
   key → JSON**. A key file downloads.
4. Put that file in the project as `service_account.json` (it's gitignored), or
   paste its contents into `GOOGLE_CREDENTIALS_JSON` in `.env`.
5. Copy the **service account email** (looks like
   `lorehound-reader@your-project.iam.gserviceaccount.com`).
6. In Google Drive, create a folder for your rules, drop your PDFs / text /
   Google Docs in it, and **Share** the folder with that service-account email
   (Viewer is enough).
7. Copy the folder ID from its URL
   (`https://drive.google.com/drive/folders/<THIS>`) into `DRIVE_FOLDER_ID`.
8. Start the bot and run `/rules_sync`. Then try `/rule <topic>`.

---

## Security

Secrets never live in code — only in `.env` (gitignored) locally, or in your
host's env-var settings in production.

- **`.env` holds all tokens.** It is gitignored; only `.env.example` (with no
  real values) is committed.
- **Pre-commit guard.** A hook in `.githooks/` refuses to commit any secret file
  or embedded token/private key — defense-in-depth on top of `.gitignore`.
  Enable it per clone (already set here) with:
  `git config core.hooksPath .githooks`
- **Lock down permissions** on anything holding a real secret:
  `chmod 600 .env service_account.json`
- **Least privilege.** The Google service account uses read-only Drive scope and
  the Discord bot requests no privileged intents.
- **If a secret ever leaks, rotate it** — that's the only real fix. Reset the
  Discord token in the Developer Portal, and delete/recreate the service-account
  key in Google Cloud. (Deleting the file alone doesn't invalidate a leaked key.)

## Hosting (free tiers)

The bot is a long-running process (not a web service), so pick a host that
supports background workers:

- **Railway / Render** — set the start command to `python bot.py`, add the env
  vars from `.env`, and (on Render) choose a **Background Worker**.
- **Fly.io** — works well; the JSON-in-env-var credential option is handy here.
- For Drive creds on these hosts, use `GOOGLE_CREDENTIALS_JSON` (paste the whole
  key JSON as one env var) instead of committing a file.

Pin the host's Python to 3.12 or 3.13 for the smoothest dependency install.

---

## Project layout

```
bot.py                     # entry point: builds bot, loads cogs, runs
lorehound/
  config.py                # env-var configuration
  dice.py                  # generic dice engine (pure, unit-tested)
  twilight.py              # Twilight 2000 mechanics
  drive_client.py          # Google Drive: list / download / extract text
  search_index.py          # tiny built-in BM25 search (no numpy)
  rules.py                 # ties Drive + index together
  cogs/
    dice_cog.py            # /roll /d /t2k /ammo
    rules_cog.py           # /rule /rules_sync /sources
tests/
  test_dice.py             # run: python -m pytest
```

## Running the tests

```bash
python -m pytest          # or: python -m unittest
```

The dice/Twilight tests need no Discord token or network access.
