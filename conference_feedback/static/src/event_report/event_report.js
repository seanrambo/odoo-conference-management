import { Component, onWillStart, useEffect, useRef, useState } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { standardActionServiceProps } from "@web/webclient/actions/action_service";

const CHART_HEIGHT = 220;
const PAD = { top: 16, right: 16, bottom: 28, left: 40 };

/**
 * Post-event report for one event: KPI tiles, cumulative registrations,
 * sessions, speakers and revenue. Data comes from
 * event.event.get_conference_report_data().
 */
export class EventReport extends Component {
    static template = "conference_feedback.EventReport";
    static props = { ...standardActionServiceProps };

    setup() {
        this.orm = useService("orm");
        this.actionService = useService("action");
        const ctx = this.props.action.context || {};
        this.state = useState({
            eventId: (ctx.active_model === "event.event" && ctx.active_id) || ctx.default_event_id || false,
            events: [],
            data: null,
            width: 640,
            hover: null,
        });
        this.chartRef = useRef("chart");

        onWillStart(async () => {
            if (this.state.eventId) {
                await this.load(this.state.eventId);
            } else {
                await this.loadEvents();
            }
        });
        // Track the chart container's width whenever it is on screen (it appears once an event is loaded).
        useEffect(
            (el) => {
                if (!el) {
                    return;
                }
                const observer = new ResizeObserver(() => this.measure());
                observer.observe(el);
                this.measure();
                return () => observer.disconnect();
            },
            () => [this.chartRef.el]
        );
    }

    measure() {
        const el = this.chartRef.el;
        if (el && el.clientWidth) {
            this.state.width = el.clientWidth;
        }
    }

    async loadEvents() {
        this.state.events = await this.orm.searchRead("event.event", [], ["name", "date_begin"], {
            order: "date_begin desc",
            limit: 40,
        });
    }

    async load(eventId) {
        this.state.eventId = eventId;
        this.state.data = await this.orm.call("event.event", "get_conference_report_data", [[eventId]]);
    }

    async onPickEvent(eventId) {
        await this.load(eventId);
    }

    onClickBack() {
        if (this.props.action.context?.active_id) {
            this.actionService.restore();
        } else if (this.state.data) {
            this.state.data = null;
            this.loadEvents();
        } else {
            this.actionService.doAction("event.action_event_view", { clearBreadcrumbs: true });
        }
    }

    openEvent() {
        this.actionService.doAction({
            type: "ir.actions.act_window",
            res_model: "event.event",
            res_id: this.state.eventId,
            views: [[false, "form"]],
        });
    }

    openSession(session) {
        this.actionService.doAction({
            type: "ir.actions.act_window",
            res_model: "event.track",
            res_id: session.id,
            views: [[false, "form"]],
        });
    }

    // ------------------------------------------------------------------
    // Formatting
    // ------------------------------------------------------------------

    formatDay(iso) {
        const [y, m, d] = iso.split("-").map(Number);
        return new Date(y, m - 1, d).toLocaleDateString(undefined, { day: "numeric", month: "short" });
    }

    barWidth(value, max) {
        return max ? `${Math.max(2, Math.round((100 * value) / max))}%` : "0%";
    }

    get maxSessionAttendance() {
        const sessions = this.state.data?.sessions || [];
        return Math.max(1, ...sessions.map((s) => Math.max(s.attended, s.capacity || 0)));
    }

    get maxTicketAmount() {
        const tickets = this.state.data?.revenue?.tickets || [];
        return Math.max(1, ...tickets.map((t) => t.amount));
    }

    // ------------------------------------------------------------------
    // Registrations chart (single series: cumulative registrations)
    // ------------------------------------------------------------------

    get chart() {
        const points = this.state.data?.timeline || [];
        const width = Math.max(this.state.width, 280);
        const innerW = width - PAD.left - PAD.right;
        const innerH = CHART_HEIGHT - PAD.top - PAD.bottom;
        const top = Math.max(1, ...points.map((p) => p.total));
        const step = niceStep(top / 4);
        const tickCount = Math.ceil(top / step);
        const maxY = step * tickCount;
        const x = (i) => PAD.left + (points.length > 1 ? (i * innerW) / (points.length - 1) : innerW / 2);
        const y = (v) => PAD.top + innerH - (v * innerH) / maxY;
        const coords = points.map((p, i) => [x(i), y(p.total)]);
        const line = coords.map(([cx, cy], i) => `${i ? "L" : "M"}${cx.toFixed(1)},${cy.toFixed(1)}`).join(" ");
        const area = coords.length
            ? `${line} L${coords.at(-1)[0].toFixed(1)},${y(0)} L${coords[0][0].toFixed(1)},${y(0)} Z`
            : "";
        const ticks = Array.from({ length: tickCount + 1 }, (_, i) => ({ value: step * i, y: y(step * i) }));
        const labelEvery = Math.max(1, Math.ceil(points.length / Math.max(2, Math.floor(innerW / 90))));
        const xLabels = points
            .map((p, i) => ({ i, x: x(i), label: this.formatDay(p.date) }))
            .filter(({ i }) => i % labelEvery === 0 || i === points.length - 1)
            .filter((l, idx, arr) => idx === arr.length - 1 || arr.at(-1).x - l.x > 60);
        return {
            width,
            height: CHART_HEIGHT,
            line,
            area,
            ticks,
            xLabels,
            coords,
            left: PAD.left,
            right: width - PAD.right,
            baseline: y(0),
        };
    }

    onChartMove(ev) {
        const points = this.state.data?.timeline || [];
        if (!points.length) {
            return;
        }
        const rect = ev.currentTarget.getBoundingClientRect();
        const chart = this.chart;
        const px = ev.clientX - rect.left;
        const span = chart.right - chart.left;
        const i = points.length > 1
            ? Math.round(((px - chart.left) / span) * (points.length - 1))
            : 0;
        this.state.hover = Math.min(points.length - 1, Math.max(0, i));
    }

    onChartLeave() {
        this.state.hover = null;
    }

    get hoverPoint() {
        const i = this.state.hover;
        if (i === null || i === undefined) {
            return null;
        }
        const point = this.state.data.timeline[i];
        const [cx, cy] = this.chart.coords[i];
        const flip = cx > this.chart.width - 160;
        return {
            ...point,
            label: this.formatDay(point.date),
            cx,
            cy,
            style: `top: ${Math.max(0, cy - 56)}px; ${flip ? `right: ${this.chart.width - cx + 12}px` : `left: ${cx + 12}px`};`,
        };
    }
}

/** Smallest whole "round" step (1, 2, 5 × 10^n) at least as big as value. */
function niceStep(value) {
    if (value <= 1) {
        return 1;
    }
    const exp = Math.pow(10, Math.floor(Math.log10(value)));
    for (const m of [1, 2, 5, 10]) {
        if (m * exp >= value) {
            return m * exp;
        }
    }
    return 10 * exp;
}

registry.category("actions").add("conference_feedback.event_report", EventReport);
