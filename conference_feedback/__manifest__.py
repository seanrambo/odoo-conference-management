{
    'name': 'Conference Feedback and Certificates',
    'version': '19.0.1.0.0',
    'category': 'Marketing/Events',
    'summary': 'Session ratings, speaker scorecards, post-event surveys, attendance '
               'certificates with CPD points, and an event report',
    'description': """
Conference Feedback and Certificates (Phase 4)
==============================================

* Session ratings: after a session ends, the people scanned into it are emailed
  a link to rate it from 1 to 5 and comment. Emails are grouped, so an attendee
  gets at most one every few hours.
* Speaker scorecards: attendance and ratings per session and per speaker, and
  an email to each speaker with their results and comments.
* Event survey: pick one of your surveys; each attendee gets a personal link a
  set time after the event ends.
* Certificates of attendance: a PDF with the attendee's CPD points, a number and
  a QR code that opens an online verification page. Rules per event (minimum
  sessions or points, survey completed), issued automatically or in one click.
* Event report: registrations over time, revenue by ticket, check-in rate,
  session attendance and ratings, speakers, survey and certificate numbers.
""",
    'author': 'R4M80',
    'license': 'LGPL-3',
    'depends': ['conference_onsite', 'survey', 'event_sale'],
    'data': [
        'security/security.xml',
        'security/ir.model.access.csv',
        'data/ir_sequence_data.xml',
        'report/certificate_report.xml',
        'data/mail_template_data.xml',
        'data/ir_cron_data.xml',
        'views/conference_session_feedback_views.xml',
        'views/conference_certificate_views.xml',
        'views/event_event_views.xml',
        'views/event_track_views.xml',
        'views/event_registration_views.xml',
        'views/menus.xml',
        'views/website_templates.xml',
    ],
    'assets': {
        'web.assets_backend': [
            'conference_feedback/static/src/event_report/*',
        ],
        'web.assets_web_dark': [
            'conference_feedback/static/src/dark/event_report.dark.scss',
        ],
        'web.assets_tests': [
            'conference_feedback/static/tests/tours/*',
        ],
    },
    'installable': True,
}
