/* What has built up in a library, as the Maintenance screen asks the server about it.
 *
 * Two requests and no cleverness: read what is there, and run one of them by name. The counts are
 * never worked out here: a client that computed them would be a second answer to the same
 * question, and the one it showed would be the one nobody could check.
 *
 * Every run comes back with a fresh survey attached, because removing rows strands the files those
 * rows named. The numbers genuinely move in ways nothing here could predict, so they are re-read
 * rather than adjusted.
 *
 * Two of the counts read a whole directory off the disk, so the server does not take them when
 * this screen opens: it answers with the last survey and when it was taken, or with no count at
 * all before there has been one. Asking for a survey queues the count as a job, and the screen
 * re-reads when the queue moves.
 */

import { api, ApiError } from '$lib/api/client';
import { UNREACHABLE } from '$lib/shell/unreachable';
import type { components } from '$lib/api/schema';

/* LIVE: followed by lib/settings-ui/Maintenance.svelte (load on the jobs bell) */

export type Leftovers = components['schemas']['LeftoversView'];

function refusal(error: unknown): string {
	return error instanceof ApiError ? (error.detail ?? error.message) : UNREACHABLE;
}

export class Tidy {
	leftovers = $state<Leftovers[]>([]);
	/** True once a load has succeeded. An attempt that failed leaves this false, so the screen says
	    it could not look rather than that there is nothing to do. */
	loaded = $state(false);
	loading = $state(false);
	problem = $state<string | null>(null);
	busy = $state(false);

	/** What the last run removed, kept so the screen can say so afterwards. */
	lastRun = $state<{ title: string; removed: number; noun: string; nouns: string } | null>(null);

	/** Whether a survey of the counts that read the disk is waiting or under way. */
	surveying = $state(false);

	/** When the counts that read the disk were last taken (the oldest of them, since one survey
	    takes them all), or null while none has been. */
	get lastSurveyed(): number | null {
		const times = this.leftovers
			.map((one) => one.surveyed_at)
			.filter((at): at is number => at !== null && at !== undefined);
		return times.length ? Math.min(...times) : null;
	}

	/** Whether any count is still waiting for its first survey. */
	get unsurveyed(): boolean {
		return this.leftovers.some((one) => one.count === null);
	}

	/** Ask for the counts that read the disk to be taken. A press while one is already going is
	    answered rather than doubled, and reads the same on screen. */
	async survey(): Promise<void> {
		this.problem = null;
		try {
			await api.post<components['schemas']['SurveyStarted']>('/tidy/survey', { body: {} });
			this.surveying = true;
		} catch (error) {
			this.problem = refusal(error);
		}
	}

	/* --- settling the database down -----------------------------------------------------------
	 *
	 * Kept apart from the tidyings above and not folded in with them, because a tidying REMOVES
	 * something and this removes nothing. Every row survives; only the disk the file takes and how
	 * SQLite chooses its indexes change. Listing it among things that delete data would be inviting
	 * somebody to read it as one of them. */

	/** What the last optimize freed, so the screen can report a change nobody can otherwise see. */
	optimized = $state<{ freed: number; now: number } | null>(null);

	async optimize(): Promise<string | undefined> {
		this.busy = true;
		try {
			const answer = await api.post<components['schemas']['OptimizeResult']>(
				'/tidy/database/optimize',
				{ body: {} }
			);
			this.optimized = { freed: answer.freed_bytes, now: answer.now_bytes };
			return undefined;
		} catch (error) {
			return refusal(error);
		} finally {
			this.busy = false;
		}
	}

	/* --- making every picture again ------------------------------------------------------------
	 *
	 * The count is asked for on load and the run is a second request, for the reason the survey
	 * above is separate from the tidying: this is minutes of the machine on a large library, and a
	 * control whose size is only visible after it has started is one nobody can use carefully. */

	/** How many files a rebuild would touch, or null while that has not been asked. */
	rebuildable = $state<number | null>(null);
	/** How many jobs the last rebuild queued, so the screen can say it happened. */
	rebuilding = $state<number | null>(null);

