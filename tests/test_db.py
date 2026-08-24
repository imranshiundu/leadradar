import pytest
from app.db import Database
from app.models import LeadCreate
from app.safety import fingerprint


@pytest.mark.asyncio
async def test_insert_dedupe(tmp_path):
    db = Database(str(tmp_path / 'test.db'))
    await db.init()
    fp = fingerprint('lead', 'url')
    lead = LeadCreate(source='test', source_url='https://example.com', name='Lead', fingerprint=fp)
    first_id, inserted = await db.insert_lead(lead)
    second_id, inserted_again = await db.insert_lead(lead)
    assert inserted is True
    assert inserted_again is False
    assert first_id == second_id
