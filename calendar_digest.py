"""Daily calendar digest across Google Calendar and Zoho Calendar."""

from __future__ import annotations

import base64
import email.mime.text
import html
import json
import logging
import os
import re
import time as time_module
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any, Sequence
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from dotenv import load_dotenv
from googleapiclient.discovery import Resource, build

from gmail_auth import load_credentials

ROOT = Path(__file__).resolve().parent

DATE_FORMAT = "%d-%m-%Y"
DEFAULT_TIMEZONE = "Asia/Kolkata"
DEFAULT_GOOGLE_CALENDAR_ID = "primary"
DEFAULT_DIGEST_TOKEN = ROOT / "token.calendar.json"
DEFAULT_ZOHO_TOKEN = ROOT / "zoho_token.json"

GOOGLE_DIGEST_SCOPES = [
    "https://www.googleapis.com/auth/calendar.readonly",
    "https://www.googleapis.com/auth/gmail.send",
]

ZOHO_SCOPES = "ZohoCalendar.event.READ,ZohoCalendar.calendar.READ"

CONFLICT_COLORS = [
    "#fff3cd",
    "#ffdce0",
    "#d8f3dc",
    "#dbeafe",
    "#f3e8ff",
    "#ffe4cc",
]


@dataclass(frozen=True)
class CalendarEvent:
    source: str
    calendar_email: str
    title: str
    start: datetime
    end: datetime
    location: str = ""
    organizer: str = ""
    link: str = ""
    is_all_day: bool = False
    description: str = ""
    attendees: tuple[str, ...] = ()
    meeting_url: str = ""
    status: str = ""


@dataclass(frozen=True)
class CalendarDigestResult:
    digest_date: date
    recipients: list[str]
    google_events: int
    secondary_events: int
    conflict_groups: int
    sent: bool
    message_id: str = ""
    html_body: str = ""


@dataclass(frozen=True)
class CalendarEventInsight:
    event_index: int
    priority: str
    context_note: str = ""
    preparation_note: str = ""


@dataclass(frozen=True)
class CalendarConflictInsight:
    group: int
    explanation: str
    recommendation: str = ""


@dataclass(frozen=True)
class CalendarAiInsights:
    daily_brief: str = ""
    today_focus: tuple[str, ...] = ()
    events: tuple[CalendarEventInsight, ...] = ()
    conflicts: tuple[CalendarConflictInsight, ...] = ()


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def _bool_env(name: str, default: bool = False) -> bool:
    raw = _env(name)
    if not raw:
        return default
    return raw.lower() in {"1", "true", "yes", "on"}


def _split_csv(raw: str) -> list[str]:
    return [part.strip() for part in raw.replace(";", ",").split(",") if part.strip()]


def load_project_env() -> None:
    """Load .env, falling back to .env.example for local experiments."""
    env_path = ROOT / ".env"
    if env_path.exists():
        load_dotenv(env_path)
    else:
        load_dotenv(ROOT / ".env.example", override=True)


def parse_digest_date(raw: str | None = None, *, tz_name: str | None = None) -> date:
    """Parse DD-MM-YYYY; use today's date in the configured timezone when omitted."""
    if not raw:
        tz = get_timezone(tz_name)
        return datetime.now(tz).date()
    try:
        return datetime.strptime(raw.strip(), DATE_FORMAT).date()
    except ValueError as exc:
        raise ValueError("Date must use DD-MM-YYYY format, for example 25-08-2026") from exc


def format_digest_date(day: date) -> str:
    return day.strftime(DATE_FORMAT)


def get_timezone(tz_name: str | None = None) -> ZoneInfo:
    name = (tz_name or _env("CALENDAR_DIGEST_TIMEZONE", DEFAULT_TIMEZONE)).strip()
    try:
        return ZoneInfo(name)
    except ZoneInfoNotFoundError as exc:
        raise ValueError(f"Unknown timezone {name!r}") from exc


def day_bounds(day: date, tz: ZoneInfo) -> tuple[datetime, datetime]:
    start = datetime.combine(day, time.min, tzinfo=tz)
    return start, start + timedelta(days=1)


def _parse_dt(value: str, tz: ZoneInfo) -> datetime:
    normalized = value.replace("Z", "+00:00")
    normalized = re.sub(r"(\.\d{6})\d+", r"\1", normalized)
    parsed = datetime.fromisoformat(normalized)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=tz)
    return parsed.astimezone(tz)


def _clean_event_detail(value: Any, *, limit: int = 2000) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    text = re.sub(r"(?i)<br\s*/?>", "\n", text)
    text = re.sub(r"(?is)<style.*?</style>|<script.*?</script>", " ", text)
    text = re.sub(r"<[^>]+>", " ", text)
    text = html.unescape(text)
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    text = re.sub(r"\n\s*\n+", "\n", text)
    text = "\n".join(line.strip() for line in text.splitlines() if line.strip())
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "..."


def _google_attendees(raw: dict[str, Any]) -> tuple[str, ...]:
    attendees: list[str] = []
    for item in raw.get("attendees", []):
        if not isinstance(item, dict):
            continue
        label = item.get("email") or item.get("displayName") or ""
        if label:
            attendees.append(str(label))
    return tuple(dict.fromkeys(attendees))


def _google_meeting_url(raw: dict[str, Any]) -> str:
    if raw.get("hangoutLink"):
        return str(raw["hangoutLink"])
    conference = raw.get("conferenceData") or {}
    for entry in conference.get("entryPoints", []):
        if not isinstance(entry, dict):
            continue
        uri = entry.get("uri")
        if uri:
            return str(uri)
    return ""


def _parse_google_event(raw: dict[str, Any], *, tz: ZoneInfo, source: str) -> CalendarEvent:
    start_raw = raw.get("start") or {}
    end_raw = raw.get("end") or {}
    is_all_day = "date" in start_raw and "dateTime" not in start_raw
    if is_all_day:
        start = datetime.combine(date.fromisoformat(start_raw["date"]), time.min, tzinfo=tz)
        end = datetime.combine(date.fromisoformat(end_raw["date"]), time.min, tzinfo=tz)
    else:
        start = _parse_dt(start_raw["dateTime"], tz)
        end = _parse_dt(end_raw["dateTime"], tz)

    organizer = raw.get("organizer") or {}
    return CalendarEvent(
        source=source,
        calendar_email=_env("GOOGLE_CALENDAR_EMAIL", "Gmail"),
        title=raw.get("summary") or "(No title)",
        start=start,
        end=end,
        location=raw.get("location") or "",
        organizer=organizer.get("email") or organizer.get("displayName") or "",
        link=raw.get("htmlLink") or "",
        is_all_day=is_all_day,
        description=_clean_event_detail(raw.get("description")),
        attendees=_google_attendees(raw),
        meeting_url=_google_meeting_url(raw),
        status=raw.get("status") or "",
    )


