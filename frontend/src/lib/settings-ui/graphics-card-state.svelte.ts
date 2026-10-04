/* The graphics card, as Performance asks the server about it: one reading shared by the block that
 * describes the card and the danger row at the foot of the pane, so deleting the support from the
 * one is what the other draws the moment it happens.
 *
 * What the block says in each state, and why there are five, is written beside the block in
 * `GraphicsCard.svelte`.
 */
import { api, ApiError } from '$lib/api/client';
import { serverBootId } from '$lib/shell/health';
import { settingChanges, whenChanged } from '$lib/library/changes.svelte';
import { thisDevice } from '$lib/desktop/server-shell';
import { accelWatch } from '$lib/jobs/accelerator.svelte';
import type { components } from '$lib/api/schema';
import { COPY } from './GraphicsCard.search';

type Accelerator = components['schemas']['AcceleratorView'];

/* How long a restart is given, and how often it is looked at.
 *
 * Generous, because a first start after an update creates nothing but takes as long as the
 * machine takes, and the cost of being wrong is a screen saying it failed when it did not. */
const RESTART_LIMIT_MS = 90_000;
const RESTART_POLL_MS = 500;

export class GraphicsCardState {
	/* Named `accel`, not `state`: a field called `state` reads as the `$state` rune beside it. */
	accel = $state<Accelerator | null>(null);
	testing = $state(false);
	tested = $state<{ works: boolean; problem: string | null } | null>(null);
	removing = $state(false);
	restarting = $state(false);
	restartProblem = $state<string | undefined>(undefined);

	/** The support is on this device and can be deleted: installed, and no download running. */
	readonly removable = $derived(this.accel?.installed === true && !accelWatch.running);

	/**
	 * Ask the SERVER to stop and start again, not the application this is being read in.
	 *
	 * The process that has to start again is the one the runtime is loaded into, which is the
	 * backend, and the backend is on whichever computer holds the library. Read from a browser or
	 * from a second copy of Sift in client mode, restarting the thing in front of you would restart
	 * the wrong computer, report success, and leave the card exactly as it was.
	 *
	 * AND IT WAITS FOR IT, which is the whole difference between this working and this LOOKING like
	 * it did nothing. The server answers the ask before it goes: stopping at the yes would leave
	 * the button saying "Restarting" for ever and the panel showing the state of the process that
	 * had just been stopped: a restart that happened in two seconds, invisible on screen.
	 *
	 * The waiting is on the BOOT ID rather than on "does it answer". A server that is about to stop
	 * still answers, so a poll for a reply decides it has come back before it has left.
	 */
	async restart(): Promise<void> {
		this.restarting = true;
		this.restartProblem = undefined;
		const before = await serverBootId();
		// The window's own computer, for the line History keeps of the restart (`thisDevice`).
		const device = await thisDevice();
		try {
			if (device) await api.post('/performance/restart', { query: { device } });
			else await api.post('/performance/restart');
		} catch (error) {
			this.restarting = false;
			this.restartProblem =
				error instanceof ApiError ? (error.detail ?? error.message) : COPY.restart.cannot;
			return;
		}
		const back = await this.#waitForTheServer(before);
		this.restarting = false;
		if (!back) {
			this.restartProblem = COPY.restart.slow;
			return;
		}
		// The panel is about a process that no longer exists. Read again, so "Restart needed" goes
		// and the test beside it becomes pressable.
		await this.read();
		this.tested = null;
		/* AND THE CARD IS TESTED WITHOUT BEING ASKED, because pressing Download and pressing Restart
		 * is the whole of what anybody should have to do. The test is the only thing standing
		 * between a restart and knowing whether it worked, it takes seconds, and it is the question
		 * every person will have at exactly this moment. Not on every visit: only here, once,
		 * after a restart this screen asked for. */
		if (this.accel?.installed && !this.accel.restart_needed) await this.test();
	}

	/** Wait until a DIFFERENT run of the server is answering. False if it never does. */
	async #waitForTheServer(before: string | null): Promise<boolean> {
		const deadline = Date.now() + RESTART_LIMIT_MS;
		while (Date.now() < deadline) {
			await new Promise((settle) => setTimeout(settle, RESTART_POLL_MS));
			const now = await serverBootId();
			if (now === null) continue;
			// A different run is the answer. Where the id could not be read beforehand (an older
			// server, a request that failed), there is nothing to compare against, and any answer
			// after the ask is the best this can honestly do.
			if (before === null || now !== before) return true;
		}
		return false;
	}

	/** Read the card again when a setting moves (the device recognition runs on, chosen in another
	 *  window). Called by the screen that holds this, while it sets up. */
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
				'/performance/accelerator/test'
			);
		} catch (error) {
			this.tested = {
				works: false,
				problem: error instanceof ApiError && error.detail ? error.detail : COPY.test.failed
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
