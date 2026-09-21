"""Gmail API helpers: list threads by domain, load full chain, create draft replies."""

from __future__ import annotations

import base64
import email.mime.text
import re
from dataclasses import dataclass, field
from typing import Any, Sequence

from googleapiclient.discovery import Resource, build


@dataclass
class ThreadMessage:
    id: str
    thread_id: str
    from_header: str
    to_header: str
    subject: str
    date: str
    message_id_header: str
    references_header: str
    body_text: str
    snippet: str


@dataclass
class MailThread:
    id: str
    subject: str
    messages: list[ThreadMessage] = field(default_factory=list)

    @property
    def latest(self) -> ThreadMessage | None:
        return self.messages[-1] if self.messages else None


def build_service(creds: Any) -> Resource:
    return build("gmail", "v1", credentials=creds, cache_discovery=False)


def _header(headers: list[dict[str, str]], name: str) -> str:
    name_l = name.lower()
    for h in headers:
        if h.get("name", "").lower() == name_l:
            return h.get("value", "")
    return ""


def _decode_body_data(data: str | None) -> str:
    if not data:
        return ""
    padded = data + "=" * (-len(data) % 4)
    return base64.urlsafe_b64decode(padded.encode("utf-8")).decode("utf-8", errors="replace")


def _walk_parts(payload: dict[str, Any]) -> tuple[str, str]:
    """Return (text/plain, text/html) from a message payload."""
    mime = payload.get("mimeType", "")
    body = payload.get("body", {}) or {}
    data = body.get("data")
    parts = payload.get("parts") or []

    if mime == "text/plain" and data:
        return _decode_body_data(data), ""
    if mime == "text/html" and data:
        return "", _decode_body_data(data)

    plain, html = "", ""
    for part in parts:
        p, h = _walk_parts(part)
        plain = plain or p
        html = html or h
    if data and not plain and not html and mime.startswith("text/"):
        decoded = _decode_body_data(data)
        if "html" in mime:
            html = decoded
        else:
            plain = decoded
    return plain, html


