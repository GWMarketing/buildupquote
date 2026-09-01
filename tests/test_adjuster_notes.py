# End-to-end tests for adjuster notes: the parser captures trailing remarks
# (document_notes) and per-line notes, the workspace carries them into the
# row contract, the from-parse flow persists them onto the quote, and the
# exports can include or exclude them.
#
# Runs inside `unittest discover -s tests` next to test_crm_api.py: sets
# DATABASE_URL/SECRET_KEY before importing the app, so nothing touches the
# real postgres database.
import os
import tempfile
import unittest

_DB = os.path.join(tempfile.gettempdir(), 'test_adjuster_notes.db')
os.environ['DATABASE_URL'] = 'sqlite:///' + _DB
os.environ['SECRET_KEY'] = 'test-secret-key'
for suffix in ('', '-journal', '-wal', '-shm'):
    p = _DB + suffix
    if os.path.exists(p):
        os.remove(p)

from fastapi.testclient import TestClient  # noqa: E402
import fastapi_app  # noqa: E402
from scope_parser.pipeline import parse_text  # noqa: E402
import workspace  # noqa: E402
from proposal.build import build_proposal  # noqa: E402
from proposal.models import ContractorInfo  # noqa: E402
REMARKS_TEXT = chr(10).join([
    '1. Remove and replace shingles',
    '10.00 SQ 2.00 20.00 0.00',
    '2. Install new drip edge',
    '20.00 LF 1.50 30.00 0.00',
    'Total: 50.00',
    'Remarks:',
    'No visible sudden and accidental storm related damage found during inspection.',
    'The roof replacement shall include the removal of all existing layers.',
])


class ParserNotesTests(unittest.TestCase):
    def test_document_notes_capture_trailing_remarks(self):
        estimate = parse_text(REMARKS_TEXT)
        self.assertEqual(len(estimate.line_items), 2)
        self.assertEqual(
            estimate.document_notes,
            [
                'No visible sudden and accidental storm related damage found during inspection.',
                'The roof replacement shall include the removal of all existing layers.',
            ],
        )

    def test_rows_carry_per_line_notes(self):
        text = chr(10).join([
            '1. Remove and replace shingles',
            '10.00 SQ 2.00 20.00 0.00',
            'Roofing labor required.',
            '2. Install new drip edge',
            '20.00 LF 1.50 30.00 0.00',
            'Total: 50.00',
        ])
        estimate = parse_text(text)
        rows = workspace._rows_from_estimate(estimate, default_margin=20)
        self.assertIn('Notes', rows.columns)
        self.assertIn('Roofing labor required.', rows.iloc[0]['Notes'])


class NotesApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(fastapi_app.app)
        cls.client.__enter__()

    @classmethod
    def tearDownClass(cls):
        cls.client.__exit__(None, None, None)

    def register(self, email):
        r = self.client.post('/api/auth/register', json={
            'email': email, 'password': 'pw12345678', 'organization_name': 'Acme Roofing',
        })
        self.assertEqual(r.status_code, 201, r.text)
        return {'Authorization': 'Bearer ' + r.json()['access_token']}

    def test_from_parse_persists_notes_and_remarks(self):
        auth = self.register('notes1@acme.com')
        r = self.client.post('/api/quotes/from-parse', headers=auth, json={
            'rows': [{
                'Include': True, 'Trade': 'Roofing', 'Section': 'Roof1',
                'Description': 'Remove and replace shingles', 'Qty': 10, 'Unit': 'SQ',
                'Unit Cost': 2.0, 'Margin %': 20, 'Material': True,
                'Insurance RCV': 20.0, 'Needs Review': False, 'Review Note': '',
                'Notes': 'Roofing labor required.',
            }],
            'claim_fields': {'insured_name': 'Jane Smith'},
            'notes': ['No visible sudden and accidental storm related damage found during inspection.'],
        })
        self.assertEqual(r.status_code, 201, r.text)
        qid = r.json()['id']
        detail = self.client.get(f'/api/quotes/{qid}', headers=auth).json()
        self.assertEqual(
            detail['adjuster_notes'],
            'No visible sudden and accidental storm related damage found during inspection.',
        )
        self.assertTrue(detail['include_adjuster_notes'])
        self.assertEqual(detail['lines'][0]['notes'], 'Roofing labor required.')

    def test_save_lines_preserves_notes(self):
        auth = self.register('notes2@acme.com')
        qid = self.client.post('/api/quotes', headers=auth, json={'title': 'Q'}).json()['id']
        r = self.client.put(f'/api/quotes/{qid}/lines', headers=auth, json=[{
            'description': 'Shingles', 'item_type': 'material', 'quantity': 10,
            'unit': 'SQ', 'unit_cost': 2.0, 'markup_percent': 20,
            'notes': 'Waste is factored in, then rounded up',
        }])
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()['lines'][0]['notes'], 'Waste is factored in, then rounded up')

    def test_patch_adjuster_notes_and_toggle(self):
        auth = self.register('notes3@acme.com')
        qid = self.client.post('/api/quotes', headers=auth, json={'title': 'Q'}).json()['id']
        r = self.client.patch(f'/api/quotes/{qid}', headers=auth, json={
            'adjuster_notes': 'Thank you for insuring with us.',
            'include_adjuster_notes': False,
        })
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()['adjuster_notes'], 'Thank you for insuring with us.')
        self.assertFalse(r.json()['include_adjuster_notes'])


    def test_export_pdf_includes_adjuster_notes(self):
        auth = self.register('notes4@acme.com')
        r = self.client.post('/api/quotes/from-parse', headers=auth, json={
            'rows': [{
                'Include': True, 'Trade': 'Roofing', 'Section': 'Roof1',
                'Description': 'Shingles', 'Qty': 10, 'Unit': 'SQ',
                'Unit Cost': 2.0, 'Margin %': 20, 'Material': True,
                'Insurance RCV': 20.0, 'Needs Review': False, 'Review Note': '',
                'Notes': 'Roofing labor required.',
            }],
            'claim_fields': {},
            'notes': ['No visible storm damage found.'],
        })
        self.assertEqual(r.status_code, 201, r.text)
        qid = r.json()['id']
        pdf = self.client.get(f'/api/quotes/{qid}/export-pdf', headers=auth)
        self.assertEqual(pdf.status_code, 200, pdf.text[:200])
        import io
        import pdfplumber
        text = ''
        with pdfplumber.open(io.BytesIO(pdf.content)) as doc:
            for page in doc.pages:
                text += page.extract_text() or ''
        self.assertIn('Roofing labor required.', text)
        self.assertIn('No visible storm damage found.', text)


class NotesExportTests(unittest.TestCase):
    def _rows(self, include_notes=True):
        rows = [{
            'Include': True, 'Trade': 'Roofing', 'Section': 'Roof1',
            'Description': 'Shingles', 'Qty': 10, 'Unit': 'SQ',
            'Unit Cost': 2.0, 'Margin %': 20, 'Material': True,
            'Notes': 'Roofing labor required.', 'Review Note': '',
            'Insurance RCV': 20.0, 'Recoverable Depreciation': 0.0,
        }]
        if not include_notes:
            rows = [{**r, 'Notes': ''} for r in rows]
        return rows

    def test_proposal_carries_line_note_and_remarks(self):
        contractor = ContractorInfo(name='Acme Roofing')
        data = build_proposal(
            self._rows(), contractor, {'insured_name': 'Jane'}, '01/01/2026',
            remarks='No visible storm damage found.',
        )
        self.assertEqual(data.grouped_items[0].items[0].note, 'Roofing labor required.')
        self.assertEqual(data.remarks, 'No visible storm damage found.')

    def test_proposal_omits_notes_when_not_sent(self):
        contractor = ContractorInfo(name='Acme Roofing')
        data = build_proposal(
            self._rows(include_notes=False), contractor, {'insured_name': 'Jane'},
            '01/01/2026', remarks='',
        )
        self.assertEqual(data.grouped_items[0].items[0].note, '')
        self.assertEqual(data.remarks, '')

    def test_csv_includes_notes_by_default_and_drops_when_toggled_off(self):
        client = TestClient(fastapi_app.app)
        with client:
            with_notes = client.post('/api/csv', json={
                'rows': self._rows(), 'business': {'name': 'Acme Roofing'},
                'claim_fields': {}, 'include_notes': True,
            })
            self.assertEqual(with_notes.status_code, 200, with_notes.text)
            self.assertIn('Roofing labor required.', with_notes.text)
            without = client.post('/api/csv', json={
                'rows': self._rows(include_notes=False), 'business': {'name': 'Acme Roofing'},
                'claim_fields': {}, 'include_notes': False,
            })
            self.assertEqual(without.status_code, 200, without.text)
            self.assertNotIn('Roofing labor required.', without.text)


if __name__ == '__main__':
    unittest.main()
