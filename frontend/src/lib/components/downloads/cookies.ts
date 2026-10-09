// SPDX-License-Identifier: AGPL-3.0-or-later

import type { components } from '$lib/api/schema';
import type { IconName } from '$lib/design/icons';
import { dayOf, sayAgo } from '$lib/shell/when';
import { counted } from '$lib/entity/entity-counts';

/**
 * How a Site's cookies stand, decided by the SERVER: a client's clock can step backwards. `none` is
 * a supported Site with no row, whose badge says what it needs.
 */
type CookieState = CookieRow['state'];

export type CookieRow = components['schemas']['ConnectionItem'];

/** A `Pick<>` of the save's own answer, so a moved field fails to compile rather than drifting. */
export type CookiesRead = Pick<
	components['schemas']['SavedConnection'],
	'cookies' | 'domains' | 'expires_at' | 'expired'
>;

export interface CookiesChecked {
	accepted: boolean;
	/** Written by the server, which made the request. */
	said: string;
}

/** The pill's word; `none` only for a Site the supported list does not name. */
export const STATE_WORD: Record<CookieState, string> = {
	saved: 'Cookies saved',
	ending_soon: 'Ending soon',
	expired: 'Cookies expired',
	none: 'No cookies'
};

/** What a Site gives somebody with no cookies: the site catalog's measured answer. */
type CookieNeed = components['schemas']['SupportedSite']['cookies'];

interface NeedBadge {
	label: string;
	state: 'failed' | 'blocked' | 'queued';
	icon: IconName;
}

/** One table for the cookies sheet and the supported list, so a need is never said two ways. */
export const NEED_BADGE: Record<CookieNeed, NeedBadge> = {
	required: { label: 'Required', state: 'failed', icon: 'lock' },
	partial: { label: 'Partial', state: 'blocked', icon: 'cookie' },
	not_needed: { label: 'Not required', state: 'queued', icon: 'playlist_add_check' }
};

/** By name and by the addresses that reach it: "twitter" finds X. */
export function siteMatches(typed: string, name: string, hosts: readonly string[]): boolean {
	const wanted = typed.trim().toLowerCase();
	if (!wanted) return true;
	return [name, ...hosts].some((one) => one.toLowerCase().includes(wanted));
}

/**
 * When the cookies run out (a day with its year) and when they were last used. Either may be
 * missing and neither is invented. `now` is a parameter so a test's clock cannot step backwards.
 */
export function saidAbout(row: CookieRow, now: number, site: string): string {
	// Only that nothing is saved; whether the Site wants any is `NEED_BADGE`'s.
	if (row.state === 'none') return `No cookies saved for ${site}.`;
	const ends =
		row.expires_at === null
			? 'No end date.'
			: `${row.state === 'expired' ? 'Ended' : 'Ends'} ${dayOf(row.expires_at)}.`;
	const used =
		row.last_used_at === null ? 'Not used yet.' : `Last used ${sayAgo(row.last_used_at, now)}.`;
	return `${ends} ${used}`;
}

export function readIsGood(read: CookiesRead): boolean {
	return read.cookies > 0 && !read.expired;
}

/** What the box says about a file BEFORE it is saved: after Save a cookie cannot be looked at. */
export function readBack(read: CookiesRead, site: string): string {
	if (read.cookies === 0) {
		return `That isn't a cookie file. Export one with a browser extension while signed in to ${site}.`;
	}
	if (read.expired) {
		return 'These cookies have already run out. Open the Site in your browser so it hands out new ones, then export a fresh file.';
	}
	const count = `${counted(read.cookies)} ${read.cookies === 1 ? 'cookie' : 'cookies'}`;
	const where = read.domains.length > 0 ? ` for ${read.domains.join(', ')}` : '';
	const until =
		read.expires_at === null ? ', with no end date' : `, until ${dayOf(read.expires_at)}`;
	return `Read: ${count}${where}${until}`;
}
