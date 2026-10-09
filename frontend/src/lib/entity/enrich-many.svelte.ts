/** Asking the stash-boxes about a selection of people, sites or tags. */
import { ApiError, api } from '$lib/api/client';
import { reasonFor } from '$lib/library/bulk';
import { filesSaid } from '$lib/entity/entity-counts';
import { toasts } from '$lib/shell/toasts.svelte';
import { place, thing, type ToastWords } from '$lib/components/common/toast-pieces';
import type { components } from '$lib/api/schema';

/** What the server said about a refusal, when it said something worth repeating. */
function why<T extends ToastWords>(error: unknown, fallback: T): string | T {
	return error instanceof ApiError && error.detail ? error.detail : fallback;
}

const WATCH = [' Watch it in ', place('Activity', '/settings/tasks?show=now'), '.'];

/** What kind of thing is being enriched. The server names the same three. */
export type EnrichSubject = 'person' | 'site' | 'tag';

/** Ask about these, and say what happened. */
export async function enrichMany(subject: EnrichSubject, ids: string[], box = ''): Promise<void> {
	if (ids.length === 0) return;
	try {
		const started = await api.post<components['schemas']['EnrichStarted']>('/stash-boxes/enrich', {
			// The flyout's answer: a box's word, `EVERY_BOX` for every switched-on box, or nothing
			// for whatever Settings says ("Auto-enrich using").
			body: { subject, ids, ...(box ? { box } : {}) }
		});
		const asked = started.asked ?? ids.length;
		const about = asked === 1 ? 'it' : asked.toLocaleString();
		toasts.show([`Asking the stash-boxes about ${about}.`, ...WATCH], { tone: 'success' });
	} catch (error) {
		toasts.show(why(error, "Those couldn't be asked about"), { tone: 'error' });
	}
}

/** Ask the stash-boxes about the FILES in one folder, and everything under it. */
export async function enrichFolder(folderId: string, name: string): Promise<void> {
	const folder = thing('folder', folderId, name);
	try {
		await api.post('/stash-boxes/scan', { body: { folder: folderId, auto: true } });
		toasts.show(['Asking the stash-boxes about what is in ', folder, '.', ...WATCH], {
			tone: 'success'
		});
	} catch (error) {
		toasts.show(why(error, ["Sift couldn't ask about ", folder]), { tone: 'error' });
	}
}

/** The most files one press of the lookup names. The server refuses more, with the same figure. */
const LOOKED_UP_PER_ASK = 500;

/** Ask AcoustID which song each of these files is: the lookup task, pressed for THESE files. */
export async function lookUpSongs(ids: string[], again = false): Promise<void> {
	if (ids.length === 0) return;
	try {
		/* Five hundred a press, the route's own ceiling and every bulk write's: a bigger selection
		   is several presses, each its own walk on Activity, and each says what it queued. */
		for (let start = 0; start < ids.length; start += LOOKED_UP_PER_ASK) {
			const pressed = await api.post<components['schemas']['LookupPressed']>(
				'/music/lookup/files',
				{
					body: {
						asset_ids: ids.slice(start, start + LOOKED_UP_PER_ASK),
						...(again ? { again: true } : {})
					}
				}
			);
			toasts.show(pressed.said, { tone: pressed.queued > 0 ? 'success' : 'info' });
		}
	} catch (error) {
		toasts.show(why(error, "Sift couldn't ask AcoustID about those"), { tone: 'error' });
	}
}

/** The most files one ask names: the route's own ceiling, the most one Select all picks. */
const NAMED_PER_ASK = 1000;

type Asked = Omit<components['schemas']['ScanStarted'], 'job_id' | 'started'>;

/** A selection of any size, asked about a thousand at a time and answered as one. */
async function askInChunks(ids: string[], options: object): Promise<Asked> {
	const done: Asked = {
		asked: 0,
		skipped: 0,
		reason: null,
		reason_many: null,
		vault_locked: false,
		kept_local: false
	};
	let refused: unknown = null;
	for (let at = 0; at < ids.length; at += NAMED_PER_ASK) {
		const part = ids.slice(at, at + NAMED_PER_ASK);
		try {
			const one = await api.post<components['schemas']['ScanStarted']>('/stash-boxes/scan', {
				body: { assets: part, ...options }
			});
			done.asked += one.asked || part.length;
			done.skipped += one.skipped;
			done.reason ??= one.reason;
			done.reason_many ??= one.reason_many;
			done.vault_locked ||= one.vault_locked;
			done.kept_local ||= one.kept_local;
		} catch (error) {
			refused ??= error;
			done.skipped += part.length;
			done.reason ??= why(error, "Those couldn't be asked about");
			done.vault_locked ||= error instanceof ApiError && error.status === 423;
		}
	}
	if (done.asked === 0 && refused !== null) throw refused;
	return done;
}

/** Ask the stash-boxes what these FILES are, by the fingerprints Sift already holds. */
export async function enrichFiles(
	ids: string[],
	{ quiet = false, box = '', auto = false, again = false } = {}
): Promise<string | null> {
	if (ids.length === 0) return null;
	try {
		// `box` is the flyout's answer: this stash-box, or empty for every switched-on one.
		const started = await askInChunks(ids, {
			...(box ? { box } : {}),
			...(auto ? { auto } : {}),
			...(again ? { again } : {})
		});
		/* THE COUNT IS THE SERVER'S, AND SO IS WHAT WAS LEFT OUT. */
		const asked = started.asked || ids.length;
		if (started.skipped > 0 && started.reason) {
			// Worded for how many were LEFT OUT, not for how many went.
			const why = reasonFor(started, started.skipped) ?? started.reason;
			if (!quiet) {
				// Nothing failed where the vault or "Do not enrich" held a file back: each did
				// exactly what it was asked to do, the same reasoning `announceRefusal` gives for
				// its tone.
				const honoured = started.vault_locked || started.kept_local;
				// Grouped the way every other count a person reads is (`entity-counts`): "Asking
				// about 996 of 1000" beside a bar saying "1,000 files selected" would be the same
				// number written two ways on one screen.
				toasts.show(
					`Asking about ${asked.toLocaleString()} of ${ids.length.toLocaleString()}. ${why}`,
					{
						tone: honoured ? 'info' : 'error',
						icon: started.vault_locked ? 'lock' : started.kept_local ? 'shield' : undefined
					}
				);
			}
			return why;
		}
		if (!quiet) {
			// Auto-enrich says what lands on its own and what waits; Enrich says everything waits.
			const lands = auto
				? 'Exact matches are filled in. Anything less certain waits under '
				: 'What comes back waits under ';
			const about = asked === 1 ? 'it' : filesSaid(asked);
			toasts.show(
				[`Asking the stash-boxes about ${about}. `, lands, place('Organize', '/organize'), '.'],
				{ tone: 'success' }
			);
		}
		return null;
	} catch (error) {
		const said = why(error, "Those couldn't be asked about");
		if (!quiet) toasts.show(said, { tone: 'error' });
		return said;
	}
}
