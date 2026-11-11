from datetime import timedelta

from odoo import fields, http
from odoo.tests import HttpCase, new_test_user, tagged


@tagged('post_install', '-at_install', 'conference')
class TestConferencePortal(HttpCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.author_user = new_test_user(
            cls.env, login='cm_portal_author', password='cm_portal_author',
            groups='base.group_portal', email='portal.author@example.com')
        now = fields.Datetime.now()
        cls.event = cls.env['event.event'].create({
            'name': 'Portal Summit',
            'date_begin': now + timedelta(days=20),
            'date_end': now + timedelta(days=21),
            'website_published': True,
            'website_menu': True,
            'cfp_enabled': True,
            'cfp_deadline': now + timedelta(days=5),
            'doc_allowed_extensions': 'pdf',
        })

    def _post(self, url, data, files=None):
        data = dict(data, csrf_token=http.Request.csrf_token(self))
        return self.url_open(url, data=data, files=files, allow_redirects=True)

    def test_public_pages(self):
        self.assertTrue(self.event.cfp_menu_ids, "A Call for Papers menu entry is created")
        self.assertTrue(self.event.materials_menu_ids, "A Materials menu entry is created")
        res = self.url_open('/event/%s/call-for-papers' % self.event.id)
        self.assertEqual(res.status_code, 200)
        self.assertIn('Submit a proposal', res.text)
        res = self.url_open('/event/%s/materials' % self.event.id)
        self.assertEqual(res.status_code, 200)

    def test_submit_and_upload_from_portal(self):
        self.authenticate('cm_portal_author', 'cm_portal_author')
        for url in ('/my', '/my/submissions', '/my/submissions/new', '/my/conference/documents'):
            res = self.url_open(url)
            self.assertEqual(res.status_code, 200, url)

        res = self._post('/my/submissions/new', {
            'event_id': self.event.id,
            'name': 'Edge AI in agriculture',
            'abstract': 'Running models on cheap phones in the field.',
            'submission_type': 'talk',
            'duration': '0.5',
            'action': 'submit',
            'document_type': 'paper',
        }, files={'file': ('paper.pdf', b'%PDF-1.4 paper', 'application/pdf')})
        self.assertEqual(res.status_code, 200)
        sub = self.env['conference.submission'].search([('partner_id', '=', self.author_user.partner_id.id)])
        self.assertEqual(len(sub), 1)
        self.assertEqual(sub.state, 'submitted')
        self.assertEqual(sub.document_ids.filename, 'paper.pdf')
        self.assertEqual(sub.document_ids.visibility, 'organizers')

        res = self.url_open('/my/submissions/%s' % sub.id)
        self.assertEqual(res.status_code, 200)
        self.assertIn('Edge AI in agriculture', res.text)

        # A wrong file type is refused with a message, not a crash.
        res = self._post('/my/submissions/%s/upload' % sub.id, {'document_type': 'paper'},
                         files={'file': ('virus.exe', b'MZ', 'application/octet-stream')})
        self.assertEqual(res.status_code, 200)
        self.assertEqual(len(sub.document_ids), 1)

        # Downloading own file works; another portal user cannot.
        doc = sub.document_ids
        res = self.url_open(doc._get_download_url())
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.content, b'%PDF-1.4 paper')
        new_test_user(self.env, login='cm_other', password='cm_other', groups='base.group_portal')
        self.authenticate('cm_other', 'cm_other')
        res = self.url_open(doc._get_download_url())
        self.assertEqual(res.status_code, 404)
        res = self.url_open('/my/submissions/%s' % sub.id, allow_redirects=False)
        self.assertIn(res.status_code, (302, 303))

    def test_closed_cfp_refuses_submission(self):
        self.event.cfp_deadline = fields.Datetime.now() - timedelta(minutes=1)
        self.authenticate('cm_portal_author', 'cm_portal_author')
        self._post('/my/submissions/new', {
            'event_id': self.event.id, 'name': 'Late', 'abstract': 'Too late.', 'action': 'submit',
        })
        self.assertFalse(self.env['conference.submission'].search([('name', '=', 'Late')]))
