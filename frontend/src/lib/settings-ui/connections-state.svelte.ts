/* A Site's saved cookies, and how the screens that show them ask the server. */

import { api, ApiError } from '$lib/api/client';
import { UNREACHABLE } from '$lib/shell/unreachable';
import { settingChanges, whenChanged } from '$lib/library/changes.svelte';
import type { components } from '$lib/api/schema';
import type { CookieRow, CookiesChecked, CookiesRead } from '$lib/components/downloads/cookies';

/** One saved row, as `GET /site-connections` answers it: the generated `ConnectionItem`, named
 * once as `CookieRow` (`components/downloads/cookies.ts`) for every reader. */
export type Connection = CookieRow;

/** One Site Sift knows how to download from, as `GET /supported-sites` answers. */
type SupportedSite = components['schemas']['SupportedSite'];

export class Connections {
	items = $state<CookieRow[]>([]);
	loaded = $state(false);
	problem = $state<string | null>(null);
	saveError = $state<string | undefined>(undefined);
	busy = $state(false);

	/** Every Site Sift can download from, for the rows that have no cookies saved yet. */
	sites = $state<SupportedSite[]>([]);

	/** Read the saved cookies again when a Site's are saved, checked or removed in another
	 * window: the server says each on the settings bell. */
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

	/** Save (or replace) the cookies for a Site. Returns a refusal to show under the box, or
	 * none. */
	async save(site: string, cookie: string): Promise<string | undefined> {
		this.busy = true;
		this.saveError = undefined;
		try {
			/* The answer is not kept. */
			await api.post<components['schemas']['SavedConnection']>('/site-connections', {
				body: { site, cookie }
			});
			await this.load();
			return undefined;
		} catch (error) {
			// A 409 here is the one worth its own words: the key that encrypts these is only in
			// memory after a password sign-in, so a cookie-only session is asked for the password.
			this.saveError = error instanceof ApiError ? (error.detail ?? error.message) : UNREACHABLE;
			return this.saveError;
		} finally {
			this.busy = false;
		}
	}

	/** What the server makes of a file, WITHOUT saving it. */
	async preview(site: string, cookie: string): Promise<CookiesRead | null> {
		try {
			return await api.post<CookiesRead>('/site-connections/preview', {
				body: { site, cookie }
			});
		} catch (error) {
			// A 400 IS the verdict: the server's reader could make nothing of the text, which is
			// exactly "that is not a cookie file", and it is answered as a read of nought cookies
			// so the box says so.
			if (error instanceof ApiError && error.status === 400) {
				return { cookies: 0, domains: [], expires_at: null, expired: false };
			}
			return null;
		}
	}

	/** Ask the Site itself whether it still accepts what is saved, and repeat what it said. */
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

	/** One line per Site: everything saved, and every supported Site with nothing saved. */
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

/** The name a Site is shown under, which is the supported list's, falling back to its own key. */
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
