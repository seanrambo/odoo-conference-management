# Odoo Conference Management

Odoo 19 modules that run a conference on top of Odoo Events, from the call for papers and paid registration to the event day itself.

| Module | Phase | What it does |
|---|---|---|
| `conference_management` | 1 | Call for papers, peer review, document uploads, AV packs, materials page |
| `conference_registration` | 2 | Waitlist, group bookings, visa invitation letters, ticket transfers, self-service cancellation |
| `payment_mpesa` | 2 | M-Pesa online payments (Safaricom Daraja STK push) for any Odoo checkout |
| `conference_onsite` | 3 | Session attendance scanning, CPD points, live check-in numbers, emails when a session moves or is cancelled |

## Phase 1: `conference_management`

- **Call for papers.** Turn it on per event, set a deadline and guidelines, and pick a reviewer pool. Authors submit from their portal: title, abstract, session type, co-authors and files. The event website gets a "Call for Papers" page.
- **Peer review.** Reviewers can be auto-assigned (least-loaded first, never the author). Blind review is optional. Reviewers score originality, relevance, clarity and quality from 1 to 5, give a recommendation, and write separate comments for the author and for the committee. Each reviewer sees only their own reviews.
- **Decisions.** Accept, Request Revision or Reject. The author receives an email with the reviewer feedback. An accepted submission becomes a confirmed talk on the event agenda.
- **Documents.** Organisers, speakers, authors and (optionally) registered attendees can upload slides, papers, posters and handouts. Each file has a visibility setting and an approval step, and replaced files are kept in its version history. Allowed file types, maximum size and the upload deadline are set per event.
- **AV pack.** One ZIP of approved slides per room and day, named by start time, room and speaker, for example `0900_Hall-A_J-Kamau_Opening-keynote.pptx`.
- **Materials page.** An event website page where attendees download the files they are allowed to see.

## Phase 2: `conference_registration`

Settings are on the event form, **Bookings** tab. Menus are under **Conference › Bookings**.

- **Waitlist.** A sold-out event page shows "Join the waitlist". When a seat frees up (a cancellation, or more seats added), it goes to the next person in line:
  - free seats are booked straight away;
  - paid seats are **held** for that person for a set time (24 hours by default) and hidden from everyone else, and they pay online to take it. If they don't pay in time, the offer expires and moves to the next person.
- **Group bookings.** **Group Booking** on the event: choose who pays, paste attendees (`Name, Email, Phone`, one per line, straight from a spreadsheet) and book. This creates one sales order, one registration per attendee and one invoice.
- **Manage-your-booking page.** Every attendee gets a personal link by email, added automatically to new events' communication schedule. From that page they can:
  - download their ticket;
  - **transfer** it to someone else (the old ticket's barcode and link stop working);
  - **cancel**, within a deadline you set (cancelling a paid ticket flags a refund request for an organiser);
  - request a **visa invitation letter**.
- **Visa invitation letters.** Attendees enter their passport details, organisers review and issue (or turn on automatic issuing), and the PDF letter is emailed to them. Each letter carries a verification address embassies can open to confirm it is genuine. The letter is withdrawn if the ticket is transferred.
- **My Event Tickets** in the customer portal for signed-in attendees.

## Phase 2: `payment_mpesa`

Customers enter their Safaricom number at checkout, get a prompt on their phone and approve it with their M-Pesa PIN. It works for website orders, invoices and waitlist offers.

- Paybill and Till (Buy Goods) accounts. Test mode uses the Daraja sandbox; enabled mode uses production.
- Callbacks from Safaricom aren't signed, so a payment is only marked as done after Safaricom's own status query confirms it. A forged callback cannot complete an order.
- A scheduled job checks pending payments every two minutes in case a callback never arrives.
- Amounts must be in KES and are charged in whole shillings, rounded down.

**Setup:** Invoicing/Website › Configuration › Payment Providers › M-Pesa. Enter the consumer key and secret from your app on the Daraja portal, plus the short code and passkey, then set the provider to Test or Enabled. Safaricom must be able to reach `https://<your-domain>/payment/mpesa/callback` from the internet.

## Phase 3: `conference_onsite`

Settings are on the event form, **Event Day** tab. Menus are under **Conference › Event Day**.

- **Entrance check-in** uses Odoo's own **Registration Desk** screen: scan the QR code on the ticket with a phone camera or a USB scanner, then print or reprint the badge. It installs on a phone as an app.
- **Session Scanner.** Door staff open **Session Scanner** (from the menu, or the button on the event), pick the session and scan badges. Each scan records that the attendee was in the room. The screen shows a large live count of people in the room, and a clear result after each scan:
  - green: recorded (and checked in to the event, if they skipped the registration desk);
  - amber: already scanned, the room is full, or the session isn't on yet. For "full" and "not on yet", staff can still let the person in, and the record is marked as an override;
  - red: an invalid badge, a ticket for another event, a cancelled or unconfirmed registration, or a cancelled session.

  No badge? Staff search by name, email or company and tap the person.
- **Room capacity** per session (optional), and a **scan window** per event: 30 minutes before the start until 30 minutes after the end, by default.
- **CPD points.** Turn on CPD for the event and give each session its points. An attendee's registration shows the sessions they attended and their total. The points are fixed when the scan happens, so editing a session afterwards doesn't change what people have already earned.
- **Live numbers** on the event form: checked in out of expected, and total session scans. **Session Attendance** has list, pivot and graph views by session, room, day and hour.
- **Session change emails.** When a published session's time or room changes, or it is moved to a cancelled stage, the people who saved it to their agenda and its speaker are emailed what changed, with the old and new values. For key sessions ("wishlisted by default") every confirmed attendee is emailed. People who turned off a key session's reminder are left out. Sessions that have already finished, and unpublished ones, don't send emails. A note on the session records how many people were emailed.

## Install

Add this repository's folder to your Odoo `addons_path`, update the apps list, then install the modules you need:

```bash
./odoo-bin -c odoo.conf -d <database> -i conference_management,conference_registration,payment_mpesa,conference_onsite
```

`conference_registration` depends on `conference_management` and `event_sale`. `payment_mpesa` only needs `payment`. `conference_onsite` only needs `conference_management`.

## Roles

| Role | Access |
|---|---|
| Events user (organiser) | Everything: submissions, decisions, reviews, documents, AV pack, waitlists, visa letters, group bookings |
| Events / Registration Desk (door staff) | Registration Desk and Session Scanner only: check in and scan attendees, read sessions and rooms |
| Conference / Reviewer | Their own assigned reviews and the files of those submissions |
| Portal user | Their own submissions, uploaded files and tickets |
| Anyone with a booking link | That one booking: ticket, transfer, cancellation, visa letter |

## Tests

```bash
./odoo-bin -c odoo.conf -d <test_db> -i conference_registration,payment_mpesa,conference_onsite \
  --test-tags /conference_management,/conference_registration,/payment_mpesa,/conference_onsite --stop-after-init
```

The Session Scanner browser test needs Chrome or Chromium on the `PATH` and the `websocket-client` Python package. Without them it is skipped.

## Notes

- Blind review hides the author's name and contact from reviewers. File names and file contents can still identify the author, so ask authors to upload anonymised files.
- For large uploads, raise your reverse proxy's request size limit too, for example `client_max_body_size` in nginx.
- PDFs (tickets, visa letters, quotations) need `wkhtmltopdf` installed on the server.
- Session change emails go out one per session. Someone who saved three sessions that all move gets three emails.
- The waitlist works on events with a seat limit and a single session. Events with several time slots are not covered yet.

## License

LGPL-3
