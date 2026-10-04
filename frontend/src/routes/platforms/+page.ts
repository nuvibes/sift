import { redirect } from '@sveltejs/kit';

import { movedAddress } from '$lib/shell/moved';

/*
 * The Sites wall's old address. See `$lib/shell/moved` for the table and for why it is a 308.
 *
 * No `+page.svelte` beside this, unlike the renamed queues: those still DRAW under their old name
 * because the panel registry kept them, and there is nothing here to draw: the wall itself moved,
 * so anybody arriving belongs at the new address rather than on a copy of it kept alive here.
 */
export function load({ url }: { url: URL }): void {
	const now = movedAddress('platforms');
	if (now) redirect(308, `/${now}${url.search}`);
}
