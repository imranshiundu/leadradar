# LeadRadarSafe Pro

LeadRadarSafe Pro is a lightweight 24/7 outreach operating system. It discovers leads, scores them with AI, runs sequenced campaigns with follow-ups, tracks replies via IMAP, and manages a full pipeline CRM — all from a single Python process with SQLite.

It is not a spam bot. It is not designed to bypass platform limits. The system protects your reputation by using configurable public sources, rate limits, opt-out handling, Telegram approval, reply detection, and conservative email sending.

## What it does

### Lead discovery (original)
- Discovers possible leads through an API-based search provider (Brave Search).
- Reads RSS job/opportunity feeds.
- Monitors Telegram chats where your bot is added and allowed.
- Checks configured seed pages while respecting robots.txt.
- Uses Groq to classify and score leads with a small/cheap model.
- Falls back to deterministic scoring when Groq is disabled or unavailable.
- Sends high-score alerts to Telegram with approve/reject actions.

### Campaigns & sequences (new)
- Multi-step campaigns: initial email + timed follow-ups (e.g. day 3, day 7).
- Template variables: `{{name}}`, `{{first_name}}`, `{{event_name}}`, `{{event_date}}`.
- Global + per-campaign daily send caps with configurable delays.
- Approval gate preserved: campaigns must be activated explicitly.
- Replies automatically stop follow-ups for that contact.

### Reply tracking (new)
- IMAP polling of your Gmail inbox.
- Matches replies to sent messages via Message-ID / In-Reply-To / References chain.
- Keyword classification: interested / maybe_later / not_interested / bounce / ooo / unknown.
- Interested replies trigger Telegram alerts.
- Pipeline auto-advances on reply (replied → replied, not_interested → lost).

### Pipeline CRM (new)
- Stages: new → contacted → replied → meeting → won → lost.
- Drag/move contacts between stages from the pipeline board.
- Campaign analytics per campaign: targets, sent, replies, interested, pipeline breakdown.

### Contact import (new)
- Parses messy human-format contact lists: markdown tables, TSV, CSV, pipe tables, plain lines.
- Extracts name, phone, email, priority (fire emoji counts) automatically.
- Deduplicates by email fingerprint.
- Built for lists like `emails.txt` where rows look like:
  ```
  1	Bigmiitch Events	+254 724 214 461	info@bigmiitchevents.co.ke	🔥🔥🔥
  ```

### Event intelligence (new)
- Heuristic extraction of upcoming event names and dates from lead data.
- Enables hyper-personalized outreach: "I saw your Oct 9 Realtors Summit..."

### Infrastructure
- FastAPI dashboard with pipeline board, campaigns page, replies table.
- SQLite WAL mode, no Redis, no Celery.
- Runs 24/7 with Docker Compose or systemd.
- Optional shared-secret auth for dashboard mutating routes.
- Every major action writes an event to SQLite for audit.

## What it deliberately does not do

- It does not scrape the entire web.
- It does not scrape private Telegram groups or channels.
- It does not harvest personal data.
- It does not bypass spam filters.
- It does not auto-blast cold emails by default.
- It does not use browser automation by default, to keep RAM low.

## How it compares to open-source alternatives

| Feature | LeadRadarSafe Pro | Listmonk | Mautic | Instantly / Woodpecker |
|---|---|---|---|---|
| Lead discovery (search, RSS, Telegram) | ✅ | ❌ | ❌ | ❌ |
| AI lead scoring | ✅ | ❌ | Basic | ❌ |
| Telegram approval workflow | ✅ | ❌ | ❌ | ❌ |
| Sequenced campaigns with follow-ups | ✅ | ✅ | ✅ | ✅ |
| Reply tracking (IMAP) | ✅ | ❌ | ❌ | ✅ |
| Pipeline CRM | ✅ | ❌ | ✅ | ❌ |
| Event intelligence extraction | ✅ | ❌ | ❌ | ❌ |
| Contact import (messy lists) | ✅ | Manual | Manual | CSV only |
| Infra weight | SQLite, <200MB | PostgreSQL, 500MB+ | MySQL, 2GB+ | SaaS only |
| Price | Free | Free | Free | $50-300/mo |
| Self-hosted | ✅ | ✅ | ✅ | ❌ |