def _google_services() -> tuple[Resource, Resource]:
    credentials_path = _env(
        "GOOGLE_CALENDAR_CREDENTIALS_PATH",
        _env("GMAIL_CREDENTIALS_PATH", str(ROOT / "credentials.json")),
    )
    token_path = _env("GOOGLE_CALENDAR_TOKEN_PATH", str(DEFAULT_DIGEST_TOKEN))
    creds_env = (
        "GOOGLE_CALENDAR_CREDENTIALS_JSON"
        if _env("GOOGLE_CALENDAR_CREDENTIALS_JSON")
        else "GMAIL_CREDENTIALS_JSON"
    )
    token_env = (
        "GOOGLE_CALENDAR_TOKEN_JSON"
        if _env("GOOGLE_CALENDAR_TOKEN_JSON")
        else "CALENDAR_DIGEST_TOKEN_JSON"
    )
    creds = load_credentials(
        credentials_path,
        token_path,
        scopes=GOOGLE_DIGEST_SCOPES,
        credentials_env_name=creds_env,
        token_env_name=token_env,
    )
    calendar = build("calendar", "v3", credentials=creds, cache_discovery=False)
    gmail = build("gmail", "v1", credentials=creds, cache_discovery=False)
    return calendar, gmail


def list_google_events(service: Resource, day: date, tz: ZoneInfo) -> list[CalendarEvent]:
    start, end = day_bounds(day, tz)
    calendar_id = _env("GOOGLE_CALENDAR_ID", DEFAULT_GOOGLE_CALENDAR_ID)
    resp = (
        service.events()
        .list(
            calendarId=calendar_id,
            timeMin=start.isoformat(),
            timeMax=end.isoformat(),
            singleEvents=True,
            orderBy="startTime",
        )
        .execute()
    )
    return [
        _parse_google_event(item, tz=tz, source="Gmail")
        for item in resp.get("items", [])
        if item.get("status") != "cancelled"
    ]


def _zoho_accounts_url() -> str:
    return _env("ZOHO_ACCOUNTS_URL", "https://accounts.zoho.com").rstrip("/")


def _zoho_calendar_api_url() -> str:
    return _env("ZOHO_CALENDAR_API_URL", "https://calendar.zoho.com").rstrip("/")


def _zoho_calendar_api_candidates(api_domain: str = "") -> list[str]:
    candidates: list[str] = []
    api_domain_l = api_domain.lower()
    if api_domain_l.endswith(".in"):
        candidates.append("https://calendar.zoho.in")
    elif api_domain_l.endswith(".eu"):
        candidates.append("https://calendar.zoho.eu")
    elif api_domain_l.endswith(".com.au"):
        candidates.append("https://calendar.zoho.com.au")
    elif api_domain_l.endswith("zohocloud.ca"):
        candidates.append("https://calendar.zohocloud.ca")
    elif api_domain_l:
        candidates.append("https://calendar.zoho.com")

    candidates.extend(
        [
            _zoho_calendar_api_url(),
            "https://calendar.zoho.com",
            "https://calendar.zoho.in",
            "https://calendar.zoho.eu",
            "https://calendar.zoho.com.au",
        ]
    )
    return [url.rstrip("/") for url in dict.fromkeys(candidates) if url]


def _zoho_token_path() -> Path:
    return Path(_env("ZOHO_TOKEN_PATH", str(DEFAULT_ZOHO_TOKEN))).expanduser().resolve()


def _load_zoho_token() -> dict[str, Any]:
    raw = _env("ZOHO_TOKEN_JSON")
    if raw:
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            decoded = base64.b64decode(raw).decode("utf-8")
            return json.loads(decoded)

    token_path = _zoho_token_path()
    if token_path.is_file():
        return json.loads(token_path.read_text(encoding="utf-8"))
    return {}


def _save_zoho_token(payload: dict[str, Any]) -> None:
    if _env("ZOHO_TOKEN_JSON"):
        return
    token_path = _zoho_token_path()
    token_path.parent.mkdir(parents=True, exist_ok=True)
    token_path.write_text(json.dumps(payload), encoding="utf-8")
    try:
        os.chmod(token_path, 0o600)
    except OSError:
        pass


def _zoho_accounts_candidates(callback_accounts_url: str = "") -> list[str]:
    configured = _zoho_accounts_url()
    candidates = [
        callback_accounts_url.rstrip("/"),
        configured,
        "https://accounts.zoho.com",
        "https://accounts.zoho.in",
        "https://accounts.zoho.eu",
        "https://accounts.zoho.com.au",
        "https://accounts.zohocloud.ca",
    ]
    return [url for url in dict.fromkeys(candidates) if url]


def _zoho_token_request_once(
    body: dict[str, str],
    *,
    accounts_url: str,
    params_in_query: bool,
) -> dict[str, Any]:
    token_url = f"{accounts_url.rstrip('/')}/oauth/v2/token"
    encoded = urllib.parse.urlencode(body)
    url = f"{token_url}?{encoded}" if params_in_query else token_url
    data = b"" if params_in_query else encoded.encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"{accounts_url}: {detail}") from exc
    return payload


def _zoho_token_request(
    body: dict[str, str],
    *,
    callback_accounts_url: str = "",
) -> dict[str, Any]:
    errors: list[str] = []
    for accounts_url in _zoho_accounts_candidates(callback_accounts_url):
        for params_in_query in (False, True):
            try:
                payload = _zoho_token_request_once(
                    body,
                    accounts_url=accounts_url,
                    params_in_query=params_in_query,
                )
            except RuntimeError as exc:
                errors.append(str(exc))
                continue
            if not payload.get("error"):
                return payload
            errors.append(
                f"{accounts_url} ({'query' if params_in_query else 'form'}): {payload}"
            )

    raise RuntimeError(
        "Zoho token request failed for all account endpoints. "
        "Confirm the Zoho client is a Server-based app, the Client ID/Secret are "
        "from the same Zoho data center as the account, and the redirect URI is "
        f"exactly configured. Attempts: {' | '.join(errors)}"
    )


class _ZohoOAuthHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802
        parsed = urllib.parse.urlparse(self.path)
        query = urllib.parse.parse_qs(parsed.query)
        expected_path = getattr(self.server, "expected_path", "/callback")
        if parsed.path != expected_path:
            self.send_error(404)
            return

        code = query.get("code", [""])[0]
        error = query.get("error", [""])[0]
        accounts_server = query.get("accounts-server", [""])[0]
        setattr(self.server, "auth_code", code)
        setattr(self.server, "auth_error", error)
        setattr(self.server, "accounts_server", accounts_server)

        self.send_response(200)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.end_headers()
        if code:
            self.wfile.write(b"Zoho authorization complete. You can return to smartMailer.")
        else:
            self.wfile.write(f"Zoho authorization failed: {error}".encode("utf-8"))

    def log_message(self, _: str, *args: object) -> None:
        return


