/* The stash-boxes: the stash-boxes Sift can ask about a person, a site or a file.
 *
 * Their own name, used literally, singular and plural. The three Sift is known to work with are
 * named individually wherever they are listed; there is no collective noun for them invented here
 * or anywhere else in the interface.
 *
 * A key is write-only. It is typed in and sent, and it is never rendered back, never put in the
 * DOM and never held in this store: a box that has one shows that it has one, and nothing more.
 * That is the same shape a site login already has, and it is the reason neither can leak from a
 * screenshot or a saved page.
 */

import { api, ApiError } from '$lib/api/client';
import { settingChanges, whenChanged } from '$lib/library/changes.svelte';
import { UNREACHABLE } from '$lib/shell/unreachable';
import { forgetEnrichBoxes } from '$lib/entity/enrichment.svelte';
import type { components } from '$lib/api/schema';

export type StashBox = components['schemas']['BoxResponse'];

/** One subject as a stash-box describes it, already in Sift's words. */
export type FoundRecord = components['schemas']['RecordFound'];

/** What one box said. A box that could not be asked answers with a sentence and no records. */
export type BoxAnswer = components['schemas']['BoxAnswer'];

/*
 * The three Sift has been checked against, offered as a starting point.
 *
 * A preset is an address and a name and nothing else: there is no code path per box, because all
 * three run the same software and answer the same queries. Anything else can be typed in, which is
 * why this is a convenience rather than a list of what is allowed.
 */
/* The consent switch, by the key the server declared it under.
 *
 * A constant because three screens name it (the enrichment block, the tagger's empty state
 * and the first-run flow), and a settings key typed out at each of them is a key that silently
 * reads `undefined` the day one of them is misspelt. */
export const STASH_SCAN_KEY = 'stash_boxes.scan';

export const KNOWN_BOXES = [
	{ name: 'StashDB', endpoint: 'https://stashdb.org/graphql' },
	{ name: 'FansDB', endpoint: 'https://fansdb.cc/graphql' },
	{ name: 'PMVStash', endpoint: 'https://pmvstash.org/graphql' }
] as const;

function said(error: unknown): string {
	if (error instanceof ApiError) return error.detail ?? error.message;
	return UNREACHABLE;
}

export class StashBoxes {
	#items = $state<StashBox[]>([]);
	#problem = $state<string | null>(null);
	#busy = $state(false);

	get items(): StashBox[] {
		return this.#items;
	}

	get problem(): string | null {
		return this.#problem;
	}

	get busy(): boolean {
		return this.#busy;
	}

	/**
	 * Read the boxes again when a setting moves: a box added, switched, re-routed or removed in
	 * another window, or by another admin, is said on the settings bell. Called by the component
	 * that holds this list, while it sets up.
	 */
	follow(): void {
		whenChanged(settingChanges, () => void this.load());
	}

	async load(): Promise<void> {
		// Every write in this class reloads, so this is the one place a box is ever added, removed,
		// switched or renamed. The Enrich flyout's list is held elsewhere and would otherwise go on
		// offering a box that has just been removed until the tab was reloaded.
		forgetEnrichBoxes();
		try {
			const answer = await api.get<components['schemas']['BoxList']>('/stash-boxes');
			this.#items = answer.boxes;
			this.#problem = null;
		} catch (error) {
			// An admin who is not signed in is not a broken screen; every other failure is worth a
			// sentence, because this pane is where somebody comes to find out why nothing works.
			this.#problem = said(error);
			this.#items = [];
		}
	}

	async add(box: {
		name: string;
		endpoint: string;
		api_key: string | null;
	}): Promise<string | undefined> {
		this.#busy = true;
		try {
			await api.post('/stash-boxes', { body: box });
			await this.load();
			return undefined;
		} catch (error) {
			return said(error);
		} finally {
			this.#busy = false;
		}
	}

	async setEnabled(id: string, enabled: boolean): Promise<string | undefined> {
		return this.#write(id, { enabled });
	}

	async setPace(id: string, requests_per_minute: number): Promise<string | undefined> {
		return this.#write(id, { requests_per_minute });
	}

	/** Send this box's questions through a tunnel, or `direct` for this machine's own connection. */
	async setRoute(id: string, route: string | null): Promise<string | undefined> {
		return this.#write(id, { route });
	}

	async replaceKey(id: string, api_key: string): Promise<string | undefined> {
		try {
			await api.put(`/stash-boxes/${id}/key`, { body: { api_key } });
			await this.load();
			return undefined;
		} catch (error) {
			return said(error);
		}
	}

	async forget(id: string): Promise<string | undefined> {
		try {
			await api.del(`/stash-boxes/${id}`);
			await this.load();
			return undefined;
		} catch (error) {
			return said(error);
		}
	}

	/** Throw away what one box has said, so the next question is asked for real. */
	async forgetAnswers(id: string): Promise<string | undefined> {
		try {
			await api.del(`/stash-boxes/${id}/answers`);
			return undefined;
		} catch (error) {
			return said(error);
		}
	}

	/** Ask a box the smallest real question there is. Returns the problem, or nothing. */
	async check(id: string): Promise<string | undefined> {
		try {
			const answer = await api.post<components['schemas']['CheckResult']>(
				`/stash-boxes/${id}/check`
			);
			return answer.ok ? undefined : (answer.problem ?? "It didn't answer.");
		} catch (error) {
			return said(error);
		}
	}

	/** What every switched-on box knows about a name. Reads only; writes nothing anywhere. */
	async lookUp(term: string): Promise<BoxAnswer[]> {
		const answer = await api.get<components['schemas']['LookUpResult']>('/stash-boxes/look-up', {
			query: { term }
		});
		return answer.answers;
	}

	/** What the boxes make of one file, from hashes Sift already holds. */
	async recognise(assetId: string): Promise<BoxAnswer[]> {
		const answer = await api.get<components['schemas']['LookUpResult']>(
			`/stash-boxes/recognise/${assetId}`
		);
		return answer.answers;
	}

	async #write(id: string, body: Record<string, unknown>): Promise<string | undefined> {
		try {
			await api.put(`/stash-boxes/${id}`, { body });
			await this.load();
			return undefined;
		} catch (error) {
			return said(error);
		}
	}
}
