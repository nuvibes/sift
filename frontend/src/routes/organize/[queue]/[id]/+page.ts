import { redirect } from '@sveltejs/kit';

import { itemMovedTo } from '$lib/organize/panels';

/*
 * One item of a renamed queue goes to the same item under the new name.
 *
 * The same move the queue screen above makes, one level down: a group opened from a decision taken
 * last month, or a person's page linked from a receipt, must still open. The id is carried across
 * because it is the thing being asked for. The queue name is only the door it was reached
 * through, and both doors lead to the same screen.
 *
 * The old names still DRAW here as well (`detailFor` keeps them), which is not a contradiction: a
 * redirect is what an address should do and the drawing is what stops a moment's flicker becoming
 * an empty screen if this ever cannot run. See `itemMovedTo` for where the map lives: it is
 * the queue's map with one difference, and the difference is written there.
 */
export function load({ params, url }: { params: Record<string, string>; url: URL }): void {
	const now = itemMovedTo(params.queue);
	if (now) redirect(308, `/organize/${now}/${params.id}${url.search}`);
}