def zoho_authorize() -> str:
    """Run local OAuth callback flow and store Zoho refresh token JSON."""
    load_project_env()
    client_id = _env("ZOHO_CLIENT_ID")
    client_secret = _env("ZOHO_CLIENT_SECRET")
    redirect_uri = _env("ZOHO_REDIRECT_URI", "http://localhost:8765/callback")
    if not client_id or not client_secret:
        raise RuntimeError("Set ZOHO_CLIENT_ID and ZOHO_CLIENT_SECRET before zoho-auth.")

    parsed_redirect = urllib.parse.urlparse(redirect_uri)
    if parsed_redirect.scheme != "http" or parsed_redirect.hostname not in {"localhost", "127.0.0.1"}:
        raise RuntimeError("ZOHO_REDIRECT_URI must be a local HTTP URL, e.g. http://localhost:8765/callback.")
    port = parsed_redirect.port or 8765
    path = parsed_redirect.path or "/callback"

    server = HTTPServer((parsed_redirect.hostname, port), _ZohoOAuthHandler)
    server.timeout = 300
    setattr(server, "expected_path", path)
    setattr(server, "auth_code", "")
    setattr(server, "auth_error", "")

    params = {
        "scope": ZOHO_SCOPES,
        "client_id": client_id,
        "response_type": "code",
        "access_type": "offline",
        "prompt": "consent",
        "redirect_uri": redirect_uri,
    }
    auth_url = f"{_zoho_accounts_url()}/oauth/v2/auth?{urllib.parse.urlencode(params)}"
    print("Open this Zoho authorization URL:")
    print(auth_url)
    try:
        webbrowser.open(auth_url)
    except Exception:
        pass

    server.handle_request()
    code = getattr(server, "auth_code", "")
    error = getattr(server, "auth_error", "")
    accounts_server = getattr(server, "accounts_server", "")
    server.server_close()
    if not code:
        raise RuntimeError(f"Zoho authorization did not return a code: {error or 'timed out'}")

    token = _zoho_token_request(
        {
            "grant_type": "authorization_code",
            "client_id": client_id,
            "client_secret": client_secret,
            "redirect_uri": redirect_uri,
            "code": code,
        },
        callback_accounts_url=accounts_server,
    )
    if token.get("error") or not token.get("access_token"):
        raise RuntimeError(f"Zoho did not return an access_token: {token}")
    if not token.get("refresh_token"):
        existing = _load_zoho_token()
        if existing.get("error"):
            existing = {}
        existing_refresh_token = existing.get("refresh_token")
        if existing_refresh_token:
            token["refresh_token"] = existing_refresh_token
        else:
            print(
                "Warning: Zoho did not return a refresh_token. Saved the short-lived "
                "access_token so you can run the digest now, but scheduled runs need "
                "a refresh token. Revoke the app in Zoho Connected Apps and rerun "
                "`python main.py zoho-auth` if this token expires."
            )
    if token.get("expires_in"):
        token["expires_at"] = int(time_module.time()) + int(token["expires_in"]) - 60
    _save_zoho_token(token)
    return str(_zoho_token_path())


def _parse_zoho_datetime(value: str, event_tz_name: str, fallback_tz: ZoneInfo) -> tuple[datetime, bool]:
    raw = (value or "").strip()
    if not raw:
        raise ValueError("Missing Zoho event datetime")
    if re.fullmatch(r"\d{8}", raw):
        parsed_date = datetime.strptime(raw, "%Y%m%d").date()
        return datetime.combine(parsed_date, time.min, tzinfo=fallback_tz), True

    if raw.endswith("Z"):
        fmt = "%Y%m%dT%H%M%SZ" if len(raw) >= 16 else "%Y%m%dT%HZ"
        parsed = datetime.strptime(raw, fmt).replace(tzinfo=timezone.utc)
        return parsed.astimezone(fallback_tz), False

    if re.search(r"[+-]\d{4}$", raw):
        fmt = "%Y%m%dT%H%M%S%z" if len(raw) >= 20 else "%Y%m%dT%H%M%z"
        return datetime.strptime(raw, fmt).astimezone(fallback_tz), False

    fmt = "%Y%m%dT%H%M%S" if len(raw) >= 15 else "%Y%m%dT%H%M"
    parsed = datetime.strptime(raw, fmt)
    event_tz = fallback_tz
    if event_tz_name:
        try:
            event_tz = ZoneInfo(event_tz_name)
        except ZoneInfoNotFoundError:
            event_tz = fallback_tz
    return parsed.replace(tzinfo=event_tz).astimezone(fallback_tz), False


def _first_text(raw: dict[str, Any], keys: Sequence[str]) -> str:
    for key in keys:
        value = raw.get(key)
        if value:
            return str(value)
    return ""


def _zoho_attendees(raw: dict[str, Any]) -> tuple[str, ...]:
    values: list[str] = []
    candidates = (
        raw.get("attendees"),
        raw.get("participants"),
        raw.get("invitees"),
        raw.get("users"),
    )
    for candidate in candidates:
        if isinstance(candidate, str):
            cleaned = _clean_event_detail(candidate, limit=800)
            if cleaned:
                values.extend(part.strip() for part in re.split(r"[,;\n]+", cleaned) if part.strip())
        elif isinstance(candidate, list):
            for item in candidate:
                if isinstance(item, dict):
                    label = (
                        item.get("email")
                        or item.get("mail")
                        or item.get("name")
                        or item.get("displayName")
                        or item.get("display_name")
                    )
                    if label:
                        values.append(str(label))
                elif item:
                    values.append(str(item))
    return tuple(dict.fromkeys(values))


def parse_zoho_event(raw: dict[str, Any], tz: ZoneInfo) -> CalendarEvent:
    date_time = raw.get("dateandtime") or {}
    if isinstance(date_time, str):
        try:
            date_time = json.loads(date_time)
        except json.JSONDecodeError:
            date_time = {}
    event_tz_name = date_time.get("timezone") or ""
    start_raw = date_time.get("start") or raw.get("start") or ""
    end_raw = date_time.get("end") or raw.get("end") or ""
    start, is_all_day = _parse_zoho_datetime(start_raw, event_tz_name, tz)
    if end_raw:
        end, _ = _parse_zoho_datetime(end_raw, event_tz_name, tz)
    else:
        duration_ms = int(raw.get("duration") or 0)
        end = start + (timedelta(milliseconds=duration_ms) if duration_ms else timedelta(hours=1))
    if is_all_day and end <= start:
        end = start + timedelta(days=1)

    location = raw.get("location") or ""
    if isinstance(location, dict):
        location = location.get("display_name") or location.get("address") or ""

    return CalendarEvent(
        source=_env("SECONDARY_CALENDAR_LABEL", "Zoho"),
        calendar_email=_env("SECONDARY_CALENDAR_EMAIL") or _env("ZOHO_EMAIL", "Zoho"),
        title=raw.get("title") or "(No title)",
        start=start,
        end=end,
        location=str(location or ""),
        organizer=raw.get("organizer") or raw.get("createdby") or "",
        link=raw.get("url") or "",
        is_all_day=bool(raw.get("isallday")) or is_all_day,
        description=_clean_event_detail(
            _first_text(raw, ("description", "desc", "notes", "comment", "comments"))
        ),
        attendees=_zoho_attendees(raw),
        meeting_url=_first_text(raw, ("meeting_url", "meetingUrl", "conference_url", "url")),
        status=str(raw.get("status") or ""),
    )


