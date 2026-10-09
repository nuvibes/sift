/* What has built up in a library, as the Maintenance screen asks the server about it. */

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
	/** True once a load has succeeded. An attempt that failed leaves this false, so the screen
	   says it could not look rather than that there is nothing to do. */
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

	/** Ask for the counts that read the disk to be taken. */
	async survey(): Promise<void> {
		this.problem = null;
		try {
			await api.post<components['schemas']['SurveyStarted']>('/tidy/survey', { body: {} });
			this.surveying = true;
		} catch (error) {
			this.problem = refusal(error);
		}
	}

	/* --- settling the database down ----------------------------------------------------------- */

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

	/* --- making every picture again
	 * ------------------------------------------------------------ */

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
			// A count that could not be read is left unknown.
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

	/* --- bringing the hover clips up to the shape that is set
	 * ---------------------------------- */

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

	/* Everything, with what there is to do first. */
	get worthDoing(): Leftovers[] {
		return [
			...this.leftovers.filter((one) => (one.count ?? 0) > 0),
			...this.leftovers.filter((one) => !((one.count ?? 0) > 0))
		];
	}

	/** Whether anything at all has built up. What the screen says when nothing has. */
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