**LeadRadarSafe Pro fills a gap:** nothing open-source combines discovery → AI scoring → approval → sequenced outreach → reply tracking → pipeline CRM in one lightweight Python service. SaaS tools like Instantly charge $50-300/mo for the sequence + reply parts alone, without discovery or approval workflows.

## Quick start

```bash
cp .env.example .env
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python run.py
```

Open `http://localhost:8080`.

### Seed a campaign from a contact list

```bash
# Import your contacts.txt and create a campaign from your template
python scripts/seed_taptap.py /path/to/emails.txt /path/to/taptap.txt
```

### Import contacts via API or UI

```bash
# Via API
curl -X POST http://localhost:8080/api/import \
  -F "file=@emails.txt"

# Or paste text in the UI at /import
```

### Create a campaign via API

```bash
curl -X POST http://localhost:8080/api/campaigns \
  -d "name=Taptap Oct-Nov" \
  -d "subject_template=Taptap for {{name}}" \
  -d "body_template=Hi {{first_name}}, we're opening Taptap to events in Oct/Nov..."

# Add a follow-up step (day 3)
curl -X POST http://localhost:8080/api/campaigns/1/steps \
  -d "step_order=1" \
  -d "delay_days=3" \
  -d "body_template=Hi {{first_name}}, following up on my earlier email about {{name}}..."

# Attach contacts and activate
curl -X POST http://localhost:8080/api/campaigns/1/attach
curl -X POST http://localhost:8080/api/campaigns/1/status -d "status=active"
```

### Poll for replies

```bash
curl -X POST http://localhost:8080/api/inbox/poll
```

## Docker start

```bash
cp .env.example .env
# edit .env first
docker compose up -d --build
```

## Required API keys

Minimum useful setup:

1. `SMTP_USERNAME`, `SMTP_APP_PASSWORD`, and `SMTP_FROM_EMAIL` for email sending.
2. `BRAVE_SEARCH_API_KEY` for business discovery (optional).
3. `TELEGRAM_BOT_TOKEN` and `TELEGRAM_OWNER_CHAT_ID` for alerts (optional).
4. `GROQ_API_KEY` for AI scoring (optional; has fallback).
5. `DASHBOARD_TOKEN` to protect dashboard from unauthorized use (recommended for production).

## Gmail App Password + IMAP setup

1. Enable 2-Step Verification on your Gmail.
2. Create an App Password at https://myaccount.google.com/apppasswords.
3. Put it in `.env` as `SMTP_APP_PASSWORD`.
4. Ensure IMAP is enabled in Gmail Settings → Forwarding and POP/IMAP.

```env
SMTP_USERNAME=you@gmail.com
SMTP_APP_PASSWORD=xxxx-xxxx-xxxx-xxxx
SMTP_FROM_EMAIL=you@gmail.com
SMTP_FROM_NAME=Your Name
IMAP_HOST=imap.gmail.com
IMAP_PORT=993
INBOX_POLL_ENABLED=true
```

## Safety rules baked into the code

- Robots.txt respected for seed page fetching.
- Search discovery uses an API instead of scraping result pages.
- Duplicate leads blocked by SHA-256 fingerprints.
- Personal emails skipped for automatic cold business outreach.
- Opt-outs stored permanently.
- Groq receives redacted snippets, not raw personal contact fields.
- Email is approval-first by default.
- Scheduler jobs are capped and cannot overlap.
- Reply classification is keyword-based and conservative — unclear replies stay "unknown" for human review.
- No auto-replies are ever sent.

## Architecture

See `docs/ARCHITECTURE.md` for the full module breakdown, data model, and scaling path.
