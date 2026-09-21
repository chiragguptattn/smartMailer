#!/usr/bin/env python3
"""
Gmail draft agent — connect to Gmail, read unread threads from a sender domain,
analyze the chain, and create polite draft replies (never auto-sends).

Commands:
  python main.py auth          # one-time OAuth browser login
  python main.py whoami        # show connected mailbox
  python main.py list          # list matching unread threads
  python main.py run           # one-shot draft pass
  python main.py watch         # always-on poller (no manual trigger)
"""

from __future__ import annotations

import argparse
import logging
import os
import signal
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv

from draft_replies import SkipReply, draft_reply, format_thread_for_prompt
from gmail_auth import load_credentials
from gmail_client import GmailClient, build_service

ROOT = Path(__file__).resolve().parent
LOG_DIR = ROOT / "logs"
PID_FILE = ROOT / "watch.pid"

_stop = False


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def _bool_env(name: str) -> bool:
    return _env(name).lower() in {"1", "true", "yes", "on"}


def _int_env(name: str, default: int) -> int:
    raw = _env(name)
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def connect() -> GmailClient:
    creds_path = _env("GMAIL_CREDENTIALS_PATH", str(ROOT / "credentials.json"))
    token_path = _env("GMAIL_TOKEN_PATH", str(ROOT / "token.json"))
    creds = load_credentials(creds_path, token_path)
    return GmailClient(build_service(creds))


def setup_logging(*, watch: bool = False) -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    handlers: list[logging.Handler] = [logging.StreamHandler(sys.stdout)]
    if watch:
        fh = logging.FileHandler(LOG_DIR / "watch.log", encoding="utf-8")
        handlers.append(fh)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        handlers=handlers,
        force=True,
    )


@dataclass
class RunConfig:
    domain: str
    max_threads: int
    label: str
    dry_run: bool
    unread_only: bool
    sign_off: str
    verbose: bool = False


def load_run_config(args: argparse.Namespace) -> RunConfig | None:
    domain = getattr(args, "domain", None) or _env("GMAIL_FROM_DOMAIN")
    if not domain:
        logging.error("Set GMAIL_FROM_DOMAIN or pass --domain")
        return None
    if not _env("CURSOR_API_KEY"):
        logging.error(
            "CURSOR_API_KEY is required. Add it to .env — see .env.example / "
            "https://cursor.com/dashboard/integrations"
        )
        return None
    if domain.lower().lstrip("@") in {"example.com", "example.org", "your-domain.com"}:
        logging.warning(
            "GMAIL_FROM_DOMAIN=%r looks like a placeholder; set the real sender domain.",
            domain,
        )

    include_read = bool(getattr(args, "include_read", False))
    dry = bool(getattr(args, "dry_run", False)) or _bool_env("GMAIL_DRY_RUN")
    limit = getattr(args, "limit", 0) or _int_env("GMAIL_MAX_THREADS", 10)
    return RunConfig(
        domain=domain,
        max_threads=limit,
        label=_env("GMAIL_PROCESSED_LABEL", "AI/Drafted") or "AI/Drafted",
        dry_run=dry,
        unread_only=not include_read,
        sign_off=_env("AGENT_SIGN_OFF_NAME"),
        verbose=bool(getattr(args, "verbose", False)),
    )


