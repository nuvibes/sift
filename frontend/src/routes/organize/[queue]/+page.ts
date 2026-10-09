import { redirect } from '@sveltejs/kit';

import { movedTo } from '$lib/organize/panels';

/* A queue that has been renamed is not a dead address. */
export function load({ params, url }: { params: Record<string, string>; url: URL }): void {
	const now = movedTo(params.queue);
	if (now) redirect(308, `/organize/${now}${url.search}`);
}
