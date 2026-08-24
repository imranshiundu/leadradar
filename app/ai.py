from __future__ import annotations

import json
import re
import httpx
from app.config import get_settings
from app.models import ClassificationResult
from app.safety import redact_personal_data, score_website_need, safe_outreach_message

SYSTEM_PROMPT = """You are LeadRadarSafe, a conservative lead and opportunity classifier.
Your job is to score public business/job/opportunity snippets for legitimate fit.
Rules:
- Never recommend spam or pressure tactics.
- Prefer manual review before outreach.
- Do not include private personal data in summaries.
- Return strict JSON only.
JSON schema: {"score":0-100,"category":"website_lead|job|service_opportunity|ignore","summary":"...","reason":"...","risk_flags":["..."],"recommended_action":"ignore|review|alert|approve_candidate","draft_message":"short polite outreach or application note"}
"""


def _json_from_text(text: str) -> dict:
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r'\{.*\}', text, flags=re.S)
        if match:
            return json.loads(match.group(0))
        raise


def load_profile() -> dict:
    settings = get_settings()
    try:
        import os
        if os.path.exists(settings.profile_path):
            with open(settings.profile_path, 'r') as f:
                return json.load(f)
    except Exception:
        pass
    return {}


async def classify_lead(payload: dict) -> ClassificationResult:
    settings = get_settings()
    profile = load_profile()
    
    redacted = {k: redact_personal_data(str(v)) if k in {'raw_text', 'source_url'} else v for k, v in payload.items()}
    
    profile_summary = ""
    if profile:
        profile_summary = f"\n\nUSER PROFILE (refer to this for drafts):\n{json.dumps(profile, indent=2)}"

    system_prompt_with_profile = SYSTEM_PROMPT + profile_summary
    fallback_score, fallback_reason = score_website_need(
        name=str(payload.get('name') or 'there'),
        website_url=payload.get('website_url'),
        social_url=payload.get('social_url'),
        raw_text=payload.get('raw_text'),
    )

    if not settings.ai_enabled or not settings.groq_api_key:
        name = str(payload.get('name') or 'there')
        draft = safe_outreach_message(name, 'your business may not have a dedicated website yet.' if not payload.get('website_url') else None)
        return ClassificationResult(
            score=fallback_score,
            category=payload.get('opportunity_type', 'website_lead'),
            summary=f'{name}: possible fit based on public snippet.',
            reason=fallback_reason,
            risk_flags=['ai_disabled_or_missing_key'],
            recommended_action='alert' if fallback_score >= 65 else 'review',
            draft_message=draft,
        )

    user_prompt = json.dumps(redacted, ensure_ascii=False)[:6000]
    async with httpx.AsyncClient(timeout=25) as client:
        resp = await client.post(
            'https://api.groq.com/openai/v1/chat/completions',
            headers={'Authorization': f'Bearer {settings.groq_api_key}', 'Content-Type': 'application/json'},
            json={
                'model': settings.groq_model,
                'temperature': 0.2,
                'response_format': {'type': 'json_object'},
                'messages': [
                    {'role': 'system', 'content': system_prompt_with_profile},
                    {'role': 'user', 'content': user_prompt},
                ],
            },
        )
        resp.raise_for_status()
        content = resp.json()['choices'][0]['message']['content']
        data = _json_from_text(content)

    if not data.get('draft_message') and payload.get('opportunity_type') == 'website_lead':
        data['draft_message'] = safe_outreach_message(str(payload.get('name') or 'there'))
    if data.get('score', 0) < fallback_score and payload.get('opportunity_type') == 'website_lead':
        data['score'] = max(data.get('score', 0), fallback_score)
        data['reason'] = (data.get('reason') or '') + f' Fallback signal: {fallback_reason}.'
    return ClassificationResult(**data)
