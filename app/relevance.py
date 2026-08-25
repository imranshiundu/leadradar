"""Host relevance scoring — is this address actually an event organizer/host?

Written after the first blast went to SaaS providers, recruiters and personal
contacts. Existence of a mailbox means nothing; this module decides whether an
address belongs in Taptap outreach at all.
"""
from __future__ import annotations

import re

STRONG = re.compile(
    r'event|planner|organis|organiz|hosting\b|hosts?\b|mce\b|entertain|sound|'
    r'decor|decorat|cater|photograph|videograph|venue|ticket|festival|concert|'
    r'conference|wedding|party|expo|bashes?|raves?|tours?|fun\s*world|amusement|'
    r'stage|lighting|pa\s*hire|band|dj|artistes|talent|branding|marquee|tents', re.I)
WEAK = re.compile(r'bookings?|events?@|hello@|info@|team@|admin@', re.I)
SAAS_NOISE = re.compile(
    r'noreply|no-reply|donotreply|notifications?|mailer-daemon|postmaster|'
    r'receipts|invoices?|billing|payments?|support@|help@|careers?|jobs?|people@|'
    r'hr@|recruit|paytransparency|newsletter|updates?|digest|alerts?|security|'
    r'privacy|legal|abuse|compliance|marketing@|growth@|product@|devrel|community', re.I)
PERSONAL_DOMAINS_OK = True  # organizers often use gmail/yahoo; handled via name signals


def relevance_score(email_addr: str, name: str = '', raw_text: str = '',
                    website: str = '', event_name: str = '') -> int:
    """0..10. >=5 = confident host, 3-4 plausible, <3 = not outreach material."""
    blob = ' '.join([email_addr or '', name or '', raw_text or '', website or '', event_name or ''])
    score = 0
    if STRONG.search(blob):
        score += 4
    if event_name:
        score += 2
    if WEAK.search(email_addr or '') and score > 0:
        score += 1
    if re.search(r'\.co\.ke$|\.or\.ke$|\.ne\.ke$|kenya|nairobi|mombasa', blob, re.I):
        score += 2
    if SAAS_NOISE.search(email_addr or '') or SAAS_NOISE.search(name or ''):
        score -= 6
    # Known SaaS/notification domains seen polluting the first blast
    if re.search(r'@(supabase|resend|clickup|migadu|truehost|vercel|github|notion|'
                 r'linear|figma|stripe|microsoft|google|amazonaws|heroku|netlify)'
                 r'\.(com|io|app|cc|org)$', email_addr or '', re.I):
        score -= 8
    return max(0, min(10, score))


def is_blast_eligible(score: int) -> bool:
    return score >= 5
