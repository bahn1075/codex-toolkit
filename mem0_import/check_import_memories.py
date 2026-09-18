"""No-network self-check for the document importer."""
import json
import io
from pathlib import Path
import sys
import tempfile

from contextlib import redirect_stdout
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


class NoisyStore(Store):
    def add(self, fact, user_id, infer, metadata):
        print('HTTP Request: POST http://example "HTTP/1.1 200 OK"')
        print('Inserting 1 vectors into collection "CODEX_MEMORIES"')
        print('WARNING: provider degraded')
        return super().add(fact, user_id, infer, metadata)


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

    status = root / '.status'
    issues = root / '.issues'
    assert importer.main([
        str(root), '--dry-run', '--status-file', str(status), '--error-log', str(issues),
    ]) == 0
    events = [json.loads(line) for line in status.read_text().splitlines()]
    assert events[0] == {'event': 'start', 'total': 4}
    assert sum(event['event'] == 'file_complete' for event in events) == 4
    assert events[-1]['event'] == 'complete' and events[-1]['exit_code'] == 0
    rendered = io.StringIO()
    with redirect_stdout(rendered):
        assert importer.follow_status(status) == 0
    assert 'note.md' in rendered.getvalue() and '"errors": 0' in rendered.getvalue()

    noisy = NoisyStore()
    with patch('mem0_mcp.extract_facts', return_value=['fact']):
        importer.import_tree(root, store_factory=lambda: noisy, show_progress=False, error_log=issues)
    assert issues.read_text().splitlines() == ['WARNING: provider degraded'] * 4

print('PASS: recursive common formats, HTML filtering, redaction path, deterministic duplicate prevention.')
