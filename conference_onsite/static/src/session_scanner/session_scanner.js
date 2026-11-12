import { _t } from "@web/core/l10n/translation";
import { BarcodeScanner } from "@barcodes/components/barcode_scanner";
import { Component, onWillStart, useState } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { useBus, useService } from "@web/core/utils/hooks";
import { url } from "@web/core/utils/urls";
import { debounce } from "@web/core/utils/timing";
import { standardActionServiceProps } from "@web/webclient/actions/action_service";

const MODEL = "conference.session.attendance";
const WARNING_STATUSES = ["outside_window", "session_full", "already_recorded"];

/**
 * Door scanner for one conference session: staff pick the session, then scan
 * badges (phone camera or USB/Bluetooth scanner) or search attendees by name.
 * Each successful scan records a conference.session.attendance.
 */
export class SessionScanner extends Component {
    static template = "conference_onsite.SessionScanner";
    static components = { BarcodeScanner };
    static props = { ...standardActionServiceProps };

    setup() {
        this.orm = useService("orm");
        this.actionService = useService("action");
        this.notification = useService("notification");
        const ctx = this.props.action.context || {};
        this.fromEventForm = Boolean(ctx.default_event_id || ctx.active_id);
        this.state = useState({
            eventId: ctx.default_event_id || (ctx.active_model === "event.event" && ctx.active_id) || false,
            events: [],
            data: null,
            sessionId: false,
            result: null,
            searchTerm: "",
            searchResults: [],
            busy: false,
        });
        this.debouncedSearch = debounce(this.search.bind(this), 250);

        const barcode = useService("barcode");
        useBus(barcode.bus, "barcode_scanned", (ev) => this.onBarcodeScanned(ev.detail.barcode));

        onWillStart(async () => {
            const ext = new Audio().canPlayType("audio/ogg") ? "ogg" : "mp3";
            this.sounds = {
                error: new Audio(url(`/barcodes/static/src/audio/error.${ext}`)),
                notify: new Audio(url(`/mail/static/src/audio/ting.${ext}`)),
            };
            if (this.state.eventId) {
                await this.loadEvent(this.state.eventId);
            } else {
                this.state.events = await this.orm.call(MODEL, "scanner_events", []);
            }
        });
    }

    // ------------------------------------------------------------------
    // Getters
    // ------------------------------------------------------------------

    get session() {
        return this.state.data?.sessions.find((s) => s.id === this.state.sessionId);
    }

    get sessionsByDay() {
        const days = [];
        for (const session of this.state.data?.sessions || []) {
            let day = days.at(-1);
            if (!day || day.label !== session.day) {
                day = { label: session.day, sessions: [] };
                days.push(day);
            }
            day.sessions.push(session);
        }
        return days;
    }

    get resultClass() {
        const result = this.state.result;
        if (!result) {
            return "";
        }
        if (result.success) {
            return "text-bg-success";
        }
        return WARNING_STATUSES.includes(result.status) ? "text-bg-warning" : "text-bg-danger";
    }

    get resultIcon() {
        const result = this.state.result;
        if (result?.success) {
            return "fa-check-circle";
        }
        return WARNING_STATUSES.includes(result?.status) ? "fa-exclamation-triangle" : "fa-times-circle";
    }

    // ------------------------------------------------------------------
    // Loading
    // ------------------------------------------------------------------

    async loadEvent(eventId) {
        this.state.eventId = eventId;
        this.state.data = await this.orm.call(MODEL, "scanner_init", [eventId]);
        this.state.sessionId = this.state.data.default_session_id;
        this.state.result = null;
    }

    async onPickEvent(eventId) {
        await this.loadEvent(eventId);
    }

    onSessionChange(ev) {
        this.state.sessionId = parseInt(ev.target.value) || false;
        this.state.result = null;
    }

    // ------------------------------------------------------------------
    // Scanning
    // ------------------------------------------------------------------

    playSound(type) {
        const sound = this.sounds?.[type];
        if (sound) {
            sound.currentTime = 0;
            sound.play().catch(() => {});
        }
    }

    async onBarcodeScanned(barcode) {
        if (!this.state.data) {
            return;
        }
        if (!this.session) {
            this.playSound("error");
            this.notification.add(_t("Pick a session first."), { type: "warning" });
            return;
        }
        await this.record({ barcode });
    }

    async record(params, force = false) {
        if (this.state.busy || !this.state.sessionId) {
            return;
        }
        this.state.busy = true;
        try {
            const result = await this.orm.call(MODEL, "scan", [], {
                track_id: this.state.sessionId,
                force,
                ...params,
            });
            result.params = params;
            this.state.result = result;
            if (result.session && this.session && result.session.id === this.session.id) {
                this.session.count = result.session.count;
            }
            this.playSound(result.success ? "notify" : "error");
            if (result.success && "vibrate" in window.navigator) {
                window.navigator.vibrate(100);
            }
        } finally {
            this.state.busy = false;
        }
    }

    async onForce() {
        if (this.state.result?.can_force) {
            await this.record(this.state.result.params, true);
        }
    }

    onDismiss() {
        this.state.result = null;
    }

    // ------------------------------------------------------------------
    // Manual search
    // ------------------------------------------------------------------

    onSearchInput(ev) {
        this.state.searchTerm = ev.target.value;
        this.debouncedSearch();
    }

    async search() {
        const term = this.state.searchTerm.trim();
        if (term.length < 2) {
            this.state.searchResults = [];
            return;
        }
        this.state.searchResults = await this.orm.call(MODEL, "scanner_search", [this.state.eventId, term]);
    }

    async onPickAttendee(attendee) {
        this.state.searchResults = [];
        this.state.searchTerm = "";
        await this.record({ registration_id: attendee.id });
    }

    // ------------------------------------------------------------------
    // Navigation
    // ------------------------------------------------------------------

    onClickBack() {
        if (this.fromEventForm) {
            this.actionService.restore();
        } else if (this.state.data) {
            this.state.data = null;
            this.state.result = null;
            this.state.eventId = false;
            this.orm.call(MODEL, "scanner_events", []).then((events) => (this.state.events = events));
        } else {
            this.actionService.doAction("event.action_event_view", { clearBreadcrumbs: true });
        }
    }
}

registry.category("actions").add("conference_onsite.session_scanner", SessionScanner);
