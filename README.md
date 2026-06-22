# lorehound

> Tabletop rules and dice, one slash command away.

`lorehound` is a **self-hosted Discord bot** that rolls dice and turns your
**rulebook library** — PDFs, text, and Google Docs in a Google Drive folder —
into fast, in-chat lookups. Mid-session, pull a rule, a weapon stat block, or a
table without leaving Discord to dig through a PDF. It is **system-agnostic**:
point it at any game's books and search them with `source:<game>`. Twilight 2000
(4E) additionally gets first-class dice mechanics.

```
          >_ 🐕 lorehound
```

Self-hosted and single-purpose. **Free to run** — local BM25 search, no paid
APIs, no embeddings, no vector DB, no GPU. **Bring your own books.** Runs on a
free hosting tier or a spare machine in ~15 minutes.

## Commands

Slash commands (`/help` is authoritative):

```
/roll dice:2d6+1                    # roll any notation: d20, 3d8-2, 2d6 + 1d8   · public
/d sides:20 count:2                 # quick-roll N dice of one type, d4–d100     · public
/t2k attribute:B skill:C ammo:5     # Twilight 2000 check: attribute+skill+ammo  · public
/lookup source:<game> query:<topic> # search EVERYTHING, each result badged by type
/rule source:<game> query:<topic>   # how to play: stats, abilities, specialties
/item source:<game> query:<topic>   # gear, weapons, equipment
/transport source:<game> query:…    # vehicles, ships, craft, mounts & parts
/table source:<game> name:<table>   # find & print a rules table
/sources                            # list the games + books available
/reindex [force:true]               # (operator) re-pull and re-index from Drive
```

Lookups (`/rule`, `/item`, `/transport`, `/table`) take a `source:` game (with
autocomplete) and most take an optional `book:`. They reply **privately** with a
ranked list — pick one to read in full (cited by book + page), then optionally
**📢 Show in channel**. Only dice rolls and @mention replies are public.

**Twilight 2000 dice — double-check me.** Ratings A=d12 B=d10 C=d8 D=d6; each die
**6+** is a success (10+ counts as two); ammo-die **6s** are extra hits and **1s**
are the jam/push symbol. Round depletion is *not* tracked — players count their
own spent rounds. See `lorehound/twilight.py`.

## Why it's built this way

- **Free, local search.** Retrieval is a tiny built-in BM25 index
  (`search_index.py`) — no paid search/embedding APIs, no vector DB, no GPU.
  Nothing about a query leaves your host. The tradeoff (keyword vs. semantic) is
  the right one for rulebooks, where you usually know the term.
- **Your books are the source of truth, via Drive.** Nothing is bundled. The
  operator drops a book in the Drive folder and runs `/reindex` — no redeploy. The
  folder *is* the taxonomy: **one subfolder per game**, and that name becomes the
  `source:` value.
- **Config is environment variables, not a committed file.** Keeps secrets out of
  git, matches how cloud hosts inject config (paste env vars; no file to upload),
  and the *same mechanism* works in local dev and production.
- **Private by default.** Lookups are ephemeral; **Show in channel** shares one on
  purpose. Channels stay uncluttered.
- **Surface, don't adjudicate.** Every result is footnoted with book + page and
  reminds players to verify. Lorehound finds text; the table makes the ruling.
- **Least privilege.** Read-only Drive scope; no privileged Discord intents.

## Quickstart

Get a Discord bot token and (optionally) a Drive folder of books, then:

```bash
git clone <this-repo> lorehound && cd lorehound
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt          # Python 3.11+ (run on 3.14)
cp .env.example .env                      # then fill in DISCORD_TOKEN
./run.sh                                  # foreground; Ctrl-C to stop
```

