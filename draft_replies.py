"""LLM-curated email replies via Cursor API key (curate → draft)."""

from __future__ import annotations

import html
import json
import os
import re
from pathlib import Path

from cursor_sdk import Agent, AgentOptions, CursorAgentError, LocalAgentOptions

from gmail_client import MailThread

ROOT = Path(__file__).resolve().parent

CURATE_SYSTEM_PROMPT = """You are an office email analyst. Given a full email thread, curate what a polite reply should cover.

Return ONLY valid JSON with this shape:
{
  "language": "en",
  "recipient_greeting_name": "FirstName",
  "tone": "polite, humble, informative",
  "intent": "one-sentence summary of what the latest inbound needs",
  "reply_style": "substantive | acknowledgment",
  "points_to_address": ["specific question or request 1", "…"],
  "known_answers_from_thread": ["facts already established in the thread that can be stated", "…"],
  "unknowns_to_defer": ["items that must not be invented; say you will confirm", "…"],
  "next_steps": ["clear next step for us or them", "…"],
  "should_reply": true,
  "skip_reason": null
}

Rules:
- Base everything only on the thread. Do not invent facts, dates, prices, ticket IDs, or commitments.
- Prefer the latest inbound message, using earlier messages only for context.
- Almost always set should_reply to true. Draft a reply for FYI / thanks / “for your information” / company notices as a short polite acknowledgment (reply_style=acknowledgment): thank them, confirm you noted the update, no unnecessary questions.
- Use reply_style=substantive when there are questions, approvals, action items, or open loops.
- Set should_reply to false ONLY for clear non-human / no-value cases: newsletters, marketing blasts, automated bounce/delivery reports, calendar invites with no ask, or explicit no-reply@ senders. Explain in skip_reason.
- points_to_address must be concrete asks from the sender (or empty for acknowledgment-only), not a dump of the thread.
- Do NOT use tools, edit files, or browse the repo. Respond with JSON only — no markdown fences.
"""

DRAFT_SYSTEM_PROMPT = """You write polite, humble, and informative office email replies.

You receive a curated JSON brief produced from an email thread. Write the reply the recipient should see.

Rules:
- Follow the brief exactly. Use known_answers_from_thread when stating facts.
- For unknowns_to_defer, say you will confirm and follow up — never invent answers.
- Match the brief's language when possible.
- If reply_style is "acknowledgment", write a short thank-you note (2–4 short paragraphs): greet, acknowledge the update/FYI, confirm you have noted it, polite close. Do not invent follow-up questions.
- If reply_style is "substantive" (or omitted): short greeting → substance addressing each point → next steps → polite closing.
- NEVER paste the JSON, NEVER include thread excerpts, bullet “key points from thread”, TODOs, or drafting notes.
- Do NOT use tools, edit files, or browse the repo.
- Output ONLY the email body as simple HTML: <p> paragraphs, <br> sparingly. No subject, no markdown fences, no <html>/<body>.
"""


def format_thread_for_prompt(thread: MailThread) -> str:
    """Thread text for the model only — never shown in the outbound draft."""
    blocks: list[str] = [f"Subject: {thread.subject}", ""]
    for i, m in enumerate(thread.messages, start=1):
        blocks.append(f"--- Message {i} ---")
        blocks.append(f"From: {m.from_header}")
        blocks.append(f"To: {m.to_header}")
        blocks.append(f"Date: {m.date}")
        blocks.append("")
        blocks.append(m.body_text or m.snippet or "(empty body)")
        blocks.append("")
    return "\n".join(blocks)


def _font_family() -> str:
    return os.environ.get(
        "DRAFT_FONT_FAMILY",
        "Calibri, Arial, Helvetica, sans-serif",
    ).strip() or "Calibri, Arial, Helvetica, sans-serif"


def _font_size() -> str:
    return os.environ.get("DRAFT_FONT_SIZE", "14px").strip() or "14px"


def _require_cursor_key() -> str:
    api_key = os.environ.get("CURSOR_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError(
            "CURSOR_API_KEY is required. Replies are curated and drafted by Cursor. "
            "Create a key at https://cursor.com/dashboard/integrations and set it in "
            "scripts/gmail-draft-agent/.env (see .env.example)."
        )
    return api_key


def _model_id() -> str:
    return os.environ.get("CURSOR_MODEL", "composer-2.5").strip() or "composer-2.5"


