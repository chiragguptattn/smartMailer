"""Gmail OAuth2 helpers (desktop flow locally; env secrets in Cursor Cloud)."""

from __future__ import annotations

import json
import os
from pathlib import Path

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow

# Draft-only: read mail + create drafts. No send scope.
SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.compose",
    "https://www.googleapis.com/auth/gmail.modify",
]


def _materialize_from_env(env_name: str, dest: Path) -> bool:
    """If env_name holds JSON, write it to dest. Returns True when written."""
    raw = os.environ.get(env_name, "").strip()
    if not raw:
        return False
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        import base64

        try:
            decoded = base64.b64decode(raw).decode("utf-8")
            parsed = json.loads(decoded)
        except Exception as e:
            raise ValueError(
                f"{env_name} must be JSON (or base64-encoded JSON): {e}"
            ) from e
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps(parsed), encoding="utf-8")
    try:
        os.chmod(dest, 0o600)
    except OSError:
        pass
    return True


def load_credentials(
    credentials_path: str | Path,
    token_path: str | Path,
) -> Credentials:
    """Load or refresh user credentials.

    Cursor Cloud: set GMAIL_CREDENTIALS_JSON and GMAIL_TOKEN_JSON secrets
    (Dashboard → Cloud Agents → Secrets). Local: credentials.json + token.json.
    """
    credentials_path = Path(credentials_path).expanduser().resolve()
    token_path = Path(token_path).expanduser().resolve()

    from_env_creds = _materialize_from_env("GMAIL_CREDENTIALS_JSON", credentials_path)
    from_env_token = _materialize_from_env("GMAIL_TOKEN_JSON", token_path)
    cloudish = from_env_creds or from_env_token or bool(
        os.environ.get("CURSOR_AGENT") or os.environ.get("CURSOR_CLOUD")
    )

    if not credentials_path.is_file():
        raise FileNotFoundError(
            f"OAuth client file not found: {credentials_path}\n"
            "Local: download Desktop OAuth client JSON as credentials.json.\n"
            "Cloud: set secret GMAIL_CREDENTIALS_JSON to that JSON content."
        )

    creds: Credentials | None = None
    if token_path.is_file():
        creds = Credentials.from_authorized_user_file(str(token_path), SCOPES)

    if creds and creds.valid:
        return creds

    if creds and creds.expired and creds.refresh_token:
        creds.refresh(Request())
        token_path.write_text(creds.to_json(), encoding="utf-8")
        return creds

    if cloudish:
        raise RuntimeError(
            "Gmail token missing/invalid in Cloud. Run `python main.py auth` locally, "
            "then set secret GMAIL_TOKEN_JSON to the contents of token.json "
            "(and GMAIL_CREDENTIALS_JSON from credentials.json)."
        )

    flow = InstalledAppFlow.from_client_secrets_file(str(credentials_path), SCOPES)
    creds = flow.run_local_server(port=0)

    token_path.parent.mkdir(parents=True, exist_ok=True)
    token_path.write_text(creds.to_json(), encoding="utf-8")
    try:
        os.chmod(token_path, 0o600)
    except OSError:
        pass

    return creds
