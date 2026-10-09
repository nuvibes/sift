/* Importing a folder of people, as the task Sift runs it. */

/* LIVE: followed by lib/jobs/watch-download.svelte.ts (the task's row read again every two seconds until it ends) */
import { api, ApiError } from '$lib/api/client';
import { isFinished } from '$lib/jobs/queue.svelte';
import { DownloadWatch, sayWaiting } from '$lib/jobs/watch-download.svelte';
import type { components } from '$lib/api/schema';

/** The task's kind, as the queue knows it. */
const FOLDER_IMPORT = 'face_folder_import';

/** What the row says before the task's own note arrives. */
export const READING_THE_FOLDER = 'Reading the folder\u2026';

type Started = { job_id: string };
type JobRow = components['schemas']['JobView'];
type LeftOut = components['schemas']['FolderLeftOut'];

/** One fold under the report: its count in the report's own words, and the files behind it. */
type ReportFold = { summary: string; files: string[] };

/** Import the folder at this path, on the machine Sift runs on. Answers with the task. */
export async function importFolderByPath(path: string): Promise<string> {
	const started = await api.post<Started>('/faces/references/folder/path', { body: { path } });
	return started.job_id;
}

/* Send a folder chosen on this device. Each file goes with its path under the folder, which says
 * whose folder it sat in: FormData would send the bare name and flatten every folder into one. */
export async function importFolderFromDevice(files: File[]): Promise<string> {
	const form = new FormData();
	for (const file of files) {
		form.append('files', file, file.webkitRelativePath || file.name);
	}
	const started = await api.post<Started>('/faces/references/folder', { body: form });
	return started.job_id;
}

/** The press's refusal, in the words the screen shows. */
export function refusalOfImport(error: unknown): string {
	if (error instanceof ApiError && error.status === 409) {
		return 'Turn on face recognition before importing a folder.';
	}
	if (error instanceof ApiError && error.status === 413) {
		return 'That folder is more than Sift imports in one go. Import it in parts.';
	}
	if (error instanceof ApiError && error.status === 400 && error.detail) return error.detail;
	return "Couldn't import that folder. Try again.";
}

/** One task's last row, or null when the queue no longer lists it. */
async function rowOf(jobId: string): Promise<JobRow | null> {
	const page = await api.get<components['schemas']['JobsPage']>('/jobs', {
		query: { type: FOLDER_IMPORT, limit: 50 }
	});
	return page.jobs.find((one) => one.id === jobId) ?? null;
}

/** How a task that has ended is said on the row: its report, its refusal, or that it stopped. */
export function endedWith(row: Pick<JobRow, 'state' | 'note' | 'error'> | null): string {
	if (row === null) return 'The import has finished. Open Activity to see what it kept.';
	if (row.state === 'canceled') {
		return 'Import canceled. The faces imported before it stopped are kept.';
	}
	if (row.state === 'failed') return row.error || "The import couldn't finish.";
	return row.note || 'The import has finished.';
}

/** The report's folds: each reason in the order the report counts them, then the near copies. */
export function foldsOf(left: LeftOut): ReportFold[] {
	const folds = left.left_out.map((one) => ({
		summary: `${one.files.length.toLocaleString('en-US')} ${one.words}`,
		files: one.files
	}));
	const near = left.near_copies.length;
	if (near > 0) {
		const photos = near === 1 ? '1 photo' : `${near.toLocaleString('en-US')} photos`;
		folds.push({
			summary: `${photos} kept though almost the same as another`,
			files: left.near_copies
		});
	}
	return folds;
}

/** Which files a finished import left out, or no folds when they cannot be read. */
async function foldsOfJob(jobId: string): Promise<ReportFold[]> {
	try {
		return foldsOf(await api.get<LeftOut>(`/faces/references/folder/${jobId}/left-out`));
	} catch {
		return [];
	}
}

class FolderImportWatch extends DownloadWatch {
	/** The task last followed, kept past its end so its last row can be read. */
	#last: string | null = null;
	/** Whether the task that ended here finished. Null until one has ended. */
	succeeded = $state<boolean | null>(null);
	/** The files under each count of the report, once a task has finished here. */
	folds = $state<ReportFold[]>([]);

	constructor() {
		super(
			FOLDER_IMPORT,
			async () => {
				const row = this.#last ? await rowOf(this.#last).catch(() => null) : null;
				this.succeeded = row === null || (isFinished(row.state) && row.state === 'done');
				this.folds = row?.state === 'done' ? await foldsOfJob(row.id) : [];
				return endedWith(row);
			},
			"Couldn't follow the import. Open Activity to see it."
		);
	}

	override follow(jobId: string, state = 'queued'): void {
		this.#last = jobId;
		this.succeeded = null;
		this.folds = [];
		super.follow(jobId, state);
	}

	/** Where the task has got to, in one line beside its bar. */
	override get status(): string {
		if (this.waiting) return sayWaiting(this.position);
		return this.note ?? READING_THE_FOLDER;
	}
}

/** The folder import followed from Settings > Faces. */
export const folderImport = new FolderImportWatch();
