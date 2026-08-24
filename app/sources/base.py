from __future__ import annotations

import json
from pathlib import Path
from app.config import get_settings


def load_sources_config() -> dict:
    path = Path(get_settings().sources_path)
    if not path.exists():
        raise FileNotFoundError(f'Sources config not found: {path}')
    return json.loads(path.read_text(encoding='utf-8'))