class ZohoCalendarClient:
    def __init__(
        self,
        *,
        client_id: str,
        client_secret: str,
        refresh_token: str,
        calendar_uid: str,
        access_token: str = "",
        expires_at: int = 0,
        api_domain: str = "",
    ) -> None:
        self.client_id = client_id
        self.client_secret = client_secret
        self.refresh_token = refresh_token
        self._access_token = access_token
        self._expires_at = expires_at
        self._api_domain = api_domain
        self.calendar_uid = calendar_uid or "primary"

    @classmethod
    def from_env(cls) -> "ZohoCalendarClient":
        client_id = _env("ZOHO_CLIENT_ID")
        client_secret = _env("ZOHO_CLIENT_SECRET")
        token = _load_zoho_token()
        token_error = token.get("error")
        if token.get("error"):
            token = {}
        refresh_token = _env("ZOHO_REFRESH_TOKEN") or token.get("refresh_token") or ""
        access_token = str(token.get("access_token") or "")
        expires_at = int(token.get("expires_at") or 0)
        api_domain = str(token.get("api_domain") or "")
        if not client_id or not client_secret:
            raise RuntimeError("Set ZOHO_CLIENT_ID and ZOHO_CLIENT_SECRET.")
        if not (refresh_token or access_token):
            if token_error:
                raise RuntimeError(
                    "zoho_token.json exists but is invalid "
                    f"(Zoho error: {token_error}). Delete it and rerun "
                    "`python main.py zoho-auth` after checking ZOHO_CLIENT_ID, "
                    "ZOHO_CLIENT_SECRET, and ZOHO_REDIRECT_URI."
                )
            raise RuntimeError(
                "Run `python main.py zoho-auth` to create a valid zoho_token.json."
            )
        return cls(
            client_id=client_id,
            client_secret=client_secret,
            refresh_token=str(refresh_token),
            access_token=access_token,
            expires_at=expires_at,
            api_domain=api_domain,
            calendar_uid=_env("ZOHO_CALENDAR_UID", "primary"),
        )

    def _get_access_token(self) -> str:
        if self._access_token and int(time_module.time()) < self._expires_at:
            return self._access_token
        if not self.refresh_token:
            raise RuntimeError(
                "Zoho access_token expired and no refresh_token is available. "
                "Revoke the app in Zoho Connected Apps, then rerun "
                "`python main.py zoho-auth`."
            )
        payload = _zoho_token_request(
            {
                "refresh_token": self.refresh_token,
                "client_id": self.client_id,
                "client_secret": self.client_secret,
                "grant_type": "refresh_token",
            }
        )
        if not payload.get("access_token"):
            raise RuntimeError(f"Zoho refresh did not return access_token: {payload}")
        token = _load_zoho_token()
        token.update(payload)
        token["refresh_token"] = self.refresh_token
        if payload.get("expires_in"):
            token["expires_at"] = int(time_module.time()) + int(payload["expires_in"]) - 60
        _save_zoho_token(token)
        self._access_token = str(payload["access_token"])
        self._expires_at = int(token.get("expires_at") or 0)
        self._api_domain = str(token.get("api_domain") or self._api_domain)
        return self._access_token

    def _auth_prefixes(self) -> list[str]:
        configured = _env("ZOHO_AUTH_HEADER_PREFIX")
        prefixes = [configured] if configured else []
        prefixes.extend(["Bearer", "Zoho-oauthtoken"])
        return [prefix for prefix in dict.fromkeys(prefixes) if prefix]

    def _request_json(self, url: str, *, auth_prefix: str) -> dict[str, Any]:
        req = urllib.request.Request(
            url,
            headers={
                "Authorization": f"{auth_prefix} {self._get_access_token()}",
                "Accept": "application/json",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"{url} ({auth_prefix}): {detail}") from exc

    def list_events(self, day: date, tz: ZoneInfo) -> list[CalendarEvent]:
        start, end = day_bounds(day, tz)
        range_json = json.dumps(
            {
                "start": start.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ"),
                "end": end.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ"),
            },
            separators=(",", ":"),
        )
        query = urllib.parse.urlencode(
            {
                "range": range_json,
                "byinstance": "true",
                "timezone": tz.key,
            }
        )
        calendar_uid = urllib.parse.quote(self.calendar_uid, safe="")
        errors: list[str] = []
        payload: dict[str, Any] | None = None
        for base_url in _zoho_calendar_api_candidates(self._api_domain):
            url = f"{base_url}/api/v1/calendars/{calendar_uid}/events?{query}"
            for auth_prefix in self._auth_prefixes():
                try:
                    payload = self._request_json(url, auth_prefix=auth_prefix)
                    break
                except RuntimeError as exc:
                    errors.append(str(exc))
            if payload is not None:
                break
        if payload is None:
            raise RuntimeError(
                "Zoho Calendar request failed for all endpoints/auth styles. "
                f"Attempts: {' | '.join(errors)}"
            )
        events: list[CalendarEvent] = []
        for item in payload.get("events", []):
            if not isinstance(item, dict):
                logging.debug("Skipping non-object Zoho calendar item: %r", item)
                continue
            if item.get("message") and not any(
                item.get(key) for key in ("dateandtime", "start", "end", "title")
            ):
                logging.debug("Skipping Zoho calendar message: %s", item.get("message"))
                continue
            if str(item.get("status", "")).upper() == "CANCELLED":
                continue
            try:
                events.append(parse_zoho_event(item, tz))
            except (TypeError, ValueError) as exc:
                logging.debug(
                    "Skipping Zoho event without usable datetime: %s",
                    exc,
                )
        return [event for event in events if event.end > start and event.start < end]


class _UnionFind:
    def __init__(self, values: Sequence[int]) -> None:
        self.parent = {v: v for v in values}

    def find(self, value: int) -> int:
        parent = self.parent[value]
        if parent != value:
            self.parent[value] = self.find(parent)
        return self.parent[value]

    def union(self, left: int, right: int) -> None:
        lroot, rroot = self.find(left), self.find(right)
        if lroot != rroot:
            self.parent[rroot] = lroot


def conflict_groups(events: Sequence[CalendarEvent]) -> dict[int, int]:
    """Return event index to conflict group number for overlapping timed events."""
    timed = [
        (idx, event)
        for idx, event in enumerate(events)
        if not event.is_all_day and event.start < event.end
    ]
    uf = _UnionFind([idx for idx, _ in timed])
    for i, (left_idx, left) in enumerate(timed):
        for right_idx, right in timed[i + 1 :]:
            if max(left.start, right.start) < min(left.end, right.end):
                uf.union(left_idx, right_idx)

    components: dict[int, list[int]] = {}
    for idx, _ in timed:
        root = uf.find(idx)
        components.setdefault(root, []).append(idx)

    grouped: dict[int, int] = {}
    group_no = 1
    for indexes in components.values():
        if len(indexes) < 2:
            continue
        for idx in indexes:
            grouped[idx] = group_no
        group_no += 1
    return grouped


def _clock_label(value: datetime) -> str:
    return value.strftime("%I:%M %p")


def _time_range_label(start: datetime, end: datetime) -> str:
    return f"{_clock_label(start)} - {_clock_label(end)}"


def conflict_summaries(
    events: Sequence[CalendarEvent],
    groups: dict[int, int],
) -> dict[int, dict[str, Any]]:
    summaries: dict[int, dict[str, Any]] = {}
    for group in sorted(set(groups.values())):
        indexes = [idx for idx, value in groups.items() if value == group]
        grouped_events = [events[idx] for idx in indexes]
        starts = [event.start for event in grouped_events]
        ends = [event.end for event in grouped_events]
        common_start, common_end = max(starts), min(ends)
        summaries[group] = {
            "group": group,
            "event_indexes": indexes,
            "event_titles": [event.title for event in grouped_events],
            "affected_window": _time_range_label(min(starts), max(ends)),
            "common_overlap": (
                _time_range_label(common_start, common_end)
                if common_start < common_end
                else ""
            ),
        }
    return summaries


CALENDAR_AI_SYSTEM_PROMPT = """You are a careful calendar assistant.

Analyze a single-day meeting schedule and return ONLY valid JSON:
{
  "daily_brief": "3-5 sentence practical summary of the day using titles, descriptions, attendees, and conflict data",
  "today_focus": ["most useful action or risk to pay attention to"],
  "events": [
    {
      "event_index": 0,
      "priority": "High | Medium | Low",
      "context_note": "short useful context from the title/description/attendees, or empty string",
      "preparation_note": "specific preparation tip, or empty string"
    }
  ],
  "conflicts": [
    {
      "group": 1,
      "explanation": "why the overlap matters",
      "recommendation": "specific action to handle it"
    }
  ]
}

Rules:
- Base everything only on the event data provided.
- Do not invent attendees, documents, business facts, links, or commitments.
- Use event descriptions when available, but summarize them. Never paste long raw descriptions.
- today_focus should contain 2-4 short bullets about the most important risks, preparation, or flow of the day.
- Use High priority for external/client-facing, interviews, reviews, escalations, or ambiguous but important meetings.
- Use Medium for normal internal syncs and planned work.
- Use Low for optional, tentative, social, or broad informational events.
- context_note should explain why the meeting matters when the event has useful details. Leave it empty for obvious or low-information events.
- Preparation notes must be short and concrete. If no useful preparation is implied, return an empty string.
- Conflict recommendations should use the provided overlap windows and be practical, such as join the higher-priority meeting first, ask for notes, or request a reschedule.
- Keep all text concise and office-appropriate.
"""


def _calendar_ai_model_id() -> str:
    return _env("CALENDAR_DIGEST_AI_MODEL", _env("CURSOR_MODEL", "composer-2.5")) or "composer-2.5"


def _calendar_ai_enabled() -> bool:
    raw = _env("CALENDAR_DIGEST_AI").lower()
    if raw in {"0", "false", "no", "off"}:
        return False
    if raw in {"1", "true", "yes", "on"}:
        return True
    return bool(_env("CURSOR_API_KEY"))


def _format_event_for_ai(idx: int, event: CalendarEvent, group: int | None) -> dict[str, Any]:
    return {
        "event_index": idx,
        "calendar": event.source,
        "calendar_email": event.calendar_email,
        "title": event.title,
        "time": _time_label(event),
        "start": event.start.isoformat(),
        "end": event.end.isoformat(),
        "location": event.location,
        "organizer": event.organizer,
        "attendees": list(event.attendees[:12]),
        "attendee_count": len(event.attendees),
        "description": _clean_event_detail(event.description, limit=900),
        "meeting_url_available": bool(event.meeting_url),
        "calendar_link_available": bool(event.link),
        "status": event.status,
        "is_all_day": event.is_all_day,
        "conflict_group": group,
    }


def _parse_json_object(raw: str) -> dict[str, Any]:
    text = raw.strip()
    text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.I)
    text = re.sub(r"\s*```$", "", text)
    if not text.startswith("{"):
        start, end = text.find("{"), text.rfind("}")
        if start >= 0 and end > start:
            text = text[start : end + 1]
    data = json.loads(text)
    if not isinstance(data, dict):
        raise RuntimeError("Calendar AI did not return a JSON object")
    return data


