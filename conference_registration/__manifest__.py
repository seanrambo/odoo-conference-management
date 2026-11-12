{
    'name': 'Conference Registration',
    'version': '19.0.1.0.0',
    'category': 'Marketing/Events',
    'summary': 'Waitlists, group bookings, visa invitation letters, ticket transfers '
               'and self-service cancellations for conferences',
    'description': """
Conference Registration (Phase 2)
=================================

* Waitlist: sold-out events take waitlist sign-ups. Freed seats are held
  for the next person and offered by email; paid seats are paid online.
* Group bookings: register a company's attendees in one go, with one
  sales order and one invoice.
* Visa invitation letters: attendees request a letter, organisers approve
  it, and embassies can verify it online.
* Ticket transfers and self-service cancellations, within deadlines you set.
* A "manage your booking" page and email for every attendee.

Online payment works with any payment provider; install payment_mpesa for M-Pesa.
""",
    'author': 'R4M80',
    'license': 'LGPL-3',
    'depends': ['conference_management', 'event_sale'],
    'data': [
        'security/ir.model.access.csv',
        'security/security.xml',
        'data/ir_sequence_data.xml',
        'report/visa_letter_report.xml',
        'data/mail_template_data.xml',
        'data/ir_cron_data.xml',
        'views/conference_waitlist_views.xml',
        'views/event_event_views.xml',
        'views/event_registration_views.xml',
        'wizard/group_booking_views.xml',
        'views/menus.xml',
        'views/website_templates.xml',
        'views/portal_templates.xml',
    ],
    'installable': True,
}
