/**
 * Asking the stash-boxes about a selection of people, sites or tags.
 *
 * ## Why this is one function and not three
 *
 * Three walls offer the verb and every one of them would otherwise write the same six lines: post
 * the ids, say what was queued, say so if it failed. Three copies of a sentence is how one of them
 * comes to say "asking about 12" while another says "started", and the one that drifts is the
 * wall nobody uses.
 *
 * ## Why it queues rather than waits
 *
 * Each subject is one request to somebody else's service, paced at a few a second, so forty of
 * them is most of a minute. The route queues a job and returns; this says where to watch it. A
 * screen that waited would look broken for the whole of it and would take the tab with it.
 */
import { ApiError, api } from '$lib/api/client';
import { reasonFor } from '$lib/library/bulk';
import { filesSaid } from '$lib/entity/entity-counts';
import { toasts } from '$lib/shell/toasts.svelte';
import { place, thing, type ToastWords } from '$lib/components/common/toast-pieces';
import type { components } from '$lib/api/schema';

/**
 * What the server said about a refusal, when it said something worth repeating.
 *
 * "Those could not be asked about" for every failure would throw away the one refusal that
 * actually happens: matching against the stash-boxes is switched OFF in
 * Settings, and the server says exactly that in a sentence somebody can act on. A generic message
 * over an actionable one turns a setting somebody has to change into a feature that looks broken.
 */
function why<T extends ToastWords>(error: unknown, fallback: T): string | T {
	return error instanceof ApiError && error.detail ? error.detail : fallback;
}

const WATCH = [' Watch it in ', place('Activity', '/settings/tasks?show=now'), '.'];

/** What kind of thing is being enriched. The server names the same three. */
export type EnrichSubject = 'person' | 'site' | 'tag';

/**
 * Ask about these, and say what happened.
 *
 * The count comes back from the SERVER rather than being the length of what was sent: an id that
 * has gone since the wall drew it, or one this account may not be shown, is dropped there. Saying
 * "asking about 14" over a job that will ask about 12 is a small lie that makes the job list look
 * wrong.
 */
export async function enrichMany(subject: EnrichSubject, ids: string[], box = ''): Promise<void> {
	if (ids.length === 0) return;
	try {
		const started = await api.post<components['schemas']['EnrichStarted']>('/stash-boxes/enrich', {
			// The flyout's answer: a box's word, `EVERY_BOX` for every switched-on box, or nothing
			// for whatever Settings says ("Auto-enrich using"). Sent only when a box was named: a
			// key saying "" is the same answer as no key, spelled as a value.
			body: { subject, ids, ...(box ? { box } : {}) }
		});
		const asked = started.asked ?? ids.length;
		const about = asked === 1 ? 'it' : asked.toLocaleString();
		toasts.show([`Asking the stash-boxes about ${about}.`, ...WATCH], { tone: 'success' });
	} catch (error) {
		toasts.show(why(error, "Those couldn't be asked about"), { tone: 'error' });
	}
}

/**
 * Ask the stash-boxes about the FILES in one folder, and everything under it.
 *
 * A different question from the one above, which is why it is a different route rather than a
 * fourth subject. That one asks what a PERSON or a site or a tag is: a name looked up in a
 * stash-box. This one asks what each FILE is, by the fingerprints Sift already holds, which is the
 * same sweep the button in Settings runs over the whole library with a folder named instead.
 *
 * The counterpart to "Scan this folder" in the same menu, and deliberately not called that:
 * scanning reads a disk, enriching asks somebody else's stash-box. One word for both would make a
 * menu item and a settings button mean entirely different things.
 *
 * It is AUTO-enrich, and the press is the consent: an exact match is written the moment it comes
 * back, and anything less certain waits under Organize to be agreed with.
 */
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
const LOOKED_UP_AT_ONCE = 500;

