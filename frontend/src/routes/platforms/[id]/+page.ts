import { redirect } from '@sveltejs/kit';

import { movedAddress } from '$lib/shell/moved';

/* One Site's old address. The same move the wall above makes, one level down. */
export function load({ params, url }: { params: Record<string, string>; url: URL }): void {
	const now = movedAddress('platforms');
	if (now) redirect(308, `/${now}/${params.id}${url.search}`);
}
