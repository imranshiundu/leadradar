from app.safety import extract_emails, fingerprint, score_website_need, redact_personal_data


def test_extract_emails():
    assert extract_emails('Contact hello@example.com and HELLO@example.com') == ['hello@example.com']


def test_fingerprint_stable():
    assert fingerprint(' Test ', 'A') == fingerprint('test', 'a')


def test_redaction():
    text = redact_personal_data('Email me at a@test.com or +254 700 111 222')
    assert '[email-redacted]' in text
    assert '[phone-redacted]' in text


def test_score_website_need_no_site():
    score, reason = score_website_need('Shop', None, 'https://instagram.com/shop', 'DM us for booking')
    assert score >= 80
    assert 'no standalone website' in reason
