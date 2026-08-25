"""Local host discovery — crawls from YOUR machine (residential IP, no bot walls),
pushes candidates to the LeadRadar server for scoring + dedupe + storage.

Usage:
  python3 scripts/discover_local.py --api http://169.58.128.213/lr \
      --email you@gmail.com --password '...' [--target 300]

Sources: Bing search (base64 decode), Eventbrite Kenya, BrighterMonday jobs,
crt.name CT subdomain discovery. Every candidate is relevance-scored server-side.
"""
from __future__ import annotations

import argparse
import re
import sys
import time
from pathlib import Path

import base64
import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
H = {'User-Agent': 'Mozilla/5.0 (X11; Linux x86_64; rv:128.0) Gecko/20100101 Firefox/128.0'}
EMAIL_RE = re.compile(r'[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}')
BAD_EXT = re.compile(r'\.(png|jpe?g|gif|webp|svg|css|js|ico)$', re.I)
QUERIES = [
    'event planners nairobi directory',
    'event management companies nairobi contact details',
    'wedding planners kenya contacts email',
    'event organisers mombasa contact',
    'sound and decor companies nairobi contact',
    'corporate events company kenya email contact',
    'party rentals events equipment nairobi contacts',
    'site:businesslist.co.ke event planners',
    'kenya events companies @gmail.com',
    'nairobi mc entertainment events contacts',
]

CRT_DOMAINS = [
    'eventbrite.co.ke', 'eventbrite.com',
    'taptap.africa',
    'brightermonday.co.ke',
    'businesslist.co.ke',
    'nairobifunctionhalls.com',
    'eventpark.co.ke',
    'ticketbox.co.ke',
    'mkeja.co.ke',
    'kenyabuzz.com',
]


MAILTO_RE = re.compile(r'mailto:([^\x00-\x1f\"?>\s]+)', re.I)


def extract_emails(text: str) -> list[str]:
    return _dedupe(EMAIL_RE.findall(text or '')) + [e for e in _dedupe(
        [m.strip().lower() for m in MAILTO_RE.findall(text or '')]
    ) if e]


def _dedupe(addrs) -> list[str]:
    seen, out = set(), []
    for m in addrs:
        e = str(m).strip('.?!,;').lower()
        if BAD_EXT.search(e) or len(e) > 80 or '@' not in e or e in seen:
            continue
        seen.add(e)
        out.append(e)
    return out


def _extract_legacy(text: str) -> list[str]:
    seen, out = set(), []
    for m in EMAIL_RE.findall(text or ''):
        e = m.strip('.').lower()
        if BAD_EXT.search(e) or len(e) > 80 or e not in seen:
            continue
        seen.add(e)
        out.append(e)
    return out


def login(api: str, email: str, password: str) -> str:
    r = httpx.post(f'{api}/api/auth/login', json={'email': email, 'password': password}, timeout=20)
    r.raise_for_status()
    return r.json()['token']


def push(api: str, token: str, items: list[dict]) -> dict:
    r = httpx.post(f'{api}/api/leads/import-emails', json={'items': items},
                   headers={'X-Session-Token': token}, timeout=30)
    r.raise_for_status()
    return r.json()


SKIP_DOMAINS = re.compile(
    r'(wikipedia|britannica|worldatlas|bing|microsoft|duckduckgo|youtube|facebook|'
    r'instagram|twitter|x\.com|tiktok|pinterest|linkedin|google|amazon|aliexpress)', re.I)


def bing_links(cx: httpx.Client, q: str) -> list[str]:
    r = cx.get('https://www.bing.com/search', params={'q': q, 'count': 25})
    if r.status_code != 200:
        return []
    out, seen = [], set()
    for wrapped in re.findall(r'href="(https://www\.bing\.com/ck/a\?[^"]+)"', r.text):
        m = re.search(r'u=a1([A-Za-z0-9+/=_-]+)', wrapped)
        if not m:
            continue
        b64 = m.group(1).replace('-', '+').replace('_', '/')
        b64 += '=' * (-len(b64) % 4)
        try:
            url = base64.b64decode(b64).decode('utf-8', errors='ignore')
        except Exception:  # noqa: BLE001
            continue
        clean = url.rstrip('/')
        if clean.startswith('http') and clean not in seen and not SKIP_DOMAINS.search(clean):
            seen.add(clean)
            out.append(clean)
    return out