def plain_to_html(text: str) -> str:
    """Convert plain paragraphs / HTML fragment into a font-styled HTML body."""
    raw = (text or "").strip()
    if not raw:
        return ""

    raw = re.sub(r"^```(?:html)?\s*", "", raw, flags=re.I)
    raw = re.sub(r"\s*```$", "", raw)
    raw = re.sub(r"(?is)</?(?:html|body|head)[^>]*>", "", raw).strip()

    looks_html = bool(re.search(r"<(?:p|div|br|ul|ol|li|span)\b", raw, re.I))
    if looks_html:
        inner = raw
    else:
        parts = [p.strip() for p in re.split(r"\n\s*\n", raw) if p.strip()]
        if not parts:
            parts = [raw]
        inner = "".join(
            f"<p>{html.escape(p).replace(chr(10), '<br>')}</p>" for p in parts
        )

    font = html.escape(_font_family(), quote=True)
    size = html.escape(_font_size(), quote=True)
    return (
        f'<div style="font-family: {font}; font-size: {size}; '
        f'color: #222222; line-height: 1.5;">'
        f"{inner}"
        f"</div>"
    )


def _parse_curation(raw: str) -> dict:
    text = raw.strip()
    text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.I)
    text = re.sub(r"\s*```$", "", text)
    # If the model wrapped JSON in prose, take the outermost object
    if not text.startswith("{"):
        start, end = text.find("{"), text.rfind("}")
        if start >= 0 and end > start:
            text = text[start : end + 1]
    data = json.loads(text)
    if not isinstance(data, dict):
        raise RuntimeError("Cursor curation did not return a JSON object")
    return data


class SkipReply(Exception):
    """Raised when the LLM decides no reply is needed."""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


def _agent_options() -> AgentOptions:
    return AgentOptions(
        api_key=_require_cursor_key(),
        model=_model_id(),
        name="gmail-draft-agent",
        # Text-only: no shell/edit tools while curating or drafting mail.
        tools=[],
        local=LocalAgentOptions(cwd=str(ROOT)),
    )


def _cursor_prompt(message: str) -> str:
    """One-shot Cursor agent call; returns assistant text."""
    try:
        result = Agent.prompt(message, _agent_options())
    except CursorAgentError as err:
        raise RuntimeError(
            f"Cursor agent failed to start: {err.message} "
            f"(retryable={err.is_retryable})"
        ) from err

    status = getattr(result, "status", None)
    status_s = status if isinstance(status, str) else getattr(status, "value", str(status))
    if status_s == "error":
        raise RuntimeError(f"Cursor agent run failed (id={result.id})")
    text = (result.result or "").strip()
    if not text:
        raise RuntimeError(f"Cursor agent returned empty result (id={result.id})")
    return text


def curate_reply_brief(thread: MailThread, sign_off: str = "") -> dict:
    """Step 1 — ask Cursor what a relevant reply should cover."""
    user = (
        f"{CURATE_SYSTEM_PROMPT}\n\n"
        f"Sign-off name to use later: {sign_off or '(none provided)'}\n\n"
        f"Full email thread:\n{format_thread_for_prompt(thread)}"
    )
    raw = _cursor_prompt(user)
    return _parse_curation(raw)


def draft_from_brief(brief: dict, sign_off: str = "") -> str:
    """Step 2 — ask Cursor to write the HTML reply from the curated brief."""
    payload = {
        "sign_off_name": sign_off or None,
        "curated_brief": brief,
    }
    user = (
        f"{DRAFT_SYSTEM_PROMPT}\n\n"
        "Write the outbound reply HTML from this curated brief. "
        "Do not mention that a brief or JSON was used.\n\n"
        f"{json.dumps(payload, ensure_ascii=False, indent=2)}"
    )
    text = _cursor_prompt(user)
    return plain_to_html(text)


def draft_reply(thread: MailThread, sign_off: str = "") -> str:
    """
    Cursor LLM pipeline (CURSOR_API_KEY):
      1) Curate relevant reply points from the full thread
      2) Draft the formatted HTML reply from that brief
    """
    brief = curate_reply_brief(thread, sign_off=sign_off)
    if brief.get("should_reply") is False:
        reason = brief.get("skip_reason") or "Cursor marked thread as no-reply"
        raise SkipReply(str(reason))
    return draft_from_brief(brief, sign_off=sign_off)
