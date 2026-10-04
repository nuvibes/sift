/* A Site's saved cookies, and how the screens that show them ask the server.
 *
 * A cookie only ever travels one way: it is sent when saved and never comes back. Nothing here
 * holds one after it is sent, no response carries one, and the list below knows only that cookies
 * exist for a Site, never what they are.
 *
 * ## The word is cookies
 *
 * The thing this file describes is not an account. Nobody gives Sift an account here (no
 * username, no password, nothing that could sign in anywhere on their behalf), they hand over
 * what their own browser already holds for one Site. So every surface, this one included, says
 * the one word for the one thing.
 */

import { api, ApiError } from '$lib/api/client';
import { UNREACHABLE } from '$lib/shell/unreachable';
import { settingChanges, whenChanged } from '$lib/library/changes.svelte';
import type { components } from '$lib/api/schema';
import type { CookieRow, CookiesChecked, CookiesRead } from '$lib/components/downloads/cookies';

/**
 * One saved row, as `GET /site-connections` answers it: the generated `ConnectionItem`, named
 * once as `CookieRow` (`components/downloads/cookies.ts`) for every reader.
 */
export type Connection = CookieRow;

/** One Site Sift knows how to download from, as `GET /supported-sites` answers. */
type SupportedSite = components['schemas']['SupportedSite'];

export class Connections {
	items = $state<CookieRow[]>([]);
	loaded = $state(false);
	problem = $state<string | null>(null);
	saveError = $state<string | undefined>(undefined);
	busy = $state(false);

	/** Every Site Sift can download from, for the rows that have no cookies saved yet.
	 *
	 *  Read beside the saved list rather than instead of it: a Site with nothing saved is the case
	 *  somebody opens this sheet FOR, and a list of only what is already there cannot show it. */
	sites = $state<SupportedSite[]>([]);

	/**
	 * Read the saved cookies again when a Site's are saved, checked or removed in another window:
	 * the server says each on the settings bell. Called by the screen that holds this list, while
	 * it sets up.
	 */
	follow(): void {
		whenChanged(settingChanges, () => {
			if (this.loaded) void this.load();
		});
	}

	async load(): Promise<void> {
		try {
			const [items, sites] = await Promise.all([
				api.get<CookieRow[]>('/site-connections'),
				api.get<SupportedSite[]>('/supported-sites')
			]);
			this.items = items;
			this.sites = sites;
			this.problem = null;
		} catch (error) {
			this.problem = error instanceof ApiError ? error.message : UNREACHABLE;
		}
		this.loaded = true;
	}

	/** Save (or replace) the cookies for a Site. Returns a refusal to show under the box, or none.
	 *
	 *  A file that could not be read is refused HERE, while somebody is looking at the box they
	 *  just filled in, rather than by a download days later, which reports it as the Site wanting
	 *  cookies and sends you to replace ones that were never the problem. */
	async save(site: string, cookie: string): Promise<string | undefined> {
		this.busy = true;
		this.saveError = undefined;
		try {
			/* The answer is not kept. The read-back comes BEFORE Save, in the sheet's own words
			   (`readBack` in `components/downloads/cookies.ts`), so a description written
			   afterwards would be a second wording of a sentence somebody has already read. The
			   request still asks for the shape, because a save that could not be understood is a
			   refusal and the refusal is what this returns. */
			await api.post<components['schemas']['SavedConnection']>('/site-connections', {
				body: { site, cookie }
			});
			await this.load();
			return undefined;
		} catch (error) {
			// A 409 here is the one worth its own words: the key that encrypts these is only in
			// memory after a password sign-in, so a cookie-only session is asked for the password.
			//
			// SET as well as returned. The field below is drawn from `saveError`, so a refusal only
			// returned would be handed back to a caller that has nowhere to put it, and the screen
			// would simply do nothing.
			this.saveError = error instanceof ApiError ? (error.detail ?? error.message) : UNREACHABLE;
			return this.saveError;
		} finally {
			this.busy = false;
		}
	}