def ddg_links(cx: httpx.Client, q: str) -> list[str]:
    r = cx.get('https://html.duckduckgo.com/html/', params={'q': q})
    if r.status_code != 200:
        return []
    links = re.findall(r'href="(https?://[^"]+)"[^>]*class="result__a"', r.text)
    if not links:
        links = [u.split('uddg=')[-1].split('&rut=')[0]
                 for u in re.findall(r'href="//duckduckgo\.com/l/\?uddg=([^&]+)', r.text)]
    out, seen = [], set()
    for u in links:
        u = httpx.URL(u).params.get('uddg', u) if 'uddg=' in u else u
        clean = u.rstrip('/')
        if clean.startswith('http') and clean not in seen and 'duckduckgo' not in clean:
            seen.add(clean)
            out.append(clean)
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument('--api', default='http://169.58.128.213/lr')
    ap.add_argument('--email', required=True)
    ap.add_argument('--password', required=True)
    ap.add_argument('--target', type=int, default=300)
    args = ap.parse_args()

    token = login(args.api, args.email, args.password)
    print('logged in')

    leads = httpx.get(f'{args.api}/api/leads?limit=5000',
                      headers={'X-Session-Token': token}, timeout=30).json()['leads']
    active = sum(1 for l in leads if l.get('status') != 'rejected')
    needed = max(args.target - active, 0)
    print(f'server has {active} active hosts ({len(leads)} total); need {needed} more to reach {args.target}')
    if needed == 0:
        print('target already met')
        return
    if needed == 0:
        return

    candidates: dict[str, dict] = {}
    with httpx.Client(timeout=25, follow_redirects=True, headers=H) as cx:
        # Phase A: search results -> page harvest
        for qi, q in enumerate(QUERIES):
            if len(candidates) >= max(needed * 6, 120):
                break
            print(f'[search {qi+1}/{len(QUERIES)}] {q[:50]}')
            urls = bing_links(cx, q) or ddg_links(cx, q)
            print(f'   {len(urls)} result pages')
            detail_pool = []
            for url in urls[:8]:
                try:
                    pr = cx.get(url)
                    raw = pr.text
                    body = re.sub(r'<[^>]+>', ' ', raw)
                    for e in extract_emails(body):
                        candidates.setdefault(e, {'email': e, 'name': '', 'source': url[:100]})
                    # directory listings: queue profile pages one level deep
                    for m in re.findall(r'href="(/(?:company|business|listing|profile)/[^"\s]+)"', raw)[:12]:
                        if m.startswith('/'):
                            detail_pool.append(url.split('/')[0] + '//' + url.split('/')[2] + m)
                        else:
                            detail_pool.append(m)
                except Exception:  # noqa: BLE001
                    continue
                time.sleep(0.35)
            for durl in list(dict.fromkeys(detail_pool))[:25]:
                try:
                    dr = cx.get(durl)
                    for e in extract_emails(re.sub(r'<[^>]+>', ' ', dr.text)):
                        candidates.setdefault(e, {'email': e, 'name': '', 'source': durl[:100]})
                except Exception:  # noqa: BLE001
                    continue
                time.sleep(0.3)
            print(f'   candidates so far: {len(candidates)}')

        # Phase B: Eventbrite Kenya -> event pages
        event_urls: list[str] = []
        for city in ('kenya--nairobi', 'kenya--mombasa', 'kenya--kisumu', 'kenya--nakuru', 'kenya'):
            for page in range(1, 13):
                try:
                    r = cx.get(f'https://www.eventbrite.com/d/{city}/events/', params={'page': page})
                    found = list(dict.fromkeys(re.findall(
                        r'href="(https://www\.eventbrite\.com/e/[^"#?]+)', r.text)))
                    for u in found:
                        if u not in event_urls:
                            event_urls.append(u)
                except Exception:  # noqa: BLE001
                    continue
        print(f'[eventbrite] {len(event_urls)} event pages')
        import random
        random.shuffle(event_urls)
        for i, url in enumerate(event_urls[:240]):
            if i % 25 == 0:
                print(f'  event {i}/{min(len(event_urls), 150)}')
            try:
                pr = cx.get(url)
                body = re.sub(r'<[^>]+>', ' ', pr.text)
                title_m = re.search(r'<title>([^<@]+)</title>', pr.text)
                title = title_m.group(1).strip()[:70] if title_m else ''
                for e in extract_emails(body):
                    item = {'email': e, 'name': title, 'source': url[:100]}
                    if e not in candidates:
                        candidates[e] = item
            except Exception:  # noqa: BLE001
                continue
            time.sleep(0.35)

        # Phase C: event-planning job boards -> hiring event companies
        try:
            r = cx.get('https://www.brightermonday.co.ke/jobs/event-planning')
            job_links = list(dict.fromkeys(re.findall(
                r'href="(/jobs/[a-z0-9-]+)"', r.text)))[:35]
            print(f'[brightermonday] {len(job_links)} event-sector jobs')
            for j in job_links:
                try:
                    jr = cx.get('https://www.brightermonday.co.ke' + j)
                    body_j = jr.text
                    title_m = re.search(r'<h1[^>]*>([^<]{3,70})</h1>', body_j)
                    tname = title_m.group(1).strip() if title_m else ''
                    for e in extract_emails(body_j) + extract_emails(re.sub(r'<[^>]+>', ' ', body_j)):
                        candidates.setdefault(e, {'email': e, 'name': tname, 'source': 'brightermonday'})
                except Exception:  # noqa: BLE001
                    continue
                time.sleep(0.3)
        except Exception as ex:  # noqa: BLE001
            print('  bm skipped:', str(ex)[:60])

        # Phase D: crt.name CT subdomain discovery
        crt_subdomains: list[str] = []
        for apex in CRT_DOMAINS:
            try:
                r = cx.get('https://crt.name/v1/search', params={'apex': apex}, timeout=12)
                if r.status_code == 200:
                    for line in r.text.strip().splitlines():
                        sub = line.strip().lower()
                        if sub and sub != apex and '*' not in sub and sub not in crt_subdomains:
                            crt_subdomains.append(sub)
            except Exception:  # noqa: BLE001
                continue
            time.sleep(0.2)
        print(f'[crt.name] {len(crt_subdomains)} subdomains from {len(CRT_DOMAINS)} domains')
        for si, sub in enumerate(crt_subdomains[:200]):
            if len(candidates) >= max(needed * 6, 200):
                break
            for scheme in ('https', 'http'):
                try:
                    pr = cx.get(f'{scheme}://{sub}', timeout=10)
                    if pr.status_code < 400:
                        body = re.sub(r'<[^>]+>', ' ', pr.text)
                        for e in extract_emails(body):
                            candidates.setdefault(e, {'email': e, 'name': '', 'source': f'crt.name:{sub[:60]}'})
                        break
                except Exception:  # noqa: BLE001
                    continue
            if si % 40 == 0 and si:
                print(f'  crt.name crawled {si}/{min(len(crt_subdomains), 200)} — candidates: {len(candidates)}')
            time.sleep(0.25)

    items = list(candidates.values())
    print(f'collected {len(items)} candidate emails; pushing...')
    added = dupes = gated = 0
    for i in range(0, len(items), 25):
        chunk = items[i:i + 25]
        try:
            r = push(args.api, token, chunk)
            added += r['added']
            dupes += r['duplicates']
            gated += r['gated_low_relevance']
            print(f'  batch {i//25+1}: +{r["added"]} new, {r["duplicates"]} dupes, {r["gated_low_relevance"]} low-relevance')
        except Exception as ex:  # noqa: BLE001
            print('  batch failed:', str(ex)[:100])
    final = httpx.get(f'{args.api}/api/verify-smtp-stats',
                      headers={'X-Session-Token': token}, timeout=20).json()
    print(f'DONE. imported={added} dupes={dupes} gated={gated} | server now: {final.get("leads_total")} leads, {final.get("emails_with_address")} with email')


if __name__ == '__main__':
    main()