def process_batch(client: GmailClient, cfg: RunConfig) -> dict[str, int]:
    """Process matching threads once. Returns counts."""
    stats = {"found": 0, "drafted": 0, "skipped": 0, "failed": 0}
    mailbox = client.profile_email()
    ids = client.list_thread_ids(
        cfg.domain,
        max_results=cfg.max_threads,
        exclude_label=cfg.label,
        unread_only=cfg.unread_only,
    )
    stats["found"] = len(ids)
    if not ids:
        logging.info(
            "No matching %s threads from *@%s",
            "unread" if cfg.unread_only else "",
            cfg.domain.lstrip("@"),
        )
        return stats

    logging.info(
        "Processing %d thread(s) from *@%s (mailbox=%s dry_run=%s)",
        len(ids),
        cfg.domain.lstrip("@"),
        mailbox,
        cfg.dry_run,
    )

    for tid in ids:
        if _stop:
            break
        thread = client.get_thread(tid)
        logging.info(
            "Thread %s: %r (%d messages)",
            tid,
            thread.subject,
            len(thread.messages),
        )
        if cfg.verbose:
            logging.info("Thread text:\n%s", format_thread_for_prompt(thread))

        try:
            body = draft_reply(thread, sign_off=cfg.sign_off)
        except SkipReply as e:
            stats["skipped"] += 1
            logging.info("Skipped (LLM): %s", e.reason)
            if not cfg.dry_run:
                client.mark_thread_read(tid)
                client.apply_label_to_thread(tid, cfg.label)
                logging.info("Marked read and labeled %s", cfg.label)
            continue
        except RuntimeError as e:
            stats["failed"] += 1
            logging.error("Failed: %s", e)
            continue

        logging.info("Draft preview:\n%s", body)

        if cfg.dry_run:
            logging.info("dry-run: not writing draft")
            continue

        draft_id = client.create_reply_draft(thread, body_html=body, from_email=mailbox)
        client.apply_label_to_thread(tid, cfg.label)
        client.mark_thread_read(tid)
        stats["drafted"] += 1
        logging.info(
            "Created draft %s; labeled %s; marked read",
            draft_id,
            cfg.label,
        )

    return stats


def cmd_auth(_: argparse.Namespace) -> int:
    setup_logging()
    client = connect()
    email = client.profile_email()
    logging.info("Connected as %s", email)
    logging.info("Token saved to %s", _env("GMAIL_TOKEN_PATH", str(ROOT / "token.json")))
    return 0


def cmd_whoami(_: argparse.Namespace) -> int:
    setup_logging()
    client = connect()
    print(client.profile_email())
    return 0


def cmd_list(args: argparse.Namespace) -> int:
    setup_logging()
    domain = args.domain or _env("GMAIL_FROM_DOMAIN")
    if not domain:
        logging.error("Set GMAIL_FROM_DOMAIN or pass --domain")
        return 2

    max_threads = args.limit or _int_env("GMAIL_MAX_THREADS", 10)
    label = _env("GMAIL_PROCESSED_LABEL", "AI/Drafted")
    unread_only = not args.include_read
    if domain.lower().lstrip("@") in {"example.com", "example.org", "your-domain.com"}:
        logging.warning(
            "GMAIL_FROM_DOMAIN=%r looks like a placeholder. "
            "Set the real sender domain in .env.",
            domain,
        )
    client = connect()
    ids = client.list_thread_ids(
        domain,
        max_results=max_threads,
        exclude_label=label,
        unread_only=unread_only,
    )
    scope = "unread" if unread_only else "all"
    logging.info(
        "Found %d %s thread(s) from *@%s (excluding label %s)",
        len(ids),
        scope,
        domain.lstrip("@"),
        label,
    )
    if not ids and unread_only:
        logging.info(
            "Tip: confirm unread mail exists from that domain. "
            "Try --include-read or change GMAIL_FROM_DOMAIN."
        )
    for tid in ids:
        thread = client.get_thread(tid)
        latest = thread.latest
        from_h = latest.from_header if latest else "?"
        print(f"- {tid} | {thread.subject!r} | last from: {from_h}")
    return 0


def cmd_run(args: argparse.Namespace) -> int:
    setup_logging()
    cfg = load_run_config(args)
    if not cfg:
        return 2
    client = connect()
    process_batch(client, cfg)
    return 0


def _handle_signal(signum: int, _frame: object) -> None:
    global _stop
    logging.info("Received signal %s — stopping after current work", signum)
    _stop = True


