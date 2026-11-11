import base64
import io
import zipfile
from datetime import datetime, timedelta

from odoo import fields
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tests import TransactionCase, new_test_user, tagged


def b64(data):
    return base64.b64encode(data)


@tagged('post_install', '-at_install', 'conference')
class ConferenceCase(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env = cls.env(context=dict(cls.env.context, mail_create_nolog=True, tracking_disable=True))
        cls.organizer = new_test_user(
            cls.env, login='cm_organizer', groups='event.group_event_user', email='org@example.com')
        cls.reviewer_a = new_test_user(
            cls.env, login='cm_rev_a', groups='conference_management.group_conference_reviewer',
            email='reva@example.com')
        cls.reviewer_b = new_test_user(
            cls.env, login='cm_rev_b', groups='conference_management.group_conference_reviewer',
            email='revb@example.com')
        cls.reviewer_c = new_test_user(
            cls.env, login='cm_rev_c', groups='conference_management.group_conference_reviewer',
            email='revc@example.com')
        cls.author_user = new_test_user(
            cls.env, login='cm_author', groups='base.group_portal', email='author@example.com',
            name='Wanjiru Author')
        cls.attendee_user = new_test_user(
            cls.env, login='cm_attendee', groups='base.group_portal', email='attendee@example.com')
        cls.outsider_user = new_test_user(
            cls.env, login='cm_outsider', groups='base.group_portal', email='outsider@example.com')
        cls.author = cls.author_user.partner_id

        now = fields.Datetime.now()
        cls.event = cls.env['event.event'].create({
            'name': 'Nairobi Tech Summit',
            'date_begin': now + timedelta(days=30),
            'date_end': now + timedelta(days=32),
            'date_tz': 'Africa/Nairobi',
            'cfp_enabled': True,
            'cfp_deadline': now + timedelta(days=10),
            'cfp_min_reviews': 2,
            'cfp_reviewer_ids': [(6, 0, (cls.reviewer_a | cls.reviewer_b | cls.reviewer_c).ids)],
            'doc_max_size_mb': 1,
            'doc_allowed_extensions': 'pdf,pptx',
        })
        cls.room = cls.env['event.track.location'].create({'name': 'Hall A'})
        cls.env['event.registration'].create({
            'event_id': cls.event.id, 'partner_id': cls.attendee_user.partner_id.id,
            'name': 'Attendee', 'email': 'attendee@example.com',
        })

    def _submission(self, **vals):
        return self.env['conference.submission'].create(dict({
            'name': 'Mobile money for SMEs',
            'event_id': self.event.id,
            'partner_id': self.author.id,
            'abstract': 'How small businesses use M-Pesa.',
        }, **vals))

    def _doc(self, **vals):
        return self.env['conference.document'].create(dict({
            'name': 'Slides',
            'event_id': self.event.id,
            'file': b64(b'%PDF-1.4 slides'),
            'filename': 'slides.pdf',
        }, **vals))


class TestSubmissionWorkflow(ConferenceCase):

    def test_reference_and_submit(self):
        sub = self._submission()
        self.assertTrue(sub.reference.startswith('CFP'))
        self.assertEqual(sub.state, 'draft')
        sub.action_submit()
        self.assertEqual(sub.state, 'submitted')
        self.assertTrue(sub.submit_date)
        self.assertTrue(sub.message_ids.filtered(lambda m: 'Submission received' in (m.subject or '')))

    def test_submit_requires_abstract(self):
        sub = self._submission(abstract=' ')
        with self.assertRaises(UserError):
            sub.action_submit()

    def test_auto_assign_skips_author_and_balances_load(self):
        # The author is also in the reviewer pool: they must never review themselves.
        self.event.cfp_reviewer_ids |= self.organizer
        sub1 = self._submission(partner_id=self.organizer.partner_id.id)
        sub1.action_submit()
        sub1.action_auto_assign_reviewers()
        self.assertEqual(len(sub1.review_ids), 2)
        self.assertNotIn(self.organizer, sub1.review_ids.reviewer_id)
        self.assertEqual(sub1.state, 'under_review')

        sub2 = self._submission()
        sub2.action_submit()
        sub2.action_auto_assign_reviewers()
        # The reviewer left out of sub1 is now the least loaded and must be picked.
        idle = (self.reviewer_a | self.reviewer_b | self.reviewer_c) - sub1.review_ids.reviewer_id
        self.assertIn(idle, sub2.review_ids.reviewer_id)
        # Running it again does not add more reviewers.
        sub2.action_auto_assign_reviewers()
        self.assertEqual(len(sub2.review_ids), 2)

    def test_author_cannot_review_own(self):
        reviewer_author = self.reviewer_a.partner_id
        sub = self._submission(partner_id=reviewer_author.id)
        sub.action_submit()
        with self.assertRaises(UserError):
            self.env['conference.review'].create({'submission_id': sub.id, 'reviewer_id': self.reviewer_a.id})

    def test_review_scoring_and_accept(self):
        sub = self._submission()
        sub.action_submit()
        doc = self._doc(submission_id=sub.id, document_type='paper')
        review_a, review_b = self.env['conference.review'].create([
            {'submission_id': sub.id, 'reviewer_id': self.reviewer_a.id},
            {'submission_id': sub.id, 'reviewer_id': self.reviewer_b.id},
        ])
        self.assertTrue(review_a.activity_ids, "The reviewer gets a to-do activity")

        as_a = review_a.with_user(self.reviewer_a)
        as_a.write({'score_originality': '5', 'score_relevance': '4', 'score_clarity': '4',
                    'score_quality': '3', 'recommendation': 'accept',
                    'comment_author': 'Clear and useful.'})
        as_a.action_submit_review()
        self.assertEqual(review_a.score, 4.0)
        self.assertEqual(review_a.state, 'done')
        self.assertFalse(review_a.activity_ids)

        review_b.write({'score_originality': '2', 'score_relevance': '2', 'score_clarity': '2',
                        'score_quality': '2', 'recommendation': 'weak_reject'})
        review_b.action_submit_review()
        self.assertEqual(sub.average_score, 3.0)
        self.assertEqual(sub.review_done_count, 2)
        self.assertEqual(sub._get_author_feedback(), ['Clear and useful.'])

        sub.action_accept()
        self.assertEqual(sub.state, 'accepted')
        self.assertTrue(sub.track_id)
        self.assertEqual(sub.track_id.partner_id, self.author)
        self.assertEqual(sub.track_id.event_id, self.event)
        self.assertEqual(doc.track_id, sub.track_id, "Submission files follow the new session")

    def test_review_needs_all_scores(self):
        sub = self._submission()
        sub.action_submit()
        review = self.env['conference.review'].create({'submission_id': sub.id, 'reviewer_id': self.reviewer_a.id})
        review.write({'score_originality': '4'})
        with self.assertRaises(UserError):
            review.action_submit_review()

    def test_revision_round_trip(self):
        sub = self._submission()
        sub.action_submit()
        review = self.env['conference.review'].create({'submission_id': sub.id, 'reviewer_id': self.reviewer_a.id})
        review.write({'score_originality': '3', 'score_relevance': '3', 'score_clarity': '3',
                      'score_quality': '3', 'recommendation': 'weak_accept'})
        review.action_submit_review()
        sub.action_request_revision()
        self.assertEqual(sub.state, 'revision')
        sub.abstract = 'Revised abstract.'
        sub.action_submit()
        self.assertEqual(sub.state, 'under_review')
        self.assertEqual(review.state, 'assigned', "Reviewers look at the revised version again")

    def test_cannot_decide_draft(self):
        sub = self._submission()
        with self.assertRaises(UserError):
            sub.action_accept()

    def test_withdraw(self):
        sub = self._submission()
        sub.action_submit()
        sub.action_withdraw()
        self.assertEqual(sub.state, 'withdrawn')
        with self.assertRaises(UserError):
            sub.action_withdraw()


class TestReviewerAccess(ConferenceCase):

    def test_reviewer_isolation_and_blind_review(self):
        sub = self._submission()
        sub.action_submit()
        doc = self._doc(submission_id=sub.id, document_type='paper', partner_id=self.author.id)
        review_a, review_b = self.env['conference.review'].create([
            {'submission_id': sub.id, 'reviewer_id': self.reviewer_a.id},
            {'submission_id': sub.id, 'reviewer_id': self.reviewer_b.id},
        ])
        Review = self.env['conference.review'].with_user(self.reviewer_a)
        self.assertEqual(Review.search([]), review_a, "Reviewers only see their own reviews")

        as_a = review_a.with_user(self.reviewer_a)
        self.assertEqual(as_a.submission_name, sub.name)
        self.assertEqual(as_a.submission_abstract, sub.abstract)
        self.assertEqual(as_a.author_display, 'Hidden (blind review)')
        self.assertEqual(as_a.submission_document_ids, doc)
        self.assertTrue(doc.with_user(self.reviewer_a).file, "Reviewers can open the paper")

        with self.assertRaises(AccessError):
            sub.with_user(self.reviewer_a).read(['partner_id'])
        with self.assertRaises(AccessError):
            doc.with_user(self.reviewer_a).read(['partner_id'])
        with self.assertRaises(AccessError):
            review_b.with_user(self.reviewer_a).read(['score'])

        # Organizers always see the author.
        self.assertEqual(review_a.with_user(self.organizer).author_display, self.author.display_name)
        self.event.cfp_blind_review = False
        review_a.invalidate_recordset(['author_display'])
        self.assertEqual(as_a.author_display, self.author.display_name)

    def test_reviewer_cannot_edit_closed_or_foreign_fields(self):
        sub = self._submission()
        sub.action_submit()
        review = self.env['conference.review'].create({'submission_id': sub.id, 'reviewer_id': self.reviewer_a.id})
        as_a = review.with_user(self.reviewer_a)
        with self.assertRaises(AccessError):
            as_a.write({'reviewer_id': self.reviewer_b.id})
        as_a.write({'score_originality': '4', 'score_relevance': '4', 'score_clarity': '4',
                    'score_quality': '4', 'recommendation': 'accept'})
        as_a.action_submit_review()
        with self.assertRaises(UserError):
            as_a.write({'score_quality': '1'})


class TestDocuments(ConferenceCase):

    def test_extension_and_size_rules(self):
        with self.assertRaises(ValidationError):
            self._doc(filename='malware.exe')
        with self.assertRaises(ValidationError):
            self._doc(file=b64(b'x' * (1024 * 1024 + 1)), filename='big.pdf')
        doc = self._doc(filename='Talk.PPTX')
        self.assertEqual(doc.file_size, len(b'%PDF-1.4 slides'))

    def test_replace_keeps_history(self):
        doc = self._doc(partner_id=self.author.id)
        doc.action_approve()
        doc.with_user(self.author_user).sudo().write({'file': b64(b'%PDF-1.4 v2'), 'filename': 'slides-v2.pdf'})
        self.assertEqual(doc.version, 2)
        self.assertEqual(doc.state, 'pending', "A replaced file needs approval again")
        old = self.env['ir.attachment'].search([
            ('res_model', '=', 'conference.document'), ('res_id', '=', doc.id), ('name', '=like', 'v1 - %')])
        self.assertEqual(len(old), 1)
        self.assertEqual(base64.b64decode(old.datas), b'%PDF-1.4 slides')

        # An organizer replacing a file keeps it approved.
        doc.action_approve()
        doc.with_user(self.organizer).write({'file': b64(b'%PDF-1.4 v3'), 'filename': 'slides-v3.pdf'})
        self.assertEqual((doc.version, doc.state), (3, 'approved'))

    def test_reject_needs_reason(self):
        doc = self._doc(partner_id=self.author.id)
        with self.assertRaises(UserError):
            doc.action_reject()
        doc.rejection_reason = 'Use the 16:9 template.'
        doc.action_reject()
        self.assertEqual(doc.state, 'rejected')

    def test_download_permissions(self):
        public = self.env.ref('base.public_user')
        doc = self._doc(partner_id=self.author.id, visibility='attendees')
        # Pending: only the owner and organizers.
        self.assertTrue(doc._can_be_downloaded_by(self.author_user))
        self.assertTrue(doc._can_be_downloaded_by(self.organizer))
        self.assertFalse(doc._can_be_downloaded_by(self.attendee_user))

        doc.action_approve()
        self.assertTrue(doc._can_be_downloaded_by(self.attendee_user))
        self.assertFalse(doc._can_be_downloaded_by(self.outsider_user))
        self.assertFalse(doc._can_be_downloaded_by(public))

        doc.visibility = 'public'
        self.assertTrue(doc._can_be_downloaded_by(public))
        doc.visibility = 'organizers'
        self.assertFalse(doc._can_be_downloaded_by(self.attendee_user))
        doc.visibility = 'speakers'
        self.assertFalse(doc._can_be_downloaded_by(self.attendee_user))

    def test_upload_eligibility(self):
        self.assertFalse(self.event._conference_partner_can_upload(self.attendee_user.partner_id))
        self.event.doc_attendee_upload = True
        self.assertTrue(self.event._conference_partner_can_upload(self.attendee_user.partner_id))
        self.assertFalse(self.event._conference_partner_can_upload(self.outsider_user.partner_id))
        self.event.doc_upload_deadline = fields.Datetime.now() - timedelta(hours=1)
        self.assertFalse(self.event._conference_partner_can_upload(self.attendee_user.partner_id))


class TestAvPack(ConferenceCase):

    def test_zip_by_room_and_time(self):
        speaker = self.env['res.partner'].create({'name': 'J Kamau'})
        track = self.env['event.track'].create({
            'name': 'Opening keynote', 'event_id': self.event.id, 'partner_id': speaker.id,
            'location_id': self.room.id,
            # 06:00 UTC = 09:00 in Nairobi
            'date': datetime.combine(self.event.date_begin.date(), datetime.min.time()) + timedelta(hours=6),
            'duration': 1,
        })
        approved = self._doc(track_id=track.id, filename='keynote.pptx')
        approved.action_approve()
        self._doc(track_id=track.id, filename='draft.pdf', name='Draft')  # pending: excluded
        self._doc(filename='handout.pdf', document_type='handout').action_approve()  # not slides

        wizard = self.env['conference.av.pack'].create({'event_id': self.event.id})
        self.assertEqual(wizard.document_count, 1)
        content, filename = wizard._build_zip()
        self.assertTrue(filename.endswith('.zip'))
        names = zipfile.ZipFile(io.BytesIO(content)).namelist()
        day = self.event.date_begin.date().isoformat()
        self.assertEqual(names, ['Hall-A/%s/0900_Hall-A_J-Kamau_Opening-keynote.pptx' % day])

        empty = self.env['conference.av.pack'].create({
            'event_id': self.event.id, 'location_ids': [(6, 0, self.env['event.track.location'].create({'name': 'B'}).ids)]})
        with self.assertRaises(UserError):
            empty._build_zip()
