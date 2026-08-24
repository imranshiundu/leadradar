from __future__ import annotations

import hashlib
import re
import time
import urllib.parse
import urllib.robotparser
from dataclasses import dataclass, field
from typing import Iterable

EMAIL_RE = re.compile(r'(?i)\b[a-z0-9._%+\-]+@[a-z0-9.\-]+\.[a-z]{2,}\b')
PHONE_RE = re.compile(r'(?:(?:\+?254|0)\s?\d{3}\s?\d{3}\s?\d{3}|\+?\d[\d\s().-]{7,}\d)')
URL_RE = re.compile(r'https?://[^\s<>)"\']+')
SOCIAL_DOMAINS = ('facebook.com', 'instagram.com', 'linkedin.com', 'x.com', 'twitter.com', 'tiktok.com')
PERSONAL_EMAIL_DOMAINS = {'gmail.com', 'yahoo.com', 'outlook.com', 'hotmail.com', 'icloud.com', 'proton.me'}


def normalize_text(value: str | None) -> str:
    return re.sub(r'\s+', ' ', (value or '').strip())


def fingerprint(*parts: str | None) -> str:
    joined = '|'.join(normalize_text(p).lower() for p in parts if p)
    return hashlib.sha256(joined.encode('utf-8')).hexdigest()


def extract_emails(text: str) -> list[str]:
    return sorted(set(m.group(0).lower() for m in EMAIL_RE.finditer(text or '')))


def extract_phones(text: str) -> list[str]:
    phones = []
    for match in PHONE_RE.finditer(text or ''):
        cleaned = re.sub(r'\s+', ' ', match.group(0).strip())
        if len(re.sub(r'\D', '', cleaned)) >= 8:
            phones.append(cleaned)
    return sorted(set(phones))


def extract_urls(text: str) -> list[str]:
    return sorted(set(m.group(0).rstrip('.,)') for m in URL_RE.finditer(text or '')))


def domain_of(url_or_email: str | None) -> str | None:
    if not url_or_email:
        return None
    if '@' in url_or_email and not url_or_email.startswith('http'):
        return url_or_email.rsplit('@', 1)[-1].lower()
    try:
        parsed = urllib.parse.urlparse(url_or_email if '://' in url_or_email else f'https://{url_or_email}')
        host = parsed.netloc.lower()
        return host[4:] if host.startswith('www.') else host
    except Exception:
        return None


def is_social_url(url: str | None) -> bool:
    host = domain_of(url)
    return bool(host and any(d in host for d in SOCIAL_DOMAINS))


def looks_like_personal_email(email: str | None) -> bool:
    host = domain_of(email)
    return bool(host in PERSONAL_EMAIL_DOMAINS)


def redact_personal_data(text: str | None) -> str:
    if not text:
        return ''
    text = EMAIL_RE.sub('[email-redacted]', text)
    text = PHONE_RE.sub('[phone-redacted]', text)
    return text


def has_blocked_domain(url: str | None, blocked_domains: Iterable[str]) -> bool:
    if not url:
        return False
    lowered = url.lower()
    return any(blocked.lower() in lowered for blocked in blocked_domains)


@dataclass
class RobotsCache:
    user_agent: str
    ttl_seconds: int = 3600
    cache: dict[str, tuple[float, urllib.robotparser.RobotFileParser]] = field(default_factory=dict)

    def allowed(self, url: str) -> bool:
        parsed = urllib.parse.urlparse(url)
        if not parsed.scheme or not parsed.netloc:
            return False
        base = f'{parsed.scheme}://{parsed.netloc}'
        now = time.time()
        item = self.cache.get(base)
        if item and now - item[0] < self.ttl_seconds:
            rp = item[1]
        else:
            rp = urllib.robotparser.RobotFileParser()
            rp.set_url(f'{base}/robots.txt')
            try:
                rp.read()
            except Exception:
                # Conservative but practical: if robots cannot be fetched, allow seed-level HTTP fetches only.
                return True
            self.cache[base] = (now, rp)
        return rp.can_fetch(self.user_agent, url)


def safe_outreach_message(name: str, issue: str | None = None) -> str:
    issue_line = f" I noticed {issue.strip()}" if issue else ""
    return (
        f"Hi {name},\n\n"
        f"I’m Imran. I build clean, fast websites and contact/booking pages for small businesses."
        f"{issue_line}\n\n"
        "If useful, I can send you two simple ideas for improving your online presence. "
        "No pressure — if this is not relevant, reply 'no' and I will not contact you again.\n\n"
        "Best,\nImran"
    )


def score_website_need(name: str, website_url: str | None, social_url: str | None, raw_text: str | None) -> tuple[int, str]:
    score = 35
    reasons: list[str] = []
    text = (raw_text or '').lower()
    if not website_url:
        score += 30
        reasons.append('no standalone website detected')
    if social_url and not website_url:
        score += 15
        reasons.append('appears to rely on social page')
    if any(word in text for word in ['whatsapp', 'dm', 'inbox', 'call us']):
        score += 10
        reasons.append('contact flow appears manual/social-first')
    if any(word in text for word in ['book', 'booking', 'appointment', 'order', 'delivery']):
        score += 10
        reasons.append('could benefit from booking/contact/order flow')
    if looks_like_personal_email(extract_emails(raw_text or '')[0] if extract_emails(raw_text or '') else None):
        score += 5
        reasons.append('uses personal email domain')
    return min(score, 100), '; '.join(reasons) or 'general potential fit'