def cmd_watch(args: argparse.Namespace) -> int:
    """Always-on poller: process unread mail, sleep, repeat."""
    global _stop
    _stop = False
    setup_logging(watch=True)
    cfg = load_run_config(args)
    if not cfg:
        return 2

    interval = args.interval or _int_env("GMAIL_POLL_SECONDS", 120)
    if interval < 30:
        logging.warning("Raising poll interval from %s to 30s (API-friendly minimum)", interval)
        interval = 30

    signal.signal(signal.SIGINT, _handle_signal)
    signal.signal(signal.SIGTERM, _handle_signal)

    PID_FILE.write_text(str(os.getpid()), encoding="utf-8")
    logging.info(
        "Watch started pid=%s domain=*@%s interval=%ss dry_run=%s model=%s",
        os.getpid(),
        cfg.domain.lstrip("@"),
        interval,
        cfg.dry_run,
        _env("CURSOR_MODEL") or "composer-2.5",
    )
    logging.info("Logs: %s", LOG_DIR / "watch.log")

    try:
        while not _stop:
            started = datetime.now(timezone.utc)
            try:
                client = connect()
                stats = process_batch(client, cfg)
                logging.info(
                    "Cycle done found=%s drafted=%s skipped=%s failed=%s",
                    stats["found"],
                    stats["drafted"],
                    stats["skipped"],
                    stats["failed"],
                )
            except Exception:
                logging.exception("Cycle failed; will retry after sleep")

            # Sleep in short chunks so SIGTERM stops quickly
            elapsed = (datetime.now(timezone.utc) - started).total_seconds()
            remaining = max(0, interval - int(elapsed))
            logging.info("Sleeping %ss until next poll", remaining)
            for _ in range(remaining):
                if _stop:
                    break
                time.sleep(1)
    finally:
        if PID_FILE.exists():
            try:
                if PID_FILE.read_text(encoding="utf-8").strip() == str(os.getpid()):
                    PID_FILE.unlink(missing_ok=True)
            except OSError:
                pass
        logging.info("Watch stopped")

    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Gmail domain-filter draft agent")
    sub = p.add_subparsers(dest="command", required=True)

    auth = sub.add_parser("auth", help="OAuth login and save token")
    auth.set_defaults(func=cmd_auth)

    who = sub.add_parser("whoami", help="Print connected Gmail address")
    who.set_defaults(func=cmd_whoami)

    lst = sub.add_parser("list", help="List matching unread threads")
    lst.add_argument("--domain", help="Sender domain, e.g. partner.com")
    lst.add_argument("--limit", type=int, default=0, help="Max threads")
    lst.add_argument(
        "--include-read",
        action="store_true",
        help="Include already-read threads (default: unread only)",
    )
    lst.set_defaults(func=cmd_list)

    run = sub.add_parser("run", help="One-shot: draft replies for matching unread threads")
    run.add_argument("--domain", help="Sender domain, e.g. partner.com")
    run.add_argument("--limit", type=int, default=0, help="Max threads")
    run.add_argument("--dry-run", action="store_true", help="Do not write drafts")
    run.add_argument("-v", "--verbose", action="store_true", help="Print full thread text")
    run.add_argument(
        "--include-read",
        action="store_true",
        help="Include already-read threads (default: unread only)",
    )
    run.set_defaults(func=cmd_run)

    watch = sub.add_parser(
        "watch",
        help="Always-on: poll unread mail and draft replies automatically",
    )
    watch.add_argument("--domain", help="Sender domain, e.g. partner.com")
    watch.add_argument("--limit", type=int, default=0, help="Max threads per cycle")
    watch.add_argument(
        "--interval",
        type=int,
        default=0,
        help="Seconds between polls (default: GMAIL_POLL_SECONDS or 120)",
    )
    watch.add_argument("--dry-run", action="store_true", help="Do not write drafts")
    watch.add_argument("-v", "--verbose", action="store_true", help="Print full thread text")
    watch.add_argument(
        "--include-read",
        action="store_true",
        help="Include already-read threads (default: unread only)",
    )
    watch.set_defaults(func=cmd_watch)

    return p


def main(argv: list[str] | None = None) -> int:
    load_dotenv(ROOT / ".env")
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
