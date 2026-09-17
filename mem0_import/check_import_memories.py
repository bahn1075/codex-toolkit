"""No-network self-check for the document importer."""
import json
from pathlib import Path
import sys
import tempfile

from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parent))
import import_memories as importer


class Store:
    def __init__(self):
        self.rows = {}

    def get_all(self, filters):
        return {'results': [self.rows[filters['toolkit_fact_id']]]} if filters['toolkit_fact_id'] in self.rows else {'results': []}

    def add(self, fact, user_id, infer, metadata):
        assert user_id == 'codex' and infer is False
        self.rows[metadata['toolkit_fact_id']] = fact
        return {'results': [{'id': metadata['toolkit_fact_id']}]}


with tempfile.TemporaryDirectory() as tmp:
    root = Path(tmp)
    (root / 'nested').mkdir()
    (root / 'note.md').write_text('Oracle\n\nUse Python.')
    (root / 'nested/page.html').write_text('<h1>Title</h1><script>ignore()</script><p>HTML fact.</p>')
    (root / 'data.json').write_text(json.dumps({'fact': 'JSON fact'}, ensure_ascii=False))
    (root / 'events.jsonl').write_text('{"fact":"JSONL fact"}\n')
    (root / 'ignored.bin').write_bytes(b'ignored')
    store = Store()
    with patch.object(importer, 'MAX_CHUNK', 100), patch('mem0_mcp.extract_facts', side_effect=lambda s, messages: [messages[0]['content'].split('Source: ', 1)[1].split('\n\n', 1)[1]]):
        first = importer.import_tree(root, store_factory=lambda: store, show_progress=False)
        second = importer.import_tree(root, store_factory=lambda: store, show_progress=False)
    assert first['files'] == 4 and first['inserted'] == 4 and first['errors'] == 0
    assert second['inserted'] == 0 and second['skipped'] == 4
    assert 'ignore()' not in next(iter(store.rows.values()))

print('PASS: recursive common formats, HTML filtering, redaction path, deterministic duplicate prevention.')
