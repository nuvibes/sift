/*
 * The pictures the year's cards draw beyond the ones a card carries: the picture of a thing a
 * card names, at the address its own wall draws it from (`COVERS` on the server), and the day a
 * calendar's brightest square is.
 */
import type { components } from '$lib/api/schema';

type HistoryPiece = components['schemas']['HistoryPiece'];
type DayValue = components['schemas']['DayValue'];

const WALLS: Record<string, (id: string) => string> = {
	person: (id) => `/api/people/${encodeURIComponent(id)}/cover`,
	site: (id) => `/api/sites/${encodeURIComponent(id)}/cover`,
	tag: (id) => `/api/tags/${encodeURIComponent(id)}/cover`,
	asset: (id) => `/api/assets/${encodeURIComponent(id)}/thumb`
};

/** The picture of the thing a piece names, or null for a piece that names no thing with one. */
export function pictureOf(piece: HistoryPiece): string | null {
	const wall = piece.kind === null ? undefined : WALLS[piece.kind];
	return wall && piece.id && !piece.gone ? wall(piece.id) : null;
}

/** The day with the most, the earliest on a tie, or null for a calendar with nothing in it. */
export function brightest(days: readonly DayValue[]): DayValue | null {
	let best: DayValue | null = null;
	for (const day of days) if (day.value > (best?.value ?? 0)) best = day;
	return best;
}

/** Each month's places: the people with time that month, the most first, a tie in the year's
 *  order; at most `most` a month. */
export function podiums(
	bars: readonly { parts: readonly { kind: string; value: number }[] }[],
	most = 3
): string[][] {
	return bars.map((bar) =>
		bar.parts
			.map((part, order) => ({ ...part, order }))
			.filter((part) => part.value > 0)
			.sort((a, b) => b.value - a.value || a.order - b.order)
			.slice(0, most)
			.map((part) => part.kind)
	);
}

/** Days since the Unix epoch of an ISO day, read as UTC: a calendar day has no zone. */
function dayNumber(iso: string): number {
	const [year, month, day] = iso.split('-').map(Number);
	return Math.floor(Date.UTC(year, month - 1, day) / 86_400_000);
}

/** Which week column of a calendar a day stands in, Monday first as the calendar lays them, and
 *  how far across the calendar that is (the middle of its column, as a share of the width). */
export function weekOf(
	days: readonly DayValue[],
	day: string
): { week: number; weeks: number; share: number } | null {
	if (days.length === 0) return null;
	const first = dayNumber(days[0].day);
	/* The weekday of the first day, Monday first: 1970-01-01 was a Thursday. */
	const lead = (((first + 3) % 7) + 7) % 7;
	const weeks = Math.ceil((days.length + lead) / 7);
	const week = Math.floor((dayNumber(day) - first + lead) / 7);
	return { week, weeks, share: (week + 0.5) / weeks };
}
