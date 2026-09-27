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


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


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


def _time_label(event: CalendarEvent) -> str:
    if event.is_all_day:
        return "All day"
    return f"{event.start.strftime('%I:%M %p')} - {event.end.strftime('%I:%M %p')}"


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


def _event_rows(events: Sequence[CalendarEvent], groups: dict[int, int]) -> str:
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
    for idx, event in enumerate(events):
        group = groups.get(idx)
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


def build_digest_html(events: Sequence[CalendarEvent], day: date, tz: ZoneInfo) -> str:
    ordered = sorted(events, key=lambda e: (e.start, e.end, e.source, e.title.lower()))
    groups = conflict_groups(ordered)
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
  <tbody>{_event_rows(ordered, groups)}</tbody>
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
    html_body = build_digest_html(all_events, day, tz)
    ordered_events = sorted(
        all_events,
        key=lambda e: (e.start, e.end, e.source, e.title.lower()),
    )
    groups = conflict_groups(ordered_events)
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
