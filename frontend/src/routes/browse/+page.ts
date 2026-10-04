import { redirect } from '@sveltejs/kit';

/*
 * THE OLD WORD IN THE ADDRESS, moved to the new one.
 *
 * The Files wall filtered to one username is `?username=<id>`; the older `?account=<id>` is still
 * in addresses nobody here can rewrite: a Saved Filter, a bookmark, a link pasted into a message.
 * The bar's chip reads only the new one, so without this the wall would be filtered while the bar
 * showed nothing filtering it, which is the one state that chip exists to prevent.
 *
 * Only the name of the parameter changes; everything else in the address rides along in order.
 */
export function load({ url }: { url: URL }): Record<string, never> {
	const old = url.searchParams.get('account');
	if (old === null) return {};
	const moved = new URLSearchParams(url.searchParams);
	moved.delete('account');
	if (!moved.has('username')) moved.set('username', old);
	const search = moved.toString();
	redirect(308, `/browse${search ? `?${search}` : ''}`);
}
