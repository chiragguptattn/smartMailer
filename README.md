# smartMailer

Gmail draft agent for office mail: reads **unread** threads from a sender domain,
analyzes the full chain with Cursor, and creates **polite draft replies**. Never auto-sends.

**GitHub:** [chiragguptattn/smartMailer](https://github.com/chiragguptattn/smartMailer)  
**Cloud setup:** [CLOUD.md](./CLOUD.md)

## Contents

| File | Purpose |
|------|---------|
| `main.py` | CLI: `auth`, `whoami`, `list`, `run`, `watch` |
| `install-launch-agent.sh` | macOS always-on service (LaunchAgent) |
| `gmail_auth.py` | OAuth desktop flow + token refresh |
| `gmail_client.py` | Gmail API: list / fetch threads, drafts, labels |
| `draft_replies.py` | Cursor LLM: curate brief → HTML draft |
| `.env.example` | Config template |
| `requirements.txt` | Python deps |

## Google Cloud setup (one-time)

1. Open [Google Cloud Console](https://console.cloud.google.com/) → create or pick a project.
2. Enable **Gmail API**.
3. **APIs & Services → OAuth consent screen**
   - User type: **Internal** (Workspace) if available; otherwise External + add your account as test user.
   - Scopes used by this script: `gmail.readonly`, `gmail.compose`, `gmail.modify`.
4. **Credentials → Create credentials → OAuth client ID**
   - Application type: **Desktop app**
   - Download JSON → save as `scripts/gmail-draft-agent/credentials.json`
5. Workspace admins may need to approve the app for company accounts.

**Do not commit** `credentials.json`, `token.json`, or `.env`.

## Install

```bash
cd scripts/gmail-draft-agent
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
# Edit .env: set GMAIL_FROM_DOMAIN, AGENT_SIGN_OFF_NAME, and CURSOR_API_KEY (required)
```

## Connect Gmail

```bash
python main.py auth
```

A browser window opens; sign in with the office account and allow access. Token is stored at `token.json` (gitignored).

```bash
python main.py whoami   # should print your work email
```

## Usage

```bash
# List unread threads from a domain (skips already labeled AI/Drafted)
python main.py list --domain partner.com

# One-shot preview
python main.py run --domain partner.com --dry-run -v

# One-shot create drafts
python main.py run --domain partner.com
```

Or set `GMAIL_FROM_DOMAIN` in `.env` and omit `--domain`. Multiple domains:
comma-separated in `.env` or `--domain a.com,b.com`.

## Always-on

**Preferred: Cursor Cloud Automation** (laptop can be offline) — see [CLOUD.md](./CLOUD.md).

**Legacy local LaunchAgent** (requires this Mac online):

```bash
./install-launch-agent.sh install    # start at login + KeepAlive
./install-launch-agent.sh uninstall  # stop local watcher
```

Foreground poller:

```bash
python main.py watch                 # Ctrl+C to stop
python main.py watch --interval 60
```

## Behaviour

- Filter: `is:unread from:*@<domain>` (OR across multiple domains), excluding chats/promotions/social and threads already labeled `AI/Drafted`
- Loads the **full thread**, not just the latest message
- Creates a **draft** in that thread (reply headers when Message-ID is present)
- Labels the thread and **marks it read** so the next unread-only run skips it
- Drafts are **HTML** (`DRAFT_FONT_FAMILY` / `DRAFT_FONT_SIZE`); body never dumps the thread
- **`CURSOR_API_KEY` required** — curate → draft via Cursor (`CURSOR_MODEL`, default `composer-2.5`)
- FYI / thanks / company notices get a short **acknowledgment** draft (not skipped)
- Skip only clear no-value cases (newsletters, bounces, no-reply automation)
- Use `--include-read` only for debugging already-read mail

## Security notes

- Scopes intentionally exclude send — no `gmail.send`
- Keep OAuth client secret and refresh token off shared drives and out of git
- For production scheduling, run under your user with the stored token; rotate credentials if leaked

## Suggested next steps

- Put `./install-launch-agent.sh install` on each machine that should draft continuously
- Tune `GMAIL_POLL_SECONDS` (lower = faster; keep ≥ 30)
- Add a Slack ping when a draft is created
- Tighten the system prompt for your role (support / sales / engineering)
