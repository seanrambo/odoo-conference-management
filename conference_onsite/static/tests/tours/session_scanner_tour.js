import { registry } from "@web/core/registry";

/**
 * Opens the Session Scanner from its menu, picks the event and a session,
 * finds an attendee by name and records them. Then scans the same attendee
 * again through the barcode bus and checks the "already recorded" warning.
 */
registry.category("web_tour.tours").add("conference_onsite_session_scanner", {
    steps: () => [
        {
            content: "Pick the event",
            trigger: ".o_conference_session_scanner button:contains('Scanner Tour Summit')",
            run: "click",
        },
        {
            content: "Pick the session",
            trigger: ".o_conference_session_scanner select.o_session_select",
            run() {
                const select = this.anchor;
                const option = [...select.options].find((o) => o.textContent.includes("Tour keynote"));
                select.value = option.value;
                select.dispatchEvent(new Event("change", { bubbles: true }));
            },
        },
        {
            content: "Counter shows an empty room",
            trigger: ".o_session_counter .display-5:contains('0')",
        },
        {
            content: "Search an attendee by name",
            trigger: "input.o_attendee_search",
            run: "edit Zawadi",
        },
        {
            content: "Pick the attendee",
            trigger: ".o_attendee_results button:contains('Zawadi Achieng')",
            run: "click",
        },
        {
            content: "Attendee is recorded",
            trigger: ".o_scan_result.text-bg-success:contains('Zawadi Achieng')",
        },
        {
            content: "Counter went up",
            trigger: ".o_session_counter .display-5:contains('1')",
            run() {
                // Simulate a USB scanner reading the same badge.
                odoo.__WOWL_DEBUG__.root.env.services.barcode.bus.trigger("barcode_scanned", {
                    barcode: "424242424242",
                });
            },
        },
        {
            content: "Second scan is flagged",
            trigger: ".o_scan_result.text-bg-warning:contains('Already recorded')",
        },
    ],
});