	/** What the server makes of a file, WITHOUT saving it. The read-back in front of Save.
	 *
	 *  A separate request rather than a save that can be undone: an undo leaves the cookies on disk
	 *  for as long as it takes somebody to decide, and the whole point of the read-back is that
	 *  nothing has been kept yet. */
	async preview(site: string, cookie: string): Promise<CookiesRead | null> {
		try {
			return await api.post<CookiesRead>('/site-connections/preview', {
				body: { site, cookie }
			});
		} catch (error) {
			// A 400 IS the verdict: the server's reader could make nothing of the text, which is
			// exactly "that is not a cookie file", and it is answered as a read of nought cookies
			// so the box says so. Swallowing it would leave the box silent on the one thing this
			// screen exists to be honest about. Anything else (the server busy, the network gone)
			// stays silent, because a read-back that could not be taken is not a refusal of the
			// file.
			if (error instanceof ApiError && error.status === 400) {
				return { cookies: 0, domains: [], expires_at: null, expired: false };
			}
			return null;
		}
	}

	/** Ask the Site itself whether it still accepts what is saved, and repeat what it said.
	 *
	 *  A Site can stop accepting a session at any time (a password change, a sign-out somewhere
	 *  else, a block), and none of that touches the expiry date, so the only way to know is to
	 *  have tried. The server makes the request and writes the sentence, because the server is what
	 *  made it; a client guessing at the words would be a second opinion about a request it never
	 *  saw. */
	async check(id: string): Promise<CookiesChecked> {
		this.busy = true;
		try {
			const answer = await api.post<CookiesChecked>(`/site-connections/${id}/check`);
			await this.load();
			return answer;
		} catch (error) {
			return {
				accepted: false,
				said: error instanceof ApiError ? (error.detail ?? error.message) : UNREACHABLE
			};
		} finally {
			this.busy = false;
		}
	}

	/**
	 * One line per Site: everything saved, and every supported Site with nothing saved.
	 *
	 * The union rather than the saved list, because the Site somebody came here to add is by
	 * definition the one with no row. Saved first and in the server's own order, then the rest by
	 * name, so a list that is mostly empty rows still opens on what is actually there.
	 *
	 * ONE ROW PER SITE, matched the way the server's own lookup matches: without case
	 * (`_CONNECTION_BY_SITE`, `COLLATE NOCASE`). A saved row names its Site as the library files it
	 * ("Quillhouse") while the supported list keys it "quillhouse", and comparing the two exactly
	 * would draw the Site twice: once as saved, and once more as having nothing saved. A saved row is
	 * rewritten here onto the supported Site's own key, so everything downstream (the sheet's badges, the add
	 * form it opens) reads one Site by one key.
	 */
	get rows(): { row: CookieRow; name: string }[] {
		const saved = this.items.map((item) => {
			const known = siteFor(item.site, this.sites);
			return {
				row: known ? { ...item, site: known.key } : item,
				name: known?.name ?? item.site
			};
		});
		const have = new Set(saved.map((entry) => folded(entry.row.site)));
		const rest = this.sites
			.filter((site) => !have.has(folded(site.key)))
			.map((site) => ({
				row: {
					id: '',
					site: site.key,
					state: 'none' as const,
					expires_at: null,
					expires_last: null,
					last_used_at: null,
					status: null,
					updated_at: null
				},
				name: site.name
			}))
			.sort((one, other) => one.name.localeCompare(other.name));
		return [...saved, ...rest];
	}

	async remove(connection: CookieRow): Promise<void> {
		this.busy = true;
		try {
			await api.del(`/site-connections/${connection.id}`);
			await this.load();
		} finally {
			this.busy = false;
		}
	}
}

/** The name a Site is shown under, which is the supported list's, falling back to its own key.
 *
 * The same name the tunnel routing and the queue draw, rather than a second prettifier here: two
 * places deciding what to call one Site is how one Site comes to have two names. */
export function nameOf(key: string, sites: readonly SupportedSite[]): string {
	return siteFor(key, sites)?.name ?? key;
}

/** A Site's key or name as the server compares them: without case. */
function folded(word: string): string {
	return word.trim().toLowerCase();
}

/** The supported Site a key or a filed name stands for, compared the way the server compares. */
function siteFor(word: string, sites: readonly SupportedSite[]): SupportedSite | undefined {
	const wanted = folded(word);
	return sites.find((site) => folded(site.key) === wanted || folded(site.name) === wanted);
}
