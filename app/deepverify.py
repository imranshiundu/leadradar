"""SMTP-level mailbox verification: does this inbox actually exist?

MX lookup (dnspython) -> SMTP handshake probe against the mail exchanger.
Verdicts: 'valid' (accepted, not catch-all), 'catch_all' (domain accepts
anything), 'invalid' (mailbox rejected), 'risky' (greylist/tempfail),
'unreachable' (no usable MX/25).
"""
from __future__ import annotations

import random
import re
import smtplib
import socket
from email.utils import parseaddr

import dns.resolver

_EMAIL_RE = re.compile(r'^[^@\s]+@[^@\s]+\.[^@\s]+$')


def get_mx_hosts(domain: str) -> list[str]:
    try:
        answers = dns.resolver.resolve(domain, 'MX', lifetime=10)
        hosts = sorted(str(r.exchange).rstrip('.').lower() + f':{r.preference}' for r in answers)
        return [h.rsplit(':', 1)[0] for h in hosts]
    except Exception:  # noqa: BLE001
        return []


def _classify(rcpt_code: int | None, catchall_code: int | None) -> str:
    if rcpt_code is None:
        return 'unreachable'
    if 200 <= rcpt_code < 300:
        if catchall_code is not None and 200 <= catchall_code < 300:
            return 'catch_all'
        return 'valid'
    if rcpt_code in (550, 551, 553):
        return 'invalid'
    if 400 <= rcpt_code < 500:
        return 'risky'
    return 'risky'


def smtp_probe(email_addr: str, helo_domain: str = 'taptap.africa',
               from_addr: str = 'imran@taptap.africa', timeout: int = 15) -> dict:
    """Probe one address. Returns {status, mx}. Never raises."""
    email_addr = (parseaddr(email_addr)[1] or email_addr).strip().lower()
    out = {'email': email_addr, 'status': 'unreachable', 'mx': ''}
    if not _EMAIL_RE.match(email_addr):
        out['status'] = 'invalid'
        return out
    domain = email_addr.split('@')[1]
    mxs = get_mx_hosts(domain)
    if not mxs:
        # RFC 5321 fallback: implicit A record.
        try:
            socket.gethostbyname(domain)
            mxs = [domain]
        except socket.error:
            out['status'] = 'invalid'
            return out
    out['mx'] = mxs[0]
    for host in mxs[:2]:
        code = catchall_code = None
        server = None
        try:
            server = smtplib.SMTP(timeout=timeout)
            server.connect(host, 25)
            server.ehlo_or_helo_if_needed()
            code_tuple = server.docmd('MAIL', f'FROM:<{from_addr}>')
            if code_tuple[0] >= 400:
                out['status'] = 'risky'
                continue
            code = server.docmd('RCPT', f'TO:<{email_addr}>')[0]
            probe2 = f"probe-{random.randint(10**8, 10**9 - 1)}@{domain}"
            catchall_code = server.docmd('RCPT', f'TO:<{probe2}>')[0]
        except (smtplib.SMTPServerDisconnected, socket.timeout, ConnectionRefusedError, OSError):
            out['status'] = 'unreachable'
            continue
        finally:
            try:
                if server is not None:
                    server.quit()
            except Exception:  # noqa: BLE001
                pass
        out['status'] = _classify(code, catchall_code)
        if out['status'] != 'unreachable':
            break
    return out
