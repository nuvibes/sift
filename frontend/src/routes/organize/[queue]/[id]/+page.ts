import { redirect } from '@sveltejs/kit';

import { itemMovedTo } from '$lib/organize/panels';

/* One item of a renamed queue goes to the same item under the new name. */
export function load({ params, url }: { params: Record<string, string>; url: URL }): void {
	const now = itemMovedTo(params.queue);
	if (now) redirect(308, `/organize/${now}/${params.id}${url.search}`);
}
