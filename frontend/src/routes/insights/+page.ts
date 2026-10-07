import { placeOf, type Place } from '$lib/components/insights/period';

/*
 * Which period the screen is at, read from its address: `/insights?period=month&at=<YYYY-MM-DD>`.
 *
 * A period is a place, so it is the address that holds it and not the screen. See
 * `$lib/components/insights/period`. Nothing is fetched here: the screen asks for the figures
 * itself, so the heading, the tabs and the recaps draw immediately and the figures arrive into
 * them, rather than a navigation that shows nothing until the slowest answer is in.
 */
export function load({ url }: { url: URL }): Place {
	return placeOf(url);
}
