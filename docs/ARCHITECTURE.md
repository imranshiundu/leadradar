# LeadRadarSafe Pro Architecture

## Core loop (v2)

1. Source connector discovers an item (search, RSS, Telegram, seed pages).
2. Safety layer normalizes and fingerprints it.
3. SQLite dedupes it.
4. Groq classifies it. If Groq fails, deterministic scoring handles it.
5. High-score items are sent to Telegram.
6. You approve or reject.
7. Contacts are imported via API/UI (manual lists, CSV, emails.txt format).
8. Campaigns are created with templates and follow-up steps.
9. Campaign engine sends due messages respecting daily caps, delays, and opt-outs.
10. IMAP poller detects replies, classifies intent, advances pipeline, stops follow-ups.
11. Pipeline board shows funnel: new → contacted → replied → meeting → won → lost.
12. Events are logged for audit.

## Modules

| Module | Responsibility |
|---|---|
| `app/main.py` | FastAPI app, dashboard, all API routes, optional token auth. |
| `app/db.py` | SQLite schema with migrations, lead/campaign/message/reply storage, analytics queries. |
| `app/config.py` | Pydantic-settings: SMTP, IMAP, Telegram, Groq, dashboard token, rate limits. |
| `app/scheduler.py` | APScheduler: discovery, telegram poll, email queue, campaign queue, inbox poll. |
| `app/ai.py` | Groq classification with JSON-only prompt. |
| `app/safety.py` | Redaction, fingerprints, robots, scoring, safe copy. |
| `app/sources/search.py` | Brave Search API business discovery. |
| `app/sources/rss.py` | RSS job/opportunity ingestion. |
| `app/sources/seed_pages.py` | Polite seed page fetching. |
| `app/outreach/telegram.py` | Telegram alerts, approvals, allowed-chat ingestion. |
| `app/outreach/emailer.py` | SMTP sending with Message-ID generation for reply tracking. |
| `app/campaigns.py` | Campaign engine: attach contacts, process due sends, rate limits, follow-up scheduling. |
| `app/inbox.py` | IMAP reply tracker: polls inbox, matches to sent messages, classifies replies, stops sequences. |
| `app/importer.py` | Contact list parser: messy human-format lists (TSV, CSV, pipe, markdown tables). |
| `app/models.py` | Pydantic models and enums. |

## Data model

### leads
Source, contact info, status, pipeline stage, AI scoring, event intelligence, fingerprint for dedup.

Pipeline stages: `new` → `contacted` → `replied` → `meeting` → `won` → `lost`

### campaigns
Name, subject/body templates, status (`draft`/`active`/`paused`/`done`), daily limit, min send delay.

### sequence_steps
Follow-up steps per campaign: step order, delay days, subject/body template overrides.

### campaign_leads
Join table: which contacts are in which campaign, current step, next send time, finished flag, stop reason.

### messages
Outbound log: lead, campaign, step, to_email, subject, body, RFC Message-ID, status (sent/failed), error.

### replies
Inbound matches: lead, from_email, subject, snippet, classification (interested/maybe/not_interested/bounce/ooo/unknown).

### import_batches
Audit log of contact imports: filename, total rows, imported, duplicates, missing email count.

### events_log
Audit trail: app starts, discovery runs, status changes, email sends, inbox polls, errors.

### source_state
Resumable state: Telegram update offset, IMAP UID cursor.

### opt_outs
Emails that must never be contacted again.

## Runtime profile

Designed for a small VPS:

- Python FastAPI process.
- SQLite WAL mode.
- No Redis, no Celery, no browser automation.
- Default memory target: comfortably under 200MB in normal operation.

## Scaling path

Only add complexity after revenue starts:

1. Add official Google Places API connector.
2. Add authenticated dashboard (OAuth2 or SSO).
3. Add email open/click tracking (pixel/beacon).
4. Add CRM pipeline stages with custom fields.
5. Move SQLite to Postgres when concurrent writes exceed ~100/s.
6. Add Playwright only for sites where terms allow it.
7. Add A/B testing on campaign templates.
8. Add domain warmup tracking.
