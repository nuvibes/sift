/*
 * A period is a place with an address: `/insights?period=month&at=<any day in it>`.
 *
 * The span is the tab and `at` is any day inside the period, the same two things the server's `GET
 * /api/insights` is asked (`slices/insights/router.py`, `insights_page`). In the address rather
 * than held on the screen, so Back goes to the period before, a period can be bookmarked, and a tab
 * is a link.
 *
 * What the period is (where it starts and ends, whether today is inside it) is the server's answer
 * (`from`, `to`, `today_is_live`), never worked out here: the device's calendar decides the days,
 * and the reader's browser may be in another zone. The arrows step one day past either end of the
 * answer, which is inside the next period whatever the span.
 */
import type { components } from '$lib/api/schema';

import { PERIOD_WORDS } from '$lib/components/insights/words';

export type InsightsPage = components['schemas']['InsightsPage'];
export type InsightsBlock = components['schemas']['InsightsBlock'];
export type Span = InsightsPage['period'];

/** The tabs, in the order they are drawn: the whole record first, then from the nearest out. */
const SPANS: readonly Span[] = ['all', 'day', 'week', 'month', 'year'];

/** What a bare `/insights` shows, today: the server's own default for the period (router.py). */
const DEFAULT_SPAN: Span = 'day';

/** Where the screen is: a span, and a day inside the period, or null for the one holding today. */
export interface Place {
	period: Span;
	at: string | null;
}

const DAY = /^(\d{4})-(\d{2})-(\d{2})$/;

/** Whether `value` is a real calendar day written as the address writes one. */
function isDay(value: string): boolean {
	const parts = value.match(DAY);
	if (!parts) return false;
	const [year, month, day] = [Number(parts[1]), Number(parts[2]), Number(parts[3])];
	const made = new Date(Date.UTC(year, month - 1, day));
	return made.getUTCMonth() === month - 1 && made.getUTCDate() === day;
}

/**
 * The place an address names. Anything it cannot read is left out rather than guessed at: an
 * unknown span is the default one, and a day that is not a day is today's period.
 */
export function placeOf(url: URL): Place {
	const asked = url.searchParams.get('period');
	const period = SPANS.find((span) => span === asked) ?? DEFAULT_SPAN;
	const at = url.searchParams.get('at');
	return { period, at: period !== 'all' && at !== null && isDay(at) ? at : null };
}

/** The two screens a period is read on: Insights, and its Stats view of every figure. */
export const INSIGHTS_PATH = '/insights';
export const STATS_PATH = '/insights/stats';

/** The address of a place. "all" has no day in it: it is one period, from the first day to today. */
export function addressOf(place: Place, path: string = INSIGHTS_PATH): string {
	const query = new URLSearchParams({ period: place.period });
	if (place.at !== null && place.period !== 'all') query.set('at', place.at);
	return `${path}?${query}`;
}

/**
 * A calendar day `days` away from `iso`, both written "YYYY-MM-DD".
 *
 * Counted on a UTC midnight on purpose: a calendar day has no zone, and a local midnight steps by
 * 23 or 25 hours across a clock change, which would land a day short or a day over.
 */
export function dayAway(iso: string, days: number): string {
	const parts = iso.match(DAY);
	if (!parts) return iso;
	const moved = new Date(Date.UTC(Number(parts[1]), Number(parts[2]) - 1, Number(parts[3]) + days));
	return moved.toISOString().slice(0, 10);
}

/** The tabs: each span at the same day, so Month then Week shows the week holding that day. */
export function tabsFor(
	place: Place,
	path: string = INSIGHTS_PATH
): { id: Span; label: string; href: string }[] {
	return SPANS.map((span) => ({
		id: span,
		label: PERIOD_WORDS[span],
		href: addressOf({ period: span, at: place.at }, path)
	}));
}

/**
 * The periods either side of the one answered. Earlier is the day before its first day; later is
 * the day after its last, and there is no later while today is inside this period
 * (`today_is_live` is exactly that: today's figures were counted because today is one of its
 * days), because a later period has not happened. "All" has neither: it is the whole record.
 */
export function stepsFrom(page: InsightsPage): { earlier: Place | null; later: Place | null } {
	if (page.period === 'all') return { earlier: null, later: null };
	return {
		earlier: { period: page.period, at: dayAway(page.from, -1) },
		later: page.today_is_live ? null : { period: page.period, at: dayAway(page.to, 1) }
	};
}