/**
 * Ask AcoustID which song each of these files is: the lookup task, pressed for THESE files.
 *
 * Its own route rather than a box on the stash-box scan, because it is not a stash-box: it asks by
 * the sound, under its own switch and its own key (`Settings > Music`), and what comes back is a
 * song rather than a scene. The sentence on success is the server's (`said`): it knows how many
 * were queued, how many it left out and why, which this side cannot.
 *
 * Refused in the server's words while the lookup is off or has no key, so the toast names the
 * setting rather than saying the press failed.
 *
 * `again` is Ask AcoustID again: the same press for files AcoustID was asked about and did not
 * know, at any age, because a person chose these files. A file it did know is left alone, and
 * the server says so.
 */
export async function lookUpSongs(ids: string[], again = false): Promise<void> {
	if (ids.length === 0) return;
	try {
		/* Five hundred a press, the route's own ceiling and every bulk write's: a bigger selection
		   is several presses, each its own walk on Activity, and each says what it queued. */
		for (let start = 0; start < ids.length; start += LOOKED_UP_AT_ONCE) {
			const pressed = await api.post<components['schemas']['LookupPressed']>(
				'/music/lookup/files',
				{
					body: {
						asset_ids: ids.slice(start, start + LOOKED_UP_AT_ONCE),
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
const NAMED_AT_ONCE = 1000;

type Asked = Omit<components['schemas']['ScanStarted'], 'job_id' | 'started'>;

/**
 * A selection of any size, asked about a thousand at a time and answered as one.
 *
 * A part refused whole (every file in it hidden or kept local) is counted as left out with its
 * reason, as `overChunks` does; only when nothing at all was asked about is the refusal thrown.
 */
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
	for (let at = 0; at < ids.length; at += NAMED_AT_ONCE) {
		const part = ids.slice(at, at + NAMED_AT_ONCE);
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

/**
 * Ask the stash-boxes what these FILES are, by the fingerprints Sift already holds.
 *
 * The grid's counterpart to `enrichMany` above, and a different question from it: that one asks
 * what a NAME is, this one asks what the bytes are. A stash-box can recognize a video by its
 * fingerprint without being told anything about it, which is the whole reason the file half exists.
 *
 * Answers with the refusal rather than only showing one, so a caller with somewhere better to put
 * it can. The chooser on one file has a whole sheet to say it in, and "the stash-boxes are switched
 * off in Settings" reported there as "no stash-box recognized this file" is a lie about the one
 * thing the sheet exists to tell somebody.
 *
 * It does not open a chooser. Each file becomes one queued question, and what comes back joins the
 * pile under Organize where a match is shown beside what is already on the file and a page of them
 * is agreed or refused in one press.
 *
 * `auto` is the Auto-enrich press, and the press is the consent: an EXACT match is written as it
 * comes back, the way the same verb links a person's one sure match. Anything less certain still
 * waits in the pile, a field the file already holds differently is still a question on the confirm
 * screen, and nothing is invented unless Settings allows it. Without `auto` it is Enrich, and every
 * answer waits.
 */
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
		/*
		 * THE COUNT IS THE SERVER'S, AND SO IS WHAT WAS LEFT OUT.
		 *
		 * A file this account cannot open is not asked about (a concealed one never leaves the
		 * machine, which is the whole point of the vault), and saying the stash-boxes were asked
		 * about everything would then be untrue. Where NOTHING could be asked about the server
		 * refuses outright and this is the catch below, so the only case here is a genuine partial:
		 * say how many went and why the rest did not.
		 */
		const asked = started.asked || ids.length;
		if (started.skipped > 0 && started.reason) {
			// Worded for how many were LEFT OUT, not for how many went. `reasonFor` is the same
			// picker the bulk toasts use, and the reply carries both wordings for the same reason:
			// this sentence sits beside a count only this side knows. See `$lib/library/bulk`.
			const why = reasonFor(started, started.skipped) ?? started.reason;
			if (!quiet) {
				// Nothing failed where the vault or "Do not enrich" held a file back: each did exactly
				// what it was asked to do, the same reasoning `announceRefusal` gives for its tone.
				// Read off the server's FLAGS, never its sentence, so rewording one moves nothing here.
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
