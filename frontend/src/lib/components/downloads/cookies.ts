// SPDX-License-Identifier: AGPL-3.0-or-later

import type { components } from '$lib/api/schema';
import type { IconName } from '$lib/design/icons';
import { dayOf, sayAgo } from '$lib/shell/when';
import { counted } from '$lib/entity/entity-counts';

/**
 * How a Site's cookies stand, decided by the SERVER and never worked out again here.
 *
 * Four words rather than a date the client compares against its own clock: two screens each
 * deciding what "ending soon" means is how they come to disagree, and a client's clock can step
 * backwards, so the comparison is not even stable within one screen.
 *
 * `none` is what the sheet fills in for a Site Sift supports that has no row at all, so that every
 * supported Site has a line, and on that line the badge says what the Site NEEDS, not that
 * nothing is saved. Read off the generated row rather than written again, so the four words are the
 * server's four words.
 */
type CookieState = CookieRow['state'];

/** One Site's cookies, as `GET /site-connections` answers for them. The server's own shape. */
export type CookieRow = components['schemas']['ConnectionItem'];

/**
 * What `POST /site-connections/preview` understood of a file, without saving any of it.
 *
 * A `Pick<>` of the SAVE's own answer rather than a shape written out again, and the difference is
 * not tidiness: the read-back and the save describe the same file by the same reader, so a second
 * copy of the fields would not fail when the server moves: it would go quietly wrong, and the
 * screen is where somebody would find out. What the preview drops is `id` (nothing was saved, so
 * there is nothing to have an id) and `expires_last` (the read-back names one date, not a spread).
 */
export type CookiesRead = Pick<
	components['schemas']['SavedConnection'],
	'cookies' | 'domains' | 'expires_at' | 'expired'
>;

/** What `POST /site-connections/{id}/check` found when it asked the Site. */
export interface CookiesChecked {
	accepted: boolean;
	/** The sentence to read out. The server writes it, because the server made the request. */
	said: string;
}

/**
 * The pill's word, per state, for a Site that has something saved. Two of the words name the thing,
 * because a pill is read alone.
 *
 * `none` keeps a word only for a row whose Site the supported list does not name: every Site it
 * does name says what it needs instead (`needBadge`), the question somebody with nothing saved is
 * asking.
 */
export const STATE_WORD: Record<CookieState, string> = {
	saved: 'Cookies saved',
	ending_soon: 'Ending soon',
	expired: 'Cookies expired',
	none: 'No cookies'
};

/**
 * What a Site does for somebody with no cookies saved: the site catalog's measured declaration, per
 * Site, sent with the list of supported Sites. Read off the generated shape, so the three answers
 * are the server's three. There is no fourth: every supported Site was run without cookies, and one
 * that could not be reached was settled by where Sift sends a jar at all.
 */
type CookieNeed = components['schemas']['SupportedSite']['cookies'];

/** One badge: the word, the state whose colour it is said in, and its mark. */
interface NeedBadge {
	label: string;
	state: 'failed' | 'blocked' | 'queued';
	icon: IconName;
}

/**
 * The badge each need is drawn as, on the cookies sheet and on the supported list alike: one table,
 * so the two screens cannot say one Site's need two ways.
 *
 * The colour carries the severity and nothing else: red for a Site that gives nothing without
 * cookies, amber for one that gives some things, grey for one where they change nothing. A lock for
 * the first, because the Site is locked; the cookie for the second, because cookies are the key to
 * part of it; for the third, the grey state's own list mark. Not a tick: a tick marks a thing done.
 *
 * "Not required" rather than "Not needed": the other two answers are Required and Partial, and the
 * third is the same word turned round.
 */
export const NEED_BADGE: Record<CookieNeed, NeedBadge> = {
	required: { label: 'Required', state: 'failed', icon: 'lock' },
	partial: { label: 'Partial', state: 'blocked', icon: 'cookie' },
	not_needed: { label: 'Not required', state: 'queued', icon: 'playlist_add_check' }
};

/**
 * Whether a Site's row answers what was typed into the sheet's search.
 *
 * By its name and by the addresses that reach it, because somebody holding a link knows the
 * address and not always what Sift calls the Site: "twitter" finds X. Case and surrounding space
 * are not what anybody means.
 */
export function siteMatches(typed: string, name: string, hosts: readonly string[]): boolean {
	const wanted = typed.trim().toLowerCase();
	if (!wanted) return true;
	return [name, ...hosts].some((one) => one.toLowerCase().includes(wanted));
}

/**
 * The small line under a Site's name: when its cookies run out, and when they were last any use.
 *
 * The end is a day, in the one form every date in Sift takes: with its year, because an expiry
 * is a record of what the browser wrote, and "Ends September 25" on a jar that ends next year reads
 * as a fortnight away. The last use is how long ago, because this is a list somebody checks.
 *
 * Both halves can be missing and neither is invented. A jar made entirely of session cookies has
 * no expiry at all, and a Site nothing has been downloaded from since has no last use: saying
 * "Ends today" or "Last used just now" for either would be the one thing worse than saying nothing.
 *
 * `now` is a parameter for the reason every other relative sentence in this app takes one: a test
 * that reads the machine's clock reads a clock that can step backwards.
 */
export function saidAbout(row: CookieRow, now: number, site: string): string {
	// Only that nothing is saved. Whether this Site wants any is `NEED_BADGE`'s, from the catalog,
	// not a guess from download history.
	if (row.state === 'none') return `No cookies saved for ${site}.`;
	const ends =
		row.expires_at === null
			? 'No end date.'
			: `${row.state === 'expired' ? 'Ended' : 'Ends'} ${dayOf(row.expires_at)}.`;
	const used =
		row.last_used_at === null ? 'Not used yet.' : `Last used ${sayAgo(row.last_used_at, now)}.`;
	return `${ends} ${used}`;
}

/** Whether what was read is worth saving. The three answers below are the three this can be. */
export function readIsGood(read: CookiesRead): boolean {
	return read.cookies > 0 && !read.expired;
}

/**
 * What the box says about a file BEFORE it is saved.
 *
 * The whole of the honesty here. A cookie travels one way (it is sent and never comes back) so
 * the moment after Save there is nothing left to look at, and somebody who pasted the wrong file
 * would find out days later from a download that said the Site wanted signing in to. This is that
 * finding-out, moved to while they are still looking at the box they filled in.
 */
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