	async countRebuildable(): Promise<void> {
		try {
			this.rebuildable = (
				await api.get<components['schemas']['Rebuilding']>('/jobs/rebuild-thumbnails')
			).total;
		} catch {
			// A count that could not be read is left unknown. The row says so and offers no button,
			// which is better than offering one over a number nobody has.
			this.rebuildable = null;
		}
	}

	async rebuildThumbnails(): Promise<string | undefined> {
		this.busy = true;
		try {
			this.rebuilding = (
				await api.post<components['schemas']['Rebuilding']>('/jobs/rebuild-thumbnails', {
					body: {}
				})
			).queued;
			return undefined;
		} catch (error) {
			return refusal(error);
		} finally {
			this.busy = false;
		}
	}

	/* --- bringing the hover clips up to the shape that is set ----------------------------------
	 *
	 * Its own count and its own run, rather than folding into the rebuild above. They answer
	 * different questions (that one is "every picture is out of date", this one is "the clips
	 * are a different length from the one you chose"), and a single button doing both would make
	 * changing a preference cost a full re-thumbnail of the library. */

	/** How many files have a hover clip of another shape, or null while that has not been asked. */
	restyleable = $state<number | null>(null);
	/** Whether the last press was accepted, so the screen can say it happened. */
	restyling = $state(false);

	async countRestyleable(): Promise<void> {
		try {
			this.restyleable = (
				await api.get<components['schemas']['Rebuilding']>('/jobs/rebuild-previews')
			).total;
		} catch {
			// Left unknown, like the count above: a row that says so and offers no button beats one
			// offering a button over a number nobody has.
			this.restyleable = null;
		}
	}

	async rebuildPreviews(): Promise<string | undefined> {
		this.busy = true;
		try {
			await api.post<components['schemas']['Rebuilding']>('/jobs/rebuild-previews', { body: {} });
			this.restyling = true;
			return undefined;
		} catch (error) {
			return refusal(error);
		} finally {
			this.busy = false;
		}
	}

	async load(): Promise<void> {
		this.loading = true;
		this.problem = null;
		try {
			const answer = await api.get<components['schemas']['TidyView']>('/tidy');
			this.leftovers = answer.leftovers;
			this.surveying = answer.surveying;
			this.loaded = true;
			// Alongside, not before: the tidy list is what this screen is mostly about, and a slow
			// count of the library must not hold it back.
			void this.countRebuildable();
			void this.countRestyleable();
		} catch (error) {
			this.problem = refusal(error);
		} finally {
			this.loading = false;
		}
	}

	/* Everything, with what there is to do first.
	 *
	 * Every kind is drawn even at zero: showing only what has built up would make a tidying with
	 * nothing to remove indistinguishable from one that does not exist, and somebody looking for
	 * the control that clears a particular thing would find an empty space and no way to tell
	 * whether they were looking in the wrong place.
	 *
	 * The ones with something to do are put first, so the screen still reads as a work list
	 * rather than as an inventory. Ordering is stable within each half (the registry's own
	 * order, which is rows before files), so nothing jumps around between reads.
	 */
	get worthDoing(): Leftovers[] {
		return [
			...this.leftovers.filter((one) => (one.count ?? 0) > 0),
			...this.leftovers.filter((one) => !((one.count ?? 0) > 0))
		];
	}

	/** Whether anything at all has built up. What the screen says when nothing has. A count not
	    yet taken is not something built up: it is not known either way. */
	get anythingToDo(): boolean {
		return this.leftovers.some((one) => (one.count ?? 0) > 0);
	}

	async run(one: Leftovers): Promise<void> {
		this.busy = true;
		this.problem = null;
		try {
			const answer = await api.post<components['schemas']['TidyResult']>(`/tidy/${one.name}`);
			this.leftovers = answer.leftovers;
			this.surveying = answer.surveying;
			this.lastRun = {
				title: one.title,
				removed: answer.removed,
				noun: one.noun,
				nouns: one.nouns
			};
		} catch (error) {
			this.problem = refusal(error);
		} finally {
			this.busy = false;
		}
	}
}
