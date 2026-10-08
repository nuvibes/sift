import { placeOf, type Place } from '$lib/components/insights/period';

/* The period the Stats view is at, read from its address as Insights reads its own. */
export function load({ url }: { url: URL }): Place {
	return placeOf(url);
}
