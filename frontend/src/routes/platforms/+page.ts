import { redirect } from '@sveltejs/kit';

import { movedAddress } from '$lib/shell/moved';

/* The Sites wall's old address. See `$lib/shell/moved` for the table and for why it is a 308. */
export function load({ url }: { url: URL }): void {
	const now = movedAddress('platforms');
	if (now) redirect(308, `/${now}${url.search}`);
}
