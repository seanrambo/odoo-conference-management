# Odoo Conference Management

An Odoo 19 module that runs a conference's call for papers, peer review and presentation files, on top of Odoo Events.

This is **Phase 1** of a full conference management system (planning → call for papers → registration → files → event day → feedback).

## Features

- **Call for papers.** Turn it on per event, set a deadline and guidelines, and pick a reviewer pool. Authors submit from their portal: title, abstract, session type, co-authors and files. The event website gets a "Call for Papers" page.
- **Peer review.** Reviewers can be auto-assigned (least-loaded first, never the author). Blind review is optional. Reviewers score originality, relevance, clarity and quality from 1 to 5, give a recommendation, and write separate comments for the author and for the committee. Each reviewer sees only their own reviews.
- **Decisions.** Accept, Request Revision or Reject. The author receives an email with the reviewer feedback. An accepted submission becomes a confirmed talk on the event agenda.
- **Documents.** Organizers, speakers, authors and (optionally) registered attendees can upload slides, papers, posters and handouts. Each file has a visibility setting and an approval step, and replaced files are kept in its version history. Allowed file types, maximum size and the upload deadline are set per event.
- **AV pack.** One ZIP of approved slides per room and day, named by start time, room and speaker, for example `0900_Hall-A_J-Kamau_Opening-keynote.pptx`.
- **Materials page.** An event website page where attendees download the files they are allowed to see.

## Install

1. Copy `conference_management/` into a folder on your Odoo `addons_path`.
2. Update the apps list, then install **Conference Management**. Its dependencies are `website_event_track`, `portal` and `mail`.

Or from the command line:

```bash
./odoo-bin -c odoo.conf -d <database> -i conference_management
```

## Roles

| Role | Access |
|---|---|
| Events user (organizer) | Everything: submissions, decisions, all reviews, documents, AV pack |
| Conference / Reviewer | Their own assigned reviews and the files of those submissions |
| Portal user | Their own submissions and uploaded files |

## Tests

```bash
./odoo-bin -c odoo.conf -d <test_db> -i conference_management --test-tags /conference_management --stop-after-init
```

## Notes

- Blind review hides the author's name and contact from reviewers. File names and file contents can still identify the author, so ask authors to upload anonymized files.
- For large uploads, raise your reverse proxy's request size limit too, for example `client_max_body_size` in nginx.

## License

LGPL-3
