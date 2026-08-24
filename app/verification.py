"""Email verification — disposable/free-provider/MX/role-account checks.

Uses heuristic checks (no external API needed) for fast local verification.
DNS MX lookup verifies deliverability. Known disposable domains are blocked.
Role accounts (info@, admin@) are flagged but not blocked.
"""
from __future__ import annotations

import dns.resolver
import re
from datetime import datetime, timezone

FREE_PROVIDERS = {
    'gmail.com', 'yahoo.com', 'hotmail.com', 'outlook.com', 'aol.com',
    'icloud.com', 'mail.com', 'protonmail.com', 'proton.me', 'zoho.com',
    'yandex.com', 'gmx.com', 'live.com', 'msn.com', 'me.com',
    'fastmail.com', 'tutanota.com', 'hey.com', 'pm.me',
}

DISPOSABLE_DOMAINS = {
    'guerrillamail.com', 'tempmail.com', 'throwaway.email', 'temp-mail.org',
    '10minutemail.com', 'mailinator.com', 'yopmail.com', 'guerrillamailblock.com',
    'grr.la', 'dispostable.com', 'trashmail.com', 'maildrop.cc',
    'fakeinbox.com', 'sharklasers.com', 'guerrillamail.info', 'grr.la',
    'mailnesia.com', 'tempail.com', 'tempr.email', 'discard.email',
}

ROLE_ACCOUNTS = {
    'info', 'admin', 'support', 'sales', 'contact', 'hello', 'hi',
    'help', 'team', 'office', 'mail', 'webmaster', 'abuse', 'noreply',
    'no-reply', 'postmaster', 'hostmaster', 'abuse', 'billing',
}


def verify_email(email: str) -> dict:
    """Verify an email address locally. Returns dict with checks."""
    email = email.lower().strip()
    parts = email.split('@')
    if len(parts) != 2:
        return {'status': 'invalid', 'mx_valid': False, 'disposable': False,
                'free_provider': False, 'role_account': False}

    local, domain = parts

    is_free = domain in FREE_PROVIDERS
    is_disposable = domain in DISPOSABLE_DOMAINS
    is_role = local.split('.')[0] in ROLE_ACCOUNTS or local in ROLE_ACCOUNTS

    mx_valid = False
    try:
        mx_records = dns.resolver.resolve(domain, 'MX', lifetime=5)
        mx_valid = len(mx_records) > 0
    except (dns.resolver.NoAnswer, dns.resolver.NXDOMAIN,
            dns.resolver.NoNameservers, dns.resolver.Timeout, Exception):
        mx_valid = False

    if is_disposable:
        status = 'risky'
    elif not mx_valid:
        status = 'invalid'
    elif is_role:
        status = 'flagged'
    else:
        status = 'valid'

    return {
        'status': status,
        'mx_valid': mx_valid,
        'disposable': is_disposable,
        'free_provider': is_free,
        'role_account': is_role,
    }