def _clamp_text(value: Any, limit: int) -> str:
    text = str(value or "").strip()
    text = re.sub(r"\s+", " ", text)
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "..."


def _list_values(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    if value:
        return [value]
    return []


def _clamped_list(value: Any, limit: int, max_items: int) -> tuple[str, ...]:
    items: list[str] = []
    for item in _list_values(value):
        text = _clamp_text(item, limit)
        if text:
            items.append(text)
        if len(items) >= max_items:
            break
    return tuple(items)


def parse_calendar_ai_insights(
    payload: dict[str, Any],
    *,
    event_count: int,
    valid_conflict_groups: set[int],
) -> CalendarAiInsights:
    """Validate model JSON against the events that actually exist."""
    events: list[CalendarEventInsight] = []
    seen_events: set[int] = set()
    for item in payload.get("events", []):
        if not isinstance(item, dict):
            continue
        try:
            event_index = int(item.get("event_index"))
        except (TypeError, ValueError):
            continue
        if event_index < 0 or event_index >= event_count or event_index in seen_events:
            continue
        priority = _clamp_text(item.get("priority"), 24).title()
        if priority not in {"High", "Medium", "Low"}:
            priority = "Medium"
        events.append(
            CalendarEventInsight(
                event_index=event_index,
                priority=priority,
                context_note=_clamp_text(item.get("context_note"), 220),
                preparation_note=_clamp_text(item.get("preparation_note"), 180),
            )
        )
        seen_events.add(event_index)

    conflicts: list[CalendarConflictInsight] = []
    seen_conflicts: set[int] = set()
    for item in payload.get("conflicts", []):
        if not isinstance(item, dict):
            continue
        try:
            group = int(item.get("group"))
        except (TypeError, ValueError):
            continue
        if group not in valid_conflict_groups or group in seen_conflicts:
            continue
        explanation = _clamp_text(item.get("explanation"), 220)
        recommendation = _clamp_text(item.get("recommendation"), 220)
        if not explanation and not recommendation:
            continue
        conflicts.append(
            CalendarConflictInsight(
                group=group,
                explanation=explanation,
                recommendation=recommendation,
            )
        )
        seen_conflicts.add(group)

    return CalendarAiInsights(
        daily_brief=_clamp_text(payload.get("daily_brief"), 420),
        today_focus=_clamped_list(payload.get("today_focus"), 180, 4),
        events=tuple(events),
        conflicts=tuple(conflicts),
    )


def generate_calendar_ai_insights(
    events: Sequence[CalendarEvent],
    day: date,
    tz: ZoneInfo,
) -> CalendarAiInsights:
    """Ask Cursor for a daily brief, event priorities, prep notes, and conflict advice."""
    if not events:
        return CalendarAiInsights()
    api_key = _env("CURSOR_API_KEY")
    if not api_key:
        raise RuntimeError("Set CURSOR_API_KEY to enable calendar AI insights.")

    ordered = sorted(events, key=lambda e: (e.start, e.end, e.source, e.title.lower()))
    groups = conflict_groups(ordered)
    payload = {
        "date": format_digest_date(day),
        "timezone": tz.key,
        "conflicts": list(conflict_summaries(ordered, groups).values()),
        "events": [
            _format_event_for_ai(idx, event, groups.get(idx))
            for idx, event in enumerate(ordered)
        ],
    }
    prompt = (
        f"{CALENDAR_AI_SYSTEM_PROMPT}\n\n"
        "Analyze this schedule JSON:\n"
        f"{json.dumps(payload, ensure_ascii=False, indent=2)}"
    )

    try:
        from cursor_sdk import Agent, AgentOptions, CursorAgentError, LocalAgentOptions
    except ImportError as exc:
        raise RuntimeError("Install cursor-sdk to enable calendar AI insights.") from exc

    try:
        result = Agent.prompt(
            prompt,
            AgentOptions(
                api_key=api_key,
                model=_calendar_ai_model_id(),
                name="calendar-digest-ai",
                tools=[],
                local=LocalAgentOptions(cwd=str(ROOT)),
            ),
        )
    except CursorAgentError as exc:
        raise RuntimeError(
            f"Calendar AI failed to start: {exc.message} "
            f"(retryable={exc.is_retryable})"
        ) from exc

    status = getattr(result, "status", None)
    status_s = status if isinstance(status, str) else getattr(status, "value", str(status))
    if status_s == "error":
        raise RuntimeError(f"Calendar AI run failed (id={result.id})")
    text = (result.result or "").strip()
    if not text:
        raise RuntimeError(f"Calendar AI returned empty result (id={result.id})")

    return parse_calendar_ai_insights(
        _parse_json_object(text),
        event_count=len(ordered),
        valid_conflict_groups=set(groups.values()),
    )


def _time_label(event: CalendarEvent) -> str:
    if event.is_all_day:
        return "All day"
    return _time_range_label(event.start, event.end)


def _source_summary(events: Sequence[CalendarEvent]) -> str:
    counts: dict[str, int] = {}
    for event in events:
        counts[event.source] = counts.get(event.source, 0) + 1
    if not counts:
        return "No meetings scheduled"
    return " - ".join(
        f"{html.escape(source)}: {count}"
        for source, count in sorted(counts.items(), key=lambda item: item[0].lower())
    )


def _priority_badge(priority: str) -> str:
    colors = {
        "High": ("#fef3f2", "#b42318", "#fecdca"),
        "Medium": ("#fffaeb", "#b54708", "#fedf89"),
        "Low": ("#ecfdf3", "#027a48", "#abefc6"),
    }
    bg, fg, border = colors.get(priority, ("#f8fafc", "#344054", "#d0d5dd"))
    return (
        f'<span style="display: inline-block; margin-left: 8px; padding: 2px 7px; '
        f'border-radius: 999px; background: {bg}; color: {fg}; border: 1px solid {border}; '
        f'font-size: 11px; font-weight: 700; white-space: nowrap;">'
        f"{html.escape(priority)}</span>"
    )


def _event_detail_excerpt(event: CalendarEvent, limit: int = 180) -> str:
    return _clamp_text(_clean_event_detail(event.description, limit=limit * 2), limit)


def _default_priority(event: CalendarEvent, group: int | None) -> str:
    if group:
        return "High"
    if event.is_all_day:
        return "Low"
    if event.attendees or event.organizer or event.description:
        return "Medium"
    return "Low"


def _default_context_note(event: CalendarEvent) -> str:
    detail = _event_detail_excerpt(event)
    if detail:
        return detail
    if event.attendees:
        return f"Includes {len(event.attendees)} attendee{'s' if len(event.attendees) != 1 else ''}."
    if event.organizer:
        return f"Organized by {event.organizer}."
    return ""


def _default_prep_note(event: CalendarEvent, group: int | None) -> str:
    if group and event.description:
        return "Review the event details, then confirm which overlapping meeting should take priority."
    if group:
        return "Confirm which overlapping meeting should take priority before the slot starts."
    if event.description:
        return "Review the event details before joining."
    return ""


def _complete_event_insights(
    events: Sequence[CalendarEvent],
    groups: dict[int, int],
    ai_insights: CalendarAiInsights | None,
) -> dict[int, CalendarEventInsight]:
    completed = {
        insight.event_index: insight
        for insight in (ai_insights.events if ai_insights else ())
        if 0 <= insight.event_index < len(events)
    }
    for idx, event in enumerate(events):
        existing = completed.get(idx)
        group = groups.get(idx)
        if existing:
            completed[idx] = CalendarEventInsight(
                event_index=idx,
                priority=existing.priority or _default_priority(event, group),
                context_note=existing.context_note or _default_context_note(event),
                preparation_note=existing.preparation_note or _default_prep_note(event, group),
            )
            continue
        priority = _default_priority(event, group)
        context = _default_context_note(event)
        prep = _default_prep_note(event, group)
        if priority != "Low" or context or prep:
            completed[idx] = CalendarEventInsight(
                event_index=idx,
                priority=priority,
                context_note=context,
                preparation_note=prep,
            )
    return completed


def _default_today_focus(
    events: Sequence[CalendarEvent],
    summaries: dict[int, dict[str, Any]],
) -> tuple[str, ...]:
    focus: list[str] = []
    for group, summary in sorted(summaries.items()):
        overlap = summary.get("common_overlap") or summary.get("affected_window") or ""
        titles = ", ".join(str(title) for title in summary.get("event_titles", [])[:3])
        if overlap and titles:
            focus.append(f"Resolve Conflict {group} around {overlap}: {titles}.")
    detail_events = [event for event in events if event.description]
    if detail_events:
        focus.append(
            f"Review details for {len(detail_events)} meeting"
            f"{'s' if len(detail_events) != 1 else ''} with calendar notes."
        )
    return tuple(focus[:4])


def _ai_summary_html(
    ai_insights: CalendarAiInsights | None,
    fallback_focus: Sequence[str] = (),
) -> str:
    focus_items = ai_insights.today_focus if ai_insights else ()
    if not focus_items:
        focus_items = tuple(fallback_focus)
    if not ai_insights or not (ai_insights.daily_brief or focus_items):
        return ""
    brief = (
        f'<div style="font-size: 14px; line-height: 1.55; color: #1d2939; padding-top: 7px;">'
        f"{html.escape(ai_insights.daily_brief)}</div>"
        if ai_insights.daily_brief
        else ""
    )
    title = "AI daily brief" if ai_insights.daily_brief else "Schedule focus"
    focus = ""
    if focus_items:
        items = "".join(
            f'<li style="margin: 4px 0;">{html.escape(item)}</li>'
            for item in focus_items
        )
        focus = (
            f'<div style="font-size: 12px; color: #175cd3; font-weight: 700; '
            f'text-transform: uppercase; letter-spacing: 0.3px; padding-top: 12px;">'
            f"Today's focus</div>"
            f'<ul style="margin: 6px 0 0 18px; padding: 0; font-size: 13px; '
            f'line-height: 1.45; color: #344054;">{items}</ul>'
        )
    return f"""
        <tr>
          <td style="padding: 18px 24px 4px 24px;">
            <div style="border: 1px solid #bfdbfe; border-radius: 10px; background: #eff6ff; padding: 14px 16px;">
              <div style="font-size: 12px; color: #175cd3; font-weight: 700; text-transform: uppercase; letter-spacing: 0.3px;">{html.escape(title)}</div>
              {brief}
              {focus}
            </div>
          </td>
        </tr>"""


def _ai_conflicts_html(
    ai_insights: CalendarAiInsights | None,
    summaries: dict[int, dict[str, Any]],
) -> str:
    if not ai_insights or not ai_insights.conflicts:
        return ""
    items: list[str] = []
    for insight in sorted(ai_insights.conflicts, key=lambda item: item.group):
        summary = summaries.get(insight.group, {})
        common = summary.get("common_overlap") or ""
        affected = summary.get("affected_window") or ""
        titles = ", ".join(str(title) for title in summary.get("event_titles", [])[:4])
        timing = ""
        if common or affected:
            timing = (
                f'<div style="font-size: 12px; line-height: 1.45; color: #667085; padding-top: 4px;">'
                f'<strong>Overlap:</strong> {html.escape(common or affected)}'
                f"{' | ' if titles else ''}{html.escape(titles)}</div>"
            )
        recommendation = (
            f'<div style="font-size: 13px; line-height: 1.45; color: #475467; padding-top: 4px;">'
            f'<strong>Recommendation:</strong> {html.escape(insight.recommendation)}</div>'
            if insight.recommendation
            else ""
        )
        explanation = html.escape(insight.explanation or "Review this overlap before the meeting starts.")
        items.append(
            f'<div style="padding: 10px 0; border-top: 1px solid #fed7aa;">'
            f'<div style="font-size: 13px; color: #9a3412; font-weight: 700;">Conflict {insight.group}</div>'
            f"{timing}"
            f'<div style="font-size: 13px; line-height: 1.45; color: #344054; padding-top: 4px;">{explanation}</div>'
            f"{recommendation}"
            f"</div>"
        )
    return f"""
        <tr>
          <td style="padding: 6px 24px 6px 24px;">
            <div style="border: 1px solid #fed7aa; border-radius: 10px; background: #fff7ed; padding: 4px 14px 6px 14px;">
              {''.join(items)}
            </div>
          </td>
        </tr>"""


def _event_meta_html(event: CalendarEvent) -> str:
    parts: list[str] = []
    if event.organizer:
        parts.append(f"Organizer: {event.organizer}")
    if event.attendees:
        parts.append(f"Attendees: {len(event.attendees)}")
    if event.status:
        parts.append(f"Status: {event.status.title()}")
    if not parts and not event.meeting_url:
        return ""
    meta = html.escape(" | ".join(parts))
    join = (
        f' <a href="{html.escape(event.meeting_url, quote=True)}" '
        f'style="color: #175cd3; text-decoration: none; font-weight: 700;">Join</a>'
        if event.meeting_url
        else ""
    )
    return (
        f'<div style="font-size: 12px; line-height: 1.45; color: #667085; '
        f'padding-top: 6px;">{meta}{join if meta else join.strip()}</div>'
    )


def _event_rows(
    events: Sequence[CalendarEvent],
    groups: dict[int, int],
    ai_insights: CalendarAiInsights | None = None,
) -> str:
    if not events:
        return (
            '<tr><td colspan="5" style="padding: 22px; color: #667085; text-align: center; '
            'border-top: 1px solid #e4e7ec; background: #ffffff;">'
            '<div style="font-size: 15px; color: #344054; font-weight: 700;">'
            "No meetings found</div>"
            '<div style="font-size: 13px; color: #667085; padding-top: 4px;">'
            "Your calendar looks clear for this day.</div>"
            "</td></tr>"
        )
    rows: list[str] = []
    event_insights = _complete_event_insights(events, groups, ai_insights)
    for idx, event in enumerate(events):
        group = groups.get(idx)
        insight = event_insights.get(idx)
        color = CONFLICT_COLORS[(group - 1) % len(CONFLICT_COLORS)] if group else "#ffffff"
        border_color = "#dc6803" if group else "#e4e7ec"
        conflict = (
            f'<span style="display: inline-block; padding: 3px 8px; border-radius: 999px; '
            f'background: #fff7ed; color: #9a3412; border: 1px solid #fed7aa; '
            f'font-size: 12px; font-weight: 700;">Conflict {group}</span>'
            if group
            else '<span style="color: #98a2b3;">-</span>'
        )
        source_badge = (
            f'<span style="display: inline-block; padding: 4px 8px; border-radius: 999px; '
            f'background: #f8fafc; color: #344054; border: 1px solid #d0d5dd; '
            f'font-size: 12px; font-weight: 700;">{html.escape(event.source)}</span>'
        )
        title = html.escape(event.title)
        if event.link:
            title = (
                f'<a href="{html.escape(event.link, quote=True)}" '
                f'style="color: #175cd3; text-decoration: none; font-weight: 700;">{title}</a>'
            )
        if insight:
            title = f"{title}{_priority_badge(insight.priority)}"
            if insight.context_note:
                title = (
                    f"{title}"
                    f'<div style="font-size: 12px; line-height: 1.45; color: #344054; '
                    f'padding-top: 6px;"><strong>Context:</strong> '
                    f"{html.escape(insight.context_note)}</div>"
                )
            if insight.preparation_note:
                title = (
                    f"{title}"
                    f'<div style="font-size: 12px; line-height: 1.45; color: #475467; '
                    f'padding-top: 6px;"><strong>Prep:</strong> '
                    f"{html.escape(insight.preparation_note)}</div>"
                )
        title = f"{title}{_event_meta_html(event)}"
        location = html.escape(event.location or "-")
        rows.append(
            "<tr>"
            f'<td style="padding: 12px 10px; border-top: 1px solid #e4e7ec; '
            f'border-left: 4px solid {border_color}; background: {color};">'
            f"{source_badge}</td>"
            f'<td style="padding: 12px 10px; border-top: 1px solid #e4e7ec; '
            f'background: {color}; color: #344054; white-space: nowrap;">'
            f"{html.escape(_time_label(event))}</td>"
            f'<td style="padding: 12px 10px; border-top: 1px solid #e4e7ec; '
            f'background: {color}; color: #101828;">{title}</td>'
            f'<td style="padding: 12px 10px; border-top: 1px solid #e4e7ec; '
            f'background: {color}; color: #667085;">{location}</td>'
            f'<td style="padding: 12px 10px; border-top: 1px solid #e4e7ec; '
            f'background: {color}; white-space: nowrap;">{conflict}</td>'
            "</tr>"
        )
    return "".join(rows)


def build_digest_html(
    events: Sequence[CalendarEvent],
    day: date,
    tz: ZoneInfo,
    ai_insights: CalendarAiInsights | None = None,
) -> str:
    ordered = sorted(events, key=lambda e: (e.start, e.end, e.source, e.title.lower()))
    groups = conflict_groups(ordered)
    summaries = conflict_summaries(ordered, groups)
    fallback_focus = _default_today_focus(ordered, summaries)
    conflict_count = len(set(groups.values()))
    meeting_count = len(ordered)
    date_label = format_digest_date(day)
    secondary_label = _env("SECONDARY_CALENDAR_LABEL", "Zoho")
    conflict_label = "No conflicts" if conflict_count == 0 else f"{conflict_count} conflict group{'s' if conflict_count != 1 else ''}"
    return f"""<div style="margin: 0; padding: 0; background: #f6f8fb;">
<table role="presentation" cellpadding="0" cellspacing="0" width="100%" style="border-collapse: collapse; background: #f6f8fb;">
  <tr>
    <td align="center" style="padding: 24px 12px;">
      <table role="presentation" cellpadding="0" cellspacing="0" width="100%" style="border-collapse: collapse; width: 100%; max-width: 760px; background: #ffffff; border: 1px solid #d9e2ec; border-radius: 12px; overflow: hidden; font-family: Calibri, Arial, Helvetica, sans-serif; color: #1d2939;">
        <tr>
          <td style="padding: 22px 24px; background: #eff6ff; border-bottom: 1px solid #d9e2ec;">
            <div style="font-size: 12px; line-height: 1.3; color: #175cd3; font-weight: 700; text-transform: uppercase; letter-spacing: 0.4px;">Daily calendar digest</div>
            <div style="font-size: 24px; line-height: 1.25; color: #101828; font-weight: 700; padding-top: 5px;">{html.escape(date_label)}</div>
            <div style="font-size: 14px; line-height: 1.45; color: #475467; padding-top: 6px;">Gmail and {html.escape(secondary_label)} meetings in {html.escape(tz.key)}</div>
          </td>
        </tr>
        {_ai_summary_html(ai_insights, fallback_focus)}
        <tr>
          <td style="padding: 18px 24px 6px 24px;">
            <table role="presentation" cellpadding="0" cellspacing="0" width="100%" style="border-collapse: collapse;">
              <tr>
                <td width="33.33%" style="padding: 0 8px 12px 0;">
                  <div style="border: 1px solid #e4e7ec; border-radius: 10px; padding: 12px; background: #fcfcfd;">
                    <div style="font-size: 12px; color: #667085; font-weight: 700;">Meetings</div>
                    <div style="font-size: 22px; color: #101828; font-weight: 700; padding-top: 2px;">{meeting_count}</div>
                  </div>
                </td>
                <td width="33.33%" style="padding: 0 4px 12px 4px;">
                  <div style="border: 1px solid #e4e7ec; border-radius: 10px; padding: 12px; background: #fcfcfd;">
                    <div style="font-size: 12px; color: #667085; font-weight: 700;">Conflicts</div>
                    <div style="font-size: 14px; color: #101828; font-weight: 700; padding-top: 7px;">{html.escape(conflict_label)}</div>
                  </div>
                </td>
                <td width="33.33%" style="padding: 0 0 12px 8px;">
                  <div style="border: 1px solid #e4e7ec; border-radius: 10px; padding: 12px; background: #fcfcfd;">
                    <div style="font-size: 12px; color: #667085; font-weight: 700;">Calendars</div>
                    <div style="font-size: 14px; color: #101828; font-weight: 700; padding-top: 7px;">{_source_summary(ordered)}</div>
                  </div>
                </td>
              </tr>
            </table>
          </td>
        </tr>
        {_ai_conflicts_html(ai_insights, summaries)}
        <tr>
          <td style="padding: 6px 24px 22px 24px;">
            <table cellpadding="0" cellspacing="0" width="100%" style="border-collapse: separate; border-spacing: 0; width: 100%; border: 1px solid #d0d5dd; border-radius: 10px; overflow: hidden;">
  <thead>
    <tr style="background: #f8fafc;">
      <th align="left" style="padding: 11px 10px; border-bottom: 1px solid #d0d5dd; color: #475467; font-size: 12px; text-transform: uppercase; letter-spacing: 0.3px;">Calendar</th>
      <th align="left" style="padding: 11px 10px; border-bottom: 1px solid #d0d5dd; color: #475467; font-size: 12px; text-transform: uppercase; letter-spacing: 0.3px;">Time</th>
      <th align="left" style="padding: 11px 10px; border-bottom: 1px solid #d0d5dd; color: #475467; font-size: 12px; text-transform: uppercase; letter-spacing: 0.3px;">Meeting</th>
      <th align="left" style="padding: 11px 10px; border-bottom: 1px solid #d0d5dd; color: #475467; font-size: 12px; text-transform: uppercase; letter-spacing: 0.3px;">Location</th>
      <th align="left" style="padding: 11px 10px; border-bottom: 1px solid #d0d5dd; color: #475467; font-size: 12px; text-transform: uppercase; letter-spacing: 0.3px;">Status</th>
    </tr>
  </thead>
  <tbody>{_event_rows(ordered, groups, ai_insights)}</tbody>
</table>
            <div style="font-size: 13px; line-height: 1.5; color: #667085; padding-top: 16px;">
              Regards,<br>
              <strong style="color: #344054;">{html.escape(_env("AGENT_SIGN_OFF_NAME") or "smartMailer")}</strong>
            </div>
          </td>
        </tr>
      </table>
    </td>
  </tr>
</table>
</div>"""


def send_html_email(
    service: Resource,
    *,
    from_email: str = "",
    recipients: Sequence[str],
    subject: str,
    body_html: str,
) -> str:
    msg = email.mime.text.MIMEText(body_html, "html", "utf-8")
    msg["To"] = ", ".join(recipients)
    if from_email:
        msg["From"] = from_email
    msg["Subject"] = subject
    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode("utf-8")
    sent = (
        service.users()
        .messages()
        .send(userId="me", body={"raw": raw})
        .execute()
    )
    return sent.get("id", "")


def calendar_digest_google_account() -> str:
    """Run Google OAuth for calendar digest scopes."""
    load_project_env()
    _google_services()
    return _env("GOOGLE_CALENDAR_EMAIL") or "authorized Gmail account"


def calendar_digest_recipients(override: str | Sequence[str] | None = None) -> list[str]:
    if override:
        if isinstance(override, str):
            return _split_csv(override)
        return [str(item).strip() for item in override if str(item).strip()]
    recipients = _split_csv(_env("CALENDAR_DIGEST_RECIPIENTS"))
    if not recipients:
        recipients = [
            item
            for item in (_env("GOOGLE_CALENDAR_EMAIL"), _env("SECONDARY_CALENDAR_EMAIL"))
            if item
        ]
    if not recipients:
        raise RuntimeError(
            "Set CALENDAR_DIGEST_RECIPIENTS or both GOOGLE_CALENDAR_EMAIL "
            "and SECONDARY_CALENDAR_EMAIL."
        )
    return recipients


def send_calendar_digest(
    digest_date: str | date | None = None,
    *,
    recipients: str | Sequence[str] | None = None,
    dry_run: bool = False,
) -> CalendarDigestResult:
    """Fetch both calendars and send one schedule email from Gmail."""
    load_project_env()
    tz = get_timezone()
    day = (
        digest_date
        if isinstance(digest_date, date)
        else parse_digest_date(digest_date, tz_name=tz.key)
    )
    to_addrs = calendar_digest_recipients(recipients)

    google_calendar, gmail = _google_services()
    google_events = list_google_events(google_calendar, day, tz)
    secondary_events = ZohoCalendarClient.from_env().list_events(day, tz)
    all_events = [*google_events, *secondary_events]
    ordered_events = sorted(
        all_events,
        key=lambda e: (e.start, e.end, e.source, e.title.lower()),
    )
    groups = conflict_groups(ordered_events)
    ai_insights = CalendarAiInsights()
    if _calendar_ai_enabled():
        try:
            ai_insights = generate_calendar_ai_insights(all_events, day, tz)
        except Exception as exc:
            logging.warning("Calendar AI insights unavailable; sending standard digest: %s", exc)
    html_body = build_digest_html(all_events, day, tz, ai_insights=ai_insights)
    subject = f"Meeting schedule for {format_digest_date(day)}"

    if dry_run:
        logging.info("calendar-digest dry-run: not sending email")
        message_id = ""
        sent = False
    else:
        message_id = send_html_email(
            gmail,
            from_email=_env("GOOGLE_CALENDAR_EMAIL"),
            recipients=to_addrs,
            subject=subject,
            body_html=html_body,
        )
        sent = True

    return CalendarDigestResult(
        digest_date=day,
        recipients=to_addrs,
        google_events=len(google_events),
        secondary_events=len(secondary_events),
        conflict_groups=len(set(groups.values())),
        sent=sent,
        message_id=message_id,
        html_body=html_body if dry_run else "",
    )
