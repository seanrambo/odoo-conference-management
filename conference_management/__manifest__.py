{
    'name': 'Conference Management',
    'version': '19.0.1.0.0',
    'category': 'Marketing/Events',
    'summary': 'Call for papers, peer review, speaker and attendee file uploads, '
               'and per-room presentation packs for conferences',
    'description': """
Conference Management (Phase 1)
===============================

* Call for papers: authors submit abstracts and files from their portal.
* Peer review: reviewer assignment, blind review, scoring rubric and decisions by email.
* Accepted submissions become scheduled event talks with the speaker linked.
* Documents: organizers, speakers, authors and attendees upload files with visibility, approval and version history.
* AV pack: one ZIP of approved slides per room and day, named by start time.
* Materials page on the event website for attendees.
""",
    'author': 'R4M80',
    'license': 'LGPL-3',
    'depends': ['website_event_track', 'portal', 'mail'],
    'data': [
        'security/security.xml',
        'security/ir.model.access.csv',
        'data/ir_sequence_data.xml',
        'data/mail_template_data.xml',
        'views/conference_submission_views.xml',
        'views/conference_review_views.xml',
        'views/conference_document_views.xml',
        'views/event_event_views.xml',
        'wizard/conference_av_pack_views.xml',
        'views/menus.xml',
        'views/portal_templates.xml',
        'views/website_templates.xml',
    ],
    'application': True,
    'installable': True,
}
