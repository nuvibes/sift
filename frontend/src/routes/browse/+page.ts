import { redirect } from '@sveltejs/kit';

/* THE OLD WORD IN THE ADDRESS, moved to the new one. */
export function load({ url }: { url: URL }): Record<string, never> {
	const old = url.searchParams.get('account');
	if (old === null) return {};
	const moved = new URLSearchParams(url.searchParams);
	moved.delete('account');
	if (!moved.has('username')) moved.set('username', old);
	const search = moved.toString();
	redirect(308, `/browse${search ? `?${search}` : ''}`);
}
