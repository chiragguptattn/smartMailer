# smartMailer

smartMailer is a Python CLI for two related email workflows:

1. Gmail draft agent: reads unread Gmail threads from configured sender domains,
   analyzes the full thread with Cursor, and creates polite Gmail draft replies.
   It does not auto-send these replies.
2. Calendar digest: reads meetings from primary Google Calendar and secondary
   Zoho Calendar, detects conflicts, and sends a subtle HTML schedule email from
   the primary Gmail account.

**GitHub:** [chiragguptattn/smartMailer](https://github.com/chiragguptattn/smartMailer)  
**Cloud setup:** [CLOUD.md](./CLOUD.md)

## Contents

| File | Purpose |
|------|---------|
| `main.py` | CLI commands: auth, whoami, list, run, watch, calendar-auth, zoho-auth, calendar-digest |
| `gmail_auth.py` | Google OAuth desktop flow and token refresh |
| `gmail_client.py` | Gmail API list, thread fetch, drafts, labels |
| `draft_replies.py` | Cursor prompt flow for reply drafts |
| `calendar_digest.py` | Google Calendar + Zoho Calendar digest, conflict detection, Gmail send |
| `.env.example` | Environment variable template |
| `requirements.txt` | Python dependencies |
| `install-launch-agent.sh` | Optional macOS local watcher |

## What Gets Authorized

The project uses OAuth tokens, not Gmail or Zoho passwords.

| File | Created by | Used for | Scopes |
|------|------------|----------|--------|
| `credentials.json` | Google Cloud OAuth client download | Google OAuth client configuration | Not a token |
| `token.json` | `python main.py auth` | Gmail draft agent | `gmail.readonly`, `gmail.compose`, `gmail.modify` |
| `token.calendar.json` | `python main.py calendar-auth` | Calendar digest Google side | `calendar.readonly`, `gmail.send` |
| `zoho_token.json` | `python main.py zoho-auth` | Calendar digest Zoho side | `ZohoCalendar.event.READ`, `ZohoCalendar.calendar.READ` |

The draft agent and calendar digest intentionally use separate Google tokens.
The draft agent does not have `gmail.send`. The calendar digest has `gmail.send`
only so it can send the daily schedule email.

## Security Rules

Never commit these files:

```bash
.env
credentials.json
token.json
token.calendar.json
zoho_token.json
```

They are already included in `.gitignore`. Keep real emails, client secrets, and
tokens in `.env`, not in `.env.example`. The app can fall back to `.env.example`
for local experiments, but `.env.example` is usually committed and should stay
template-like.

If a client secret or token is accidentally committed, rotate/revoke it in the
provider console and generate a fresh token.

## Install Locally

```bash
cd /path/to/smartMailer
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

Edit `.env` before running commands.

Minimum values for the Gmail draft agent:

```dotenv
GMAIL_FROM_DOMAIN=client.com
GMAIL_CREDENTIALS_PATH=./credentials.json
GMAIL_TOKEN_PATH=./token.json
CURSOR_API_KEY=your_cursor_api_key
CURSOR_MODEL=composer-2.5
AGENT_SIGN_OFF_NAME=Your Name
```

Minimum additional values for the calendar digest:

```dotenv
CALENDAR_DIGEST_TIMEZONE=Asia/Kolkata
CALENDAR_DIGEST_RECIPIENTS=your.gmail@gmail.com,your.zoho@zohomail.com

GOOGLE_CALENDAR_ID=primary
GOOGLE_CALENDAR_EMAIL=your.gmail@gmail.com
GOOGLE_CALENDAR_CREDENTIALS_PATH=./credentials.json
GOOGLE_CALENDAR_TOKEN_PATH=./token.calendar.json

SECONDARY_CALENDAR_LABEL=Zoho
SECONDARY_CALENDAR_EMAIL=your.zoho@zohomail.com
ZOHO_EMAIL=your.zoho@zohomail.com
ZOHO_CLIENT_ID=your_zoho_client_id
ZOHO_CLIENT_SECRET=your_zoho_client_secret
ZOHO_REDIRECT_URI=http://localhost:8765/callback
ZOHO_TOKEN_PATH=./zoho_token.json
ZOHO_CALENDAR_UID=primary
ZOHO_ACCOUNTS_URL=https://accounts.zoho.com
ZOHO_CALENDAR_API_URL=https://calendar.zoho.com
```

## Gmail And Google Calendar Setup

You need one Google Cloud OAuth desktop client. The same `credentials.json` can
be used to generate both `token.json` and `token.calendar.json`.

### 1. Create Or Select A Google Cloud Project

1. Open [Google Cloud Console](https://console.cloud.google.com/).
2. Create a new project or select an existing project.
3. Make sure the project belongs to the correct Google account or Workspace.

### 2. Enable Google APIs

In the selected project, enable:

1. **Gmail API**: required for draft creation and digest sending.
2. **Google Calendar API**: required for reading primary Google Calendar events.

You can find these under **APIs & Services -> Library**.

### 3. Configure OAuth Consent

Open **Google Auth Platform** or **APIs & Services -> OAuth consent screen**.

Use these settings for local/private use:

1. App name: `smartMailer` or any name you recognize.
2. User support email: your email.
3. Audience/User type:
   - Use **Internal** if this is a Google Workspace project and only users in
     your organization will use it.
   - Use **External** for a normal Gmail account or if Internal is unavailable.
4. If External/testing, add your Gmail address as a test user.
5. Save the configuration.

You do not need to manually add every scope in the console for local testing.
The app requests the scopes during `auth` and `calendar-auth`. Google will show
the requested permissions on the consent screen.

### 4. Create `credentials.json`

1. Open **Google Auth Platform -> Clients** or **APIs & Services -> Credentials**.
2. Click **Create client** or **Create credentials -> OAuth client ID**.
3. Application type: **Desktop app**.
4. Name: `smartMailer Local`.
5. Create the client.
6. Download the JSON file.
7. Rename it to `credentials.json`.
8. Put it in the repository root:

```bash
./credentials.json
```

Your `.env` should point to it:

```dotenv
GMAIL_CREDENTIALS_PATH=./credentials.json
GOOGLE_CALENDAR_CREDENTIALS_PATH=./credentials.json
```

### 5. Generate Gmail Draft Token: `token.json`

Run:

```bash
python main.py auth
```

What happens:

1. A browser opens.
2. Sign in with the Gmail account that should create draft replies.
3. Approve the Gmail permissions.
4. The app creates `token.json`.

Verify:

```bash
python main.py whoami
```

It should print the connected Gmail address.

### 6. Generate Calendar Digest Google Token: `token.calendar.json`

Set this in `.env` first:

```dotenv
GOOGLE_CALENDAR_EMAIL=your.primary.gmail@gmail.com
GOOGLE_CALENDAR_ID=primary
GOOGLE_CALENDAR_TOKEN_PATH=./token.calendar.json
```

Then run:

```bash
python main.py calendar-auth
```

Sign in with the primary Gmail account. This should usually be the same account
you want to send the digest email from.

The app creates:

```bash
./token.calendar.json
```

If you change Google scopes later, delete `token.calendar.json` and run
`python main.py calendar-auth` again.

## Zoho Calendar Setup

Zoho also uses OAuth. You must create a Zoho OAuth client and then run
`python main.py zoho-auth` to create `zoho_token.json`.

### 1. Confirm Your Zoho Data Center

Your Zoho account belongs to a data center. The domain is visible when you log in
to Zoho. Common examples:

| Region | Accounts URL | Calendar API URL |
|--------|--------------|------------------|
| US/global | `https://accounts.zoho.com` | `https://calendar.zoho.com` |
| India | `https://accounts.zoho.in` | `https://calendar.zoho.in` |
| Europe | `https://accounts.zoho.eu` | `https://calendar.zoho.eu` |
| Australia | `https://accounts.zoho.com.au` | `https://calendar.zoho.com.au` |
| Canada | `https://accounts.zohocloud.ca` | `https://calendar.zohocloud.ca` |

Set these in `.env` based on your account:

```dotenv
ZOHO_ACCOUNTS_URL=https://accounts.zoho.com
ZOHO_CALENDAR_API_URL=https://calendar.zoho.com
```

For example, if your Zoho account is India-based:

```dotenv
ZOHO_ACCOUNTS_URL=https://accounts.zoho.in
ZOHO_CALENDAR_API_URL=https://calendar.zoho.in
```

The code also reads Zoho's returned `api_domain` from `zoho_token.json` and uses
that to try the matching Calendar endpoint.

### 2. Create A Zoho OAuth App

1. Open the [Zoho API Console](https://api-console.zoho.com/).
2. Sign in with the Zoho account whose calendar you want to read.
3. Click **Add Client** or **Get Started**.
4. Choose **Server-based Applications**.
5. Fill the app details:
   - Client Name: `smartMailer`
   - Homepage URL: `http://localhost:8765`
   - Authorized Redirect URI: `http://localhost:8765/callback`
6. Create the client.
7. Open the **Client Secret** tab.
8. Copy:
   - Client ID
   - Client Secret

The redirect URI must match exactly:

```dotenv
ZOHO_REDIRECT_URI=http://localhost:8765/callback
```

If the API Console rejects `localhost`, use `http://127.0.0.1:8765/callback`
both in Zoho and in `.env`.

### 3. Configure Zoho Values In `.env`

```dotenv
SECONDARY_CALENDAR_LABEL=Zoho
SECONDARY_CALENDAR_EMAIL=your.zoho@zohomail.com
ZOHO_EMAIL=your.zoho@zohomail.com
ZOHO_CLIENT_ID=your_zoho_client_id
ZOHO_CLIENT_SECRET=your_zoho_client_secret
ZOHO_REDIRECT_URI=http://localhost:8765/callback
ZOHO_TOKEN_PATH=./zoho_token.json
ZOHO_CALENDAR_UID=primary
ZOHO_ACCOUNTS_URL=https://accounts.zoho.com
ZOHO_CALENDAR_API_URL=https://calendar.zoho.com
```

Keep `ZOHO_CALENDAR_UID=primary` unless you intentionally want to fetch a
specific Zoho calendar UID.

### 4. Generate Zoho Token: `zoho_token.json`

Run:

```bash
python main.py zoho-auth
```

What happens:

1. The CLI starts a temporary local callback server on port `8765`.
2. A Zoho authorization URL is printed and usually opened in your browser.
3. Sign in with the Zoho account.
4. Approve calendar read access.
5. Zoho redirects to `http://localhost:8765/callback`.
6. The app exchanges the authorization code for a token.
7. The app saves `zoho_token.json`.

If the browser does not open automatically, copy the printed URL into your
browser manually.

### 5. Zoho Refresh Token Notes

Scheduled digest runs need a refresh token. If `python main.py zoho-auth` says
Zoho did not return a refresh token:

1. Open Zoho account settings.
2. Go to connected apps or authorized apps.
3. Revoke the `smartMailer` app.
4. Run `python main.py zoho-auth` again.
5. Approve consent again.

Zoho commonly returns a refresh token only on a fresh consent flow with offline
access.

## Calendar Digest Configuration

Full calendar digest block:

```dotenv
CALENDAR_DIGEST_TIMEZONE=Asia/Kolkata
CALENDAR_DIGEST_RECIPIENTS=your.primary.gmail@gmail.com,your.zoho@zohomail.com

GOOGLE_CALENDAR_ID=primary
GOOGLE_CALENDAR_EMAIL=your.primary.gmail@gmail.com
GOOGLE_CALENDAR_CREDENTIALS_PATH=./credentials.json
GOOGLE_CALENDAR_TOKEN_PATH=./token.calendar.json
# GOOGLE_CALENDAR_CREDENTIALS_JSON=
# GOOGLE_CALENDAR_TOKEN_JSON=

SECONDARY_CALENDAR_LABEL=Zoho
SECONDARY_CALENDAR_EMAIL=your.zoho@zohomail.com
ZOHO_EMAIL=your.zoho@zohomail.com
ZOHO_CLIENT_ID=your_zoho_client_id
ZOHO_CLIENT_SECRET=your_zoho_client_secret
ZOHO_REDIRECT_URI=http://localhost:8765/callback
ZOHO_TOKEN_PATH=./zoho_token.json
ZOHO_CALENDAR_UID=primary
ZOHO_ACCOUNTS_URL=https://accounts.zoho.com
ZOHO_CALENDAR_API_URL=https://calendar.zoho.com
# ZOHO_TOKEN_JSON=
# ZOHO_REFRESH_TOKEN=
```

Important fields:

| Variable | Meaning |
|----------|---------|
| `CALENDAR_DIGEST_TIMEZONE` | Timezone used to decide the day boundary and display times |
| `CALENDAR_DIGEST_RECIPIENTS` | Comma-separated email recipients for the digest |
| `GOOGLE_CALENDAR_EMAIL` | Primary Gmail address shown in digest recipient defaults |
| `GOOGLE_CALENDAR_ID` | Google calendar to read, usually `primary` |
| `GOOGLE_CALENDAR_TOKEN_PATH` | Separate token for calendar read + Gmail send |
| `SECONDARY_CALENDAR_LABEL` | Display label for Zoho rows in the email |
| `SECONDARY_CALENDAR_EMAIL` | Secondary calendar email, also used as fallback recipient |
| `ZOHO_CALENDAR_UID` | Zoho calendar UID, usually `primary` |

## Run The Calendar Digest

Preview without sending:

```bash
python main.py calendar-digest --date 25-08-2026 --dry-run
```

Send for a specific date:

```bash
python main.py calendar-digest --date 25-08-2026
```

Send for today:

```bash
python main.py calendar-digest
```

The date format is always:

```text
DD-MM-YYYY
```

Example:

```text
25-08-2026
```

If `--date` is omitted, the command uses the current date in
`CALENDAR_DIGEST_TIMEZONE`.

Override recipients for one run:

```bash
python main.py calendar-digest --date 25-08-2026 --to you@gmail.com,other@example.com
```

The digest email includes:

- Google Calendar meetings.
- Zoho Calendar meetings.
- Total meeting count.
- Conflict group count.
- Subtle conflict highlighting in the schedule table.
- Links back to Google Calendar events when Google provides them.

## Run The Gmail Draft Agent

List unread matching threads:

```bash
python main.py list --domain partner.com
```

Preview draft output without writing Gmail drafts:

```bash
python main.py run --domain partner.com --dry-run -v
```

Create Gmail draft replies:

```bash
python main.py run --domain partner.com
```

Use `.env` default domain:

```dotenv
GMAIL_FROM_DOMAIN=partner.com
```

Then run:

```bash
python main.py run
```

Multiple domains are comma-separated:

```dotenv
GMAIL_FROM_DOMAIN=client.com,vendor.org
```

## Always-On Draft Agent

Foreground watcher:

```bash
python main.py watch
python main.py watch --interval 60
```

Optional macOS LaunchAgent:

```bash
./install-launch-agent.sh install
./install-launch-agent.sh uninstall
```

For cloud scheduling, see [CLOUD.md](./CLOUD.md).

## Troubleshooting

### `OAuth client file not found`

Make sure `credentials.json` exists in the repo root and `.env` points to it:

```dotenv
GMAIL_CREDENTIALS_PATH=./credentials.json
GOOGLE_CALENDAR_CREDENTIALS_PATH=./credentials.json
```

### Google Consent Shows Wrong Account

You may be signed in to multiple Google accounts. Use the account picker and
select the Gmail account that should own the token. If needed, delete the token
and run auth again:

```bash
rm token.json
python main.py auth
```

For calendar digest:

```bash
rm token.calendar.json
python main.py calendar-auth
```

### `invalid_client` From Zoho

Common causes:

- `ZOHO_CLIENT_ID` or `ZOHO_CLIENT_SECRET` was copied incorrectly.
- The Zoho app is not a Server-based Application.
- `ZOHO_ACCOUNTS_URL` does not match your Zoho data center.
- The redirect URI in Zoho does not exactly match `ZOHO_REDIRECT_URI`.

### Zoho Token Exists But Cannot Be Read

Confirm the file path:

```dotenv
ZOHO_TOKEN_PATH=./zoho_token.json
```

The path is resolved relative to this project when you run commands from the repo
root. You can also use an absolute path if needed.

### Zoho Returns No Meetings

If Zoho has no events for the selected date, the digest still sends with
`secondary_events=0`. Verify the date, timezone, and that the meetings are on
the Zoho account used during `python main.py zoho-auth`.

### Calendar Digest Sends From Gmail Only

This is expected. The digest reads Google Calendar and Zoho Calendar, but the
email is sent only from the primary Gmail account using `token.calendar.json`.

## References

- [Google Calendar Python quickstart](https://developers.google.com/workspace/calendar/api/quickstart/python)
- [Google OAuth consent configuration](https://developers.google.com/workspace/guides/configure-oauth-consent)
- [Zoho Calendar OAuth 2.0 user guide](https://www.zoho.com/calendar/help/api/oauth2-user-guide.html)
- [Zoho OAuth app registration](https://www.zoho.com/developer/oauth/register-app.html)
