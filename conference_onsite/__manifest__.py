{
    'name': 'Conference Event Day',
    'version': '19.0.1.0.0',
    'category': 'Marketing/Events',
    'summary': 'Session attendance scanning, CPD points, live check-in numbers '
               'and automatic emails when a session moves or is cancelled',
    'description': """
Conference Event Day (Phase 3)
==============================

* Session scanner: door staff pick a session and scan badges with a phone
  camera or a USB scanner. Each scan records that the attendee was in the room.
* Room capacity per session, a scan window around the session time, and
  clear on-screen results (recorded, already in, full, wrong event, cancelled).
* CPD points per session, totalled on each attendee's registration.
* Live numbers on the event: checked in, still to arrive, session attendance.
* When a session's time or room changes, or it is cancelled, the people who
  saved it to their agenda (and its speaker) are emailed what changed.

Main check-in at the entrance uses the standard Registration Desk screen.
""",
    'author': 'R4M80',
    'license': 'LGPL-3',
    'depends': ['conference_management'],
    'data': [
        'security/security.xml',
        'security/ir.model.access.csv',
        'data/mail_template_data.xml',
        'views/conference_session_attendance_views.xml',
        'views/event_track_views.xml',
        'views/event_event_views.xml',
        'views/event_registration_views.xml',
        'views/menus.xml',
    ],
    'assets': {
        'web.assets_backend': [
            'conference_onsite/static/src/session_scanner/*',
        ],
        'web.assets_tests': [
            'conference_onsite/static/tests/tours/*',
        ],
    },
    'installable': True,
}
