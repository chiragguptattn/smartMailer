# Cursor Cloud Automation — smartMailer

Repo: [chiragguptattn/smartMailer](https://github.com/chiragguptattn/smartMailer)

Run this on a **schedule** (recommended every 10–15 minutes). Cursor Cloud Agents
are not a forever process; a cron Automation achieves the same outcome without
keeping your laptop online.

## Create the Automation

1. Open [cursor.com/automations](https://cursor.com/automations) (or Agents Window → Automations).
2. **Trigger:** Schedule → custom cron, e.g. every 15 minutes: `*/15 * * * *`
3. **Repository:** `chiragguptattn/smartMailer`, branch `main`  
   (connect GitHub under Cursor Integrations if not already linked)
4. **Secrets** (Dashboard → [Cloud Agents Secrets](https://cursor.com/dashboard/cloud-agents)):

| Secret | Source |
|--------|--------|
| `CURSOR_API_KEY` | Integrations → API key |
| `GMAIL_CREDENTIALS_JSON` | contents of `credentials.json` (one-line JSON) |
| `GMAIL_TOKEN_JSON` | contents of `token.json` after local `python main.py auth` |
| `GMAIL_FROM_DOMAIN` | one domain or comma-separated, e.g. `client.com,vendor.org` |
| `AGENT_SIGN_OFF_NAME` | your name |
| `CURSOR_MODEL` | `composer-2.5` (optional) |
| `GMAIL_MAX_THREADS` | `10` (optional) |
| `GMAIL_PROCESSED_LABEL` | `AI/Drafted` (optional) |

Helper (run locally; copies values to terminal — do not paste into chat):

```bash
./print-cloud-secrets.sh
```

5. **Instructions** (paste into the automation prompt):

```text
You are a scheduled runner for smartMailer (Gmail draft agent). Do not open a pull request.
Do not edit source files unless a dependency install fails and you must fix requirements.

1. Working directory is the repo root.
2. Run exactly:
   chmod +x cloud-run.sh && ./cloud-run.sh
3. Summarize stdout: how many threads found / drafted / skipped / failed.
4. If the command fails, paste the error and stop.
5. Never print secret values or token JSON.
```

6. Save and enable the automation.

## Local LaunchAgent

If you previously installed the macOS LaunchAgent, uninstall it so only Cloud runs:

```bash
./install-launch-agent.sh uninstall
```
