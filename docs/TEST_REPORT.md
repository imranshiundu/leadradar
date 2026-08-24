# Test Report

## Performed in this build

- Static Python compilation passed for `app/`, `tests/`, `run.py`, and `scripts/`.
- Unit tests are included for safety helpers and SQLite deduplication.
- Runtime integration tests that require network/API keys were not executed in this sandbox.

## How to test locally

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
pytest -q
python run.py
```

Then open:

```text
http://localhost:8080/health
```

## Test live integrations one-by-one

1. Start with no keys. Confirm dashboard and health endpoint work.
2. Add Telegram token and owner chat ID. Confirm alerts send.
3. Add Brave key. Run discovery once.
4. Add Groq key. Confirm AI summaries appear.
5. Add SMTP credentials. Send one manual test email to yourself.
6. Only after testing, consider `AUTO_SEND_EMAILS=true` with low limits.
