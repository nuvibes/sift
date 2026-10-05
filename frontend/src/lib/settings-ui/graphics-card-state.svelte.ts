/* The graphics card, as Performance asks the server about it: one reading shared by the block that
 * describes the card and the danger row at the foot of the pane, so deleting the support from the
 * one is what the other draws the moment it happens.
 *
 * What the block says in each state, and why there are five, is written beside the block in
 * `GraphicsCard.svelte`.
 */
import { api, ApiError } from '$lib/api/client';
import { settingChanges, whenChanged } from '$lib/library/changes.svelte';
import { accelWatch } from '$lib/jobs/accelerator.svelte';
import type { components } from '$lib/api/schema';
import { COPY } from './GraphicsCard.search';

type Accelerator = components['schemas']['AcceleratorView'];

/* Just past the server's own two minutes for the test, so its sentence arrives first. */
const TEST_LIMIT_MS = 150_000;

export class GraphicsCardState {
	/* Named `accel`, not `state`: a field called `state` reads as the `$state` rune beside it. */
	accel = $state<Accelerator | null>(null);
	testing = $state(false);
	tested = $state<{ works: boolean; problem: string | null } | null>(null);
	removing = $state(false);

	/** The support is on this device and can be deleted: installed, and no download running. */
	readonly removable = $derived(this.accel?.installed === true && !accelWatch.running);

	/** Read the card again when a setting moves; called by the screen that holds this. */
	follow(): void {
		whenChanged(settingChanges, () => void this.read());
	}

	async read(): Promise<void> {
		try {
			this.accel = await api.get<Accelerator>('/performance/accelerator');
		} catch {
			// A failed read says nothing about the card. Drawing "no card" here would be a confident
			// wrong answer about somebody's machine, so the block draws nothing at all.
			this.accel = null;
		}
	}

	async install(): Promise<void> {
		this.tested = null;
		try {
			const started = await api.post<Accelerator>('/performance/accelerator');
			this.accel = started;
			if (started.job_id) accelWatch.follow(started.job_id);
		} catch (error) {
			accelWatch.couldNotStart(
				error instanceof ApiError && error.detail ? error.detail : COPY.install.cannotStart
			);
		}
	}

	async test(): Promise<void> {
		this.testing = true;
		try {
			this.tested = await api.post<components['schemas']['AcceleratorTestView']>(
				'/performance/accelerator/test',
				{ signal: AbortSignal.timeout(TEST_LIMIT_MS) }
			);
		} catch (error) {
			this.tested = {
				works: false,
				problem:
					error instanceof DOMException && error.name === 'TimeoutError'
						? COPY.test.noAnswer
						: error instanceof ApiError && error.detail
							? error.detail
							: COPY.test.failed
			};
		} finally {
			this.testing = false;
		}
	}

	async remove(): Promise<void> {
		this.removing = true;
		this.tested = null;
		try {
			this.accel = await api.del<Accelerator>('/performance/accelerator');
		} finally {
			this.removing = false;
		}
	}
}
