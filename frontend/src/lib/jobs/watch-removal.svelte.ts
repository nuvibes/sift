/*
 * Following a deletion Sift runs as a job (Delete face data, Delete index).
 *
 * On a large library each is minutes of small writes, so the press queues it and hands back the
 * job. Held at module level, like a download, so a pane closed and opened again shows it still
 * going rather than offering the press twice, and the end is said once however it ended.
 */

import { DownloadWatch, jobProgress } from '$lib/jobs/watch-download.svelte';
import { toasts } from '$lib/shell/toasts.svelte';

export class RemovalWatch extends DownloadWatch {
	/** The job last followed, kept past its end so the sentence can ask how it ended. */
	#last: string | null = null;
	/** Whether the run that ended here did what it was for. Null until one has ended. */
	succeeded = $state<boolean | null>(null);
	readonly #failed: string;

	constructor(type: string, done: string, failed: string) {
		super(
			type,
			async () => {
				const job = this.#last ? await jobProgress(type, this.#last).catch(() => null) : null;
				// Gone from the queue's page counts as done: the queue lists a finished run a while.
				this.succeeded = job === null || job.state === 'done';
				const said = this.succeeded ? done : failed;
				toasts.show(said, { tone: this.succeeded ? 'success' : 'error' });
				return said;
			},
			"Couldn't follow the deletion. Open Activity to see it."
		);
		this.#failed = failed;
	}

	override follow(jobId: string, state = 'queued'): void {
		this.#last = jobId;
		this.succeeded = null;
		super.follow(jobId, state);
	}

	/** The press itself was refused: said at once, since no job will end to say it. */
	override couldNotStart(why = this.#failed): void {
		this.succeeded = false;
		super.couldNotStart(why);
		toasts.show(why, { tone: 'error' });
	}
}

/** Delete face data, followed from Settings > Faces. */
export const faceRemoval = new RemovalWatch(
	'face_forget',
	'Face data deleted',
	"Couldn't delete face data"
);

/** Delete index, followed from Settings > Smart Search. */
export const indexRemoval = new RemovalWatch(
	'semantic_forget',
	'Smart Search index deleted',
	"Couldn't delete the Smart Search index"
);
