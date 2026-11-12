import { registry } from "@web/core/registry";

registry.category("web_tour.tours").add("conference_feedback_event_report", {
    steps: () => [
        {
            content: "Open the report from the event form",
            trigger: ".o_form_view button[name='action_open_event_report']",
            run: "click",
        },
        {
            content: "Checked-in tile shows the attendee",
            trigger: ".o_conference_report .o_cr_tile:contains('Checked in') .o_cr_value:contains('1')",
        },
        {
            content: "Rating tile shows the average",
            trigger: ".o_conference_report .o_cr_tile:contains('Session rating') .o_cr_value:contains('5')",
        },
        {
            content: "The chart is drawn",
            trigger: ".o_conference_report svg:has(path.o_cr_line)",
            run() {
                const svg = this.anchor;
                const box = svg.getBoundingClientRect();
                svg.dispatchEvent(new MouseEvent("mousemove", {
                    bubbles: true, clientX: box.left + box.width / 2, clientY: box.top + 40,
                }));
            },
        },
        {
            content: "Hovering shows the tooltip",
            trigger: ".o_conference_report .o_cr_tooltip:contains('registered')",
        },
        {
            content: "Session row with attendance and rating",
            trigger: ".o_conference_report tr.o_cr_row:contains('Digital health keynote'):contains('(1)')",
            run: "click",
        },
        {
            content: "Opens the session",
            trigger: ".o_form_view .o_field_widget[name='name'] input:value('Digital health keynote'), .o_form_view .o_field_widget[name='name']:contains('Digital health keynote')",
        },
    ],
});