def _html_to_rough_text(html: str) -> str:
    # Minimal fallback when only HTML is present
    import re

    text = re.sub(r"(?is)<(script|style).*?>.*?</\1>", "", html)
    text = re.sub(r"(?i)<br\s*/?>", "\n", text)
    text = re.sub(r"(?i)</p>", "\n\n", text)
    text = re.sub(r"(?s)<[^>]+>", "", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def parse_message(raw: dict[str, Any]) -> ThreadMessage:
    payload = raw.get("payload") or {}
    headers = payload.get("headers") or []
    plain, html = _walk_parts(payload)
    body = plain.strip() or _html_to_rough_text(html)
    return ThreadMessage(
        id=raw["id"],
        thread_id=raw.get("threadId", ""),
        from_header=_header(headers, "From"),
        to_header=_header(headers, "To"),
        subject=_header(headers, "Subject"),
        date=_header(headers, "Date"),
        message_id_header=_header(headers, "Message-ID") or _header(headers, "Message-Id"),
        references_header=_header(headers, "References"),
        body_text=body,
        snippet=raw.get("snippet", ""),
    )


def normalize_sender_domains(from_domain: str | Sequence[str]) -> list[str]:
    """Parse one or more sender domains (comma/semicolon/whitespace separated)."""
    if isinstance(from_domain, str):
        raw_parts = re.split(r"[,;\s]+", from_domain.strip())
    else:
        raw_parts = []
        for item in from_domain:
            raw_parts.extend(re.split(r"[,;\s]+", str(item).strip()))
    domains: list[str] = []
    seen: set[str] = set()
    for part in raw_parts:
        domain = part.strip().lstrip("@").lower()
        if not domain or domain in seen:
            continue
        seen.add(domain)
        domains.append(domain)
    return domains


def gmail_from_domain_clause(domains: Sequence[str]) -> str:
    """Gmail search fragment: messages from *@domain (OR when multiple)."""
    if not domains:
        raise ValueError("At least one sender domain is required")
    if len(domains) == 1:
        return f"from:*@{domains[0]}"
    inner = " OR ".join(f"from:*@{d}" for d in domains)
    return f"({inner})"


class GmailClient:
    def __init__(self, service: Resource):
        self.service = service
        self._label_cache: dict[str, str] = {}

    def list_thread_ids(
        self,
        from_domain: str | Sequence[str],
        *,
        max_results: int = 10,
        exclude_label: str | None = None,
        unread_only: bool = True,
    ) -> list[str]:
        """Find threads with at least one message from *@domain (unread by default)."""
        domains = normalize_sender_domains(from_domain)
        if not domains:
            return []
        query_parts = [
            gmail_from_domain_clause(domains),
            "-in:chats",
            "-category:promotions",
            "-category:social",
        ]
        if unread_only:
            query_parts.append("is:unread")
        if exclude_label:
            query_parts.append(f"-label:{exclude_label}")
        q = " ".join(query_parts)

        ids: list[str] = []
        page_token: str | None = None
        while len(ids) < max_results:
            resp = (
                self.service.users()
                .threads()
                .list(
                    userId="me",
                    q=q,
                    maxResults=min(50, max_results - len(ids)),
                    pageToken=page_token,
                )
                .execute()
            )
            for t in resp.get("threads") or []:
                ids.append(t["id"])
                if len(ids) >= max_results:
                    break
            page_token = resp.get("nextPageToken")
            if not page_token:
                break
        return ids

    def mark_thread_read(self, thread_id: str) -> None:
        """Clear UNREAD so the thread is not picked up again by unread-only runs."""
        self.service.users().threads().modify(
            userId="me",
            id=thread_id,
            body={"removeLabelIds": ["UNREAD"]},
        ).execute()

    def get_thread(self, thread_id: str) -> MailThread:
        raw = (
            self.service.users()
            .threads()
            .get(userId="me", id=thread_id, format="full")
            .execute()
        )
        messages = [parse_message(m) for m in (raw.get("messages") or [])]
        subject = messages[0].subject if messages else ""
        return MailThread(id=thread_id, subject=subject, messages=messages)

    def ensure_label(self, name: str) -> str:
        """Return label id, creating the label if needed."""
        if name in self._label_cache:
            return self._label_cache[name]

        existing = self.service.users().labels().list(userId="me").execute()
        for lab in existing.get("labels") or []:
            if lab.get("name") == name:
                self._label_cache[name] = lab["id"]
                return lab["id"]

        created = (
            self.service.users()
            .labels()
            .create(
                userId="me",
                body={
                    "name": name,
                    "labelListVisibility": "labelShow",
                    "messageListVisibility": "show",
                },
            )
            .execute()
        )
        self._label_cache[name] = created["id"]
        return created["id"]

    def apply_label_to_thread(self, thread_id: str, label_name: str) -> None:
        label_id = self.ensure_label(label_name)
        self.service.users().threads().modify(
            userId="me",
            id=thread_id,
            body={"addLabelIds": [label_id]},
        ).execute()

    def create_reply_draft(
        self,
        thread: MailThread,
        body_html: str,
        *,
        from_email: str | None = None,
    ) -> str:
        """Create an unsent HTML draft reply in the same thread. Returns draft id."""
        latest = thread.latest
        if not latest:
            raise ValueError(f"Thread {thread.id} has no messages")

        # Reply to the latest message's sender
        to_addr = latest.from_header
        subject = latest.subject or thread.subject or ""
        if subject and not subject.lower().startswith("re:"):
            subject = f"Re: {subject}"

        # HTML so Gmail renders font-family / size from the draft body
        msg = email.mime.text.MIMEText(body_html, "html", "utf-8")
        msg["To"] = to_addr
        msg["Subject"] = subject
        if from_email:
            msg["From"] = from_email
        if latest.message_id_header:
            msg["In-Reply-To"] = latest.message_id_header
            refs = " ".join(
                p for p in (latest.references_header, latest.message_id_header) if p
            ).strip()
            if refs:
                msg["References"] = refs

        raw = base64.urlsafe_b64encode(msg.as_bytes()).decode("utf-8")
        draft = (
            self.service.users()
            .drafts()
            .create(
                userId="me",
                body={"message": {"raw": raw, "threadId": thread.id}},
            )
            .execute()
        )
        return draft["id"]

    def profile_email(self) -> str:
        profile = self.service.users().getProfile(userId="me").execute()
        return profile.get("emailAddress", "")
