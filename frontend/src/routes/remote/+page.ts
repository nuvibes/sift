import { redirect } from '@sveltejs/kit';

import { remoteGoesTo, THEATER_ON_A_PHONE } from '$lib/components/shell/nav';

/*
 * The Remote's address is the Remote's own screen, and this sends nobody anywhere: Theater has no
 * phone screen, so the phone drives the desk from here (see `THEATER_ON_A_PHONE`). Were Theater
 * to have one, this address would lead into its second mode, so an old bookmark, a link in
 * History and the tab all land on the one screen that drives the desk.
 */
export function load(): void {
	const to = remoteGoesTo(THEATER_ON_A_PHONE);
	if (to !== null) redirect(307, to);
}
