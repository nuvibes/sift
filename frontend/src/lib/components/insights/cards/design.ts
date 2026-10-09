/*
 * What a card's picture is, read from what it carries: a Theater wall in miniature, the faces
 * of the people it ranks, the
 * marks of its Sites, its shares as a stack, its bars as a skyline, its days as squares, or its
 * one picture (a mark, a song's art, a face, a file). A card carrying none of them is its words.
 */
import type { RecapCard } from '$lib/library/recaps.svelte';

export type Design =
	| 'own'
	| 'marks'
	| 'podium'
	| 'shares'
	| 'skyline'
	| 'days'
	| 'calendar'
	| 'mark'
	| 'art'
	| 'face'
	| 'picture'
	| 'list'
	| 'wall';

/** Past this many days the calendar is a year's, drawn by the year's own card. */
export const DAYS_MOST = 62;

function ofRows(rows: RecapCard['rows']): Design | null {
	const kinds = new Set(rows.map((row) => row.piece.kind));
	if (rows.length > 0 && kinds.size === 1 && kinds.has('site')) return 'marks';
	return rows.length > 1 && rows.some((row) => row.cover) ? 'podium' : null;
}

function ofChart(chart: RecapCard['chart']): Design | null {
	if (!chart) return null;
	if (chart.kind === 'share') return chart.bars.length === 1 ? 'shares' : null;
	return chart.bars.length > 1 ? 'skyline' : null;
}

function ofDays(days: number): Design | null {
	if (days === 0) return null;
	return days > DAYS_MOST ? 'calendar' : 'days';
}

function ofCover(card: RecapCard, mark: boolean, square: boolean): Design | null {
	if (!card.cover) return null;
	if (mark) return 'mark';
	if (square) return 'art';
	const face = card.kind === 'top_person' || card.cover.startsWith('/api/people/');
	return face ? 'face' : 'picture';
}

/** The card's picture: a Site's `mark` is drawn whole, a `square` (a song's art) square. */
export function designOf(card: RecapCard, mark: boolean, square: boolean): Design | null {
	const rows = card.rows ?? [];
	if ((card as { wall?: unknown }).wall) return 'wall';
	return (
		ofRows(rows) ??
		ofChart(card.chart) ??
		ofDays(card.calendar?.days.length ?? 0) ??
		ofCover(card, mark, square) ??
		(rows.length > 0 ? 'list' : null)
	);
}