**1 · Discord bot.** <https://discord.com/developers/applications> → New
Application → **Bot → Reset Token** (that's `DISCORD_TOKEN`). Invite it via
**Installation** / **OAuth2 → URL Generator** with scopes `bot` +
`applications.commands` and perms *Send Messages, Embed Links, Read Message
History*. For instant command updates while testing, set `DISCORD_GUILD_ID` to
your server ID (Developer Mode → right-click server → Copy Server ID).

**2 · Google Drive (rules lookup).** In Google Cloud: enable the **Drive API**,
create a **service account**, add a **JSON key**. Save it as `service_account.json`
(gitignored) or paste it into `GOOGLE_CREDENTIALS_JSON`. In Drive: make a top
folder, add **one subfolder per game**, drop books in, **Share** the top folder
with the service-account email (Viewer), and put its ID (`…/folders/<THIS>`) in
`DRIVE_FOLDER_ID`. Start the bot (it indexes automatically on launch), or run
`/reindex` to refresh on demand, then `/rule source:<game> query:<topic>`.

## Configuration

All settings are **environment variables**, loaded from a `.env` file locally
(via `python-dotenv`) or set in your host's dashboard in production. `.env` is
gitignored; only `.env.example` (placeholders) is committed.

| Variable | Type | Required | Example | What it's for |
|---|---|---|---|---|
| `DISCORD_TOKEN` | string (secret) | **yes** | `MTk4N…Xq3` | Bot token from the Developer Portal. The bot won't start without it. |
| `DISCORD_GUILD_ID` | int (snowflake) | no | `1173070855526420642` | Sync slash commands to one server **instantly** (dev). Blank = global (~1h to appear). |
| `LOREHOUND_USER_INSTALL` | bool (`1`/`true`/`yes`/`on`) | no | `1` | Register as a **user-installable** app so commands work in DMs/group DMs. Forces global sync; needs "User Install" enabled in the portal. |
| `DRIVE_FOLDER_ID` | string | no¹ | `1A2WAkinj7N4_wg7…` | Drive folder holding the library (`…/folders/<THIS>`). Enables the rules features. |
| `GOOGLE_CREDENTIALS_FILE` | string (path) | no¹ | `service_account.json` | Path to the service-account key **file**. Best for local dev. |
| `GOOGLE_CREDENTIALS_JSON` | string (JSON, secret) | no¹ | `{"type":"service_account",…}` | The key as **inline JSON** (one line). Best for cloud hosts. **Wins** over `…_FILE`. |
| `LOREHOUND_SMOKE_TEST` | bool (any non-empty) | no | `1` | Diagnostic: log in, sync, confirm, then **exit** (doesn't stay up). Verifies a token/deploy. |

¹ The rules feature turns on only when `DRIVE_FOLDER_ID` **and** one credential
variable are set. Provide credentials **one** way — `GOOGLE_CREDENTIALS_FILE`
**or** `GOOGLE_CREDENTIALS_JSON` (the latter wins if both are present). With none
set, dice still work and the rules commands explain that Drive isn't configured.

**Notes.** `DISCORD_TOKEN` is the only hard requirement — treat it like a
password and **rotate** (reset in the portal) if it leaks. Guild command updates
are instant; global ones lag ~1h — so dev points at a test guild, prod goes
global. The two credential options are the *same key, two delivery methods*: a
file for local disks, inline JSON for hosts that only expose env vars.

## Running & managing

```bash
./run.sh             # foreground via the project venv; Ctrl-C stops
```

A `lorehound` shell command (in `lorehound.zsh`, sourced from `~/.zshrc`) manages
it as a background process from anywhere:

```bash
lorehound start      # run detached, logging to bot.log  (default verb)
lorehound stop       # stop it
lorehound restart    # stop + start — use after code/config changes
lorehound status     # running/stopped + pid
lorehound logs       # live-tail bot.log
lorehound run        # foreground instead
```

**Config/code changes take effect on `restart`.**

## How it works

- **Extraction is cached; the index is rebuilt each start.** PDF→Markdown+tables
  extraction is expensive, so each doc is cached to `cache/<fileid>.json`, keyed
  by Drive file id + last-modified time + extractor version. A doc re-extracts
  only when it **changes on Drive** or the **extractor version** bumps. The BM25
  index is cheap and rebuilt in memory every start (not persisted). So a normal
  restart is seconds; a first run (or extractor-version change) re-extracts the
  whole library and takes a few minutes.
- **Output is Components V2 + ANSI.** Responses are bot-composed cards (containers
  / sections / separators) with ANSI-colored, aligned text — not embeds. See
  `lorehound/ui.py`.
- **Tables** are detected in an isolated subprocess (PyMuPDF `find_tables`,
  `pdf_tables.py`) and rendered for Discord (`tables.py`). PDF text uses PyMuPDF's
  ML-free path (font-histogram headings + multi-column order) — headings drive
  section-aware chunking, no heavy layout model.

## Security

Secrets live only in `.env` (gitignored) locally, or host env vars in prod.

- **`.env` holds all tokens.** Only `.env.example` (no real values) is committed.
- **Pre-commit guard.** A `.githooks/` hook refuses to commit secret files or
  embedded tokens/keys. Enable per clone: `git config core.hooksPath .githooks`.
- **`chmod 600 .env service_account.json`** on anything holding a secret.
- **Least privilege:** read-only Drive scope, no privileged Discord intents.
- **If a secret leaks, rotate it** — reset the Discord token; delete/recreate the
  service-account key. (Deleting the file alone doesn't invalidate a leaked key.)

## Hosting (free tiers)

A long-running process, not a web service — pick a host with background workers.

- **Railway / Render** — start command `python bot.py`, add the `.env` vars, and
  on Render choose a **Background Worker**.
- **Fly.io** — works well; the inline-JSON credential option is handy here.
- For Drive creds on hosts, prefer `GOOGLE_CREDENTIALS_JSON` over committing a file.

## Layout

```
lorehound/
├── bot.py                  # entry point: build bot, load cogs, run
├── run.sh                  # start the bot via the project venv
├── lorehound.zsh           # `lorehound` shell command (source from ~/.zshrc)
├── .env.example            # config template — copy to .env and fill in
├── lorehound/
│   ├── config.py           # env-var configuration (.env locally)
│   ├── dice.py             # generic dice engine (pure, unit-tested)
│   ├── twilight.py         # Twilight 2000 mechanics
│   ├── drive_client.py     # Drive: list / download / extract (PDF→Markdown + tables)
│   ├── pdf_tables.py       # isolated-subprocess table detection (PyMuPDF find_tables)
│   ├── tables.py           # render recovered tables for Discord
│   ├── search_index.py     # tiny built-in BM25 search (no numpy / vector DB)
│   ├── ui.py               # Components V2 + ANSI output toolkit
│   ├── rules.py            # ties Drive + extraction + index together
│   └── cogs/
│       ├── dice_cog.py     # /roll /d /t2k
│       ├── rules_cog.py    # /lookup /rule /item /transport /table /sources /reindex
│       └── meta_cog.py     # /help + @mention intro
├── scripts/
│   └── retrieval_eval.py   # gold-query retrieval regression eval (local; needs the library)
└── tests/
    ├── test_dice.py        # dice + Twilight mechanics
    ├── test_rules.py       # chunking, tables, heading boost
    └── test_retrieval.py   # BM25 scoping/ranking invariants + gold regression
```

## Tests

```bash
python -m unittest        # or: python -m pytest (if installed)
```

No Discord token or network access required — the suite is pure logic. It runs
in CI on every push/PR (`.github/workflows/tests.yml`), alongside the daily
dependency security audit.

### Retrieval quality eval

`eval/gold_queries.json` is an answer key (query → `key_facts`) for measuring
whether the bot returns *correct* rules, not just plausible-looking passages —
the north-star metric the heading/chunking work serves. Run it against your
indexed library:

```bash
python scripts/retrieval_eval.py            # human-readable report
python scripts/retrieval_eval.py --json     # machine-readable summary
```

It builds the live index from the Drive cache (no re-download for unchanged
files) and scores each query's retrieved passages, so it needs Google Drive
configured + a populated `cache/` — the copyrighted books can't live in the
repo, so this is a **local** guard, not a CI check. The same eval is wired into
the suite as an opt-in regression test:

```bash
LOREHOUND_GOLD_EVAL=1 python -m unittest tests.test_retrieval
```
