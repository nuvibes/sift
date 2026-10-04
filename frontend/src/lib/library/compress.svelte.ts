/* Asking the server what a compression would do, and then asking it to do it.
 *
 * Every judgement lives on the server: whether a target can be met, what the copy will be called,
 * which files in a selection cannot be acted on at all. None of it is worked out here, and that is
 * deliberate rather than lazy: the arithmetic needs a file's running time, frame rate and codecs,
 * the browser holds none of that for a selection of four hundred, and a second implementation of
 * the rules would be a second answer to disagree with the first.
 *
 * So this is a thin thing: the shapes, one call to ask, one call to start.
 */

import { api } from '$lib/api/client';
import type { components } from '$lib/api/schema';

/** The named targets. `custom` carries its number in the request instead of in a setting. */
export type Preset = 'small' | 'standard' | 'large' | 'very_large' | 'custom';

export type CompressRequest = components['schemas']['CompressRequest'];

/** What will happen to one file. Every field beyond the id is the server's own answer. */
export type FileVerdict = components['schemas']['FileVerdict'];

export type Preflight = components['schemas']['Preflight'];

export type CompressStarted = components['schemas']['CompressStarted'];

export async function preflight(request: CompressRequest): Promise<Preflight> {
	return api.post<Preflight>('/compress/preflight', { body: request });
}

export async function start(request: CompressRequest): Promise<CompressStarted> {
	return api.post<CompressStarted>('/compress', { body: request });
}

export type SampleStarted = components['schemas']['SampleStarted'];

/** Ask for a few seconds encoded the way the whole file would be. Returns the job to watch. */
export async function sample(assetId: string, request: CompressRequest): Promise<SampleStarted> {
	return api.post<SampleStarted>(`/assets/${encodeURIComponent(assetId)}/compress/sample`, {
		body: request
	});
}

/** Where the finished sample is readable. Answers 404 until the encode has produced it. */
export function sampleUrl(jobId: string): string {
	return `/api/compress/samples/${encodeURIComponent(jobId)}`;
}

/**
 * Wait for a sample to exist, or give up.
 *
 * Polled rather than pushed, and against the sample's own address rather than the job's state:
 * what the screen needs is not "the job says done", it is "there are bytes to play", and those
 * are the same question only if nothing went wrong between them.
 */
export async function waitForSample(jobId: string, attempts = 60): Promise<boolean> {
	for (let tried = 0; tried < attempts; tried += 1) {
		try {
			const response = await fetch(sampleUrl(jobId), {
				method: 'GET',
				headers: { Range: 'bytes=0-0' }
			});
			if (response.ok || response.status === 206) return true;
		} catch {
			// A dropped request while the encode runs is not an answer. Keep asking.
		}
		await new Promise((wake) => setTimeout(wake, 1000));
	}
	return false;
}

/** A byte count as somebody reads it. Whole numbers above ten, one decimal below. */
export function megabytes(value: number): string {
	const size = value / (1024 * 1024);
	return size >= 10 ? `${Math.round(size)} MB` : `${size.toFixed(1)} MB`;
}

/* --- where a copy came from -------------------------------------------------------------------
 *
 * Sift writes a row every time it makes a file out of another one, and this is that row read
 * back. `source_asset_id` is null when the original has since been deleted: the copy is still a
 * copy and still says so, there is simply nowhere for the link to go.
 *
 * Nothing in the client draws these: the fact is two rows of the History tab, assembled
 * server-side from the same table. They are kept because the ROUTES behind them are the editing
 * feature's own, with their own tests and their own scoping, and because they are the only place
 * the OPERATION is readable. History says "Made from x.mp4" where these say "Compressed from
 * x.mp4", the verb being the feature's vocabulary and not the kernel's. Retiring the pair is a
 * change to that feature, with its route rows and its authz line.
 */

export type Made = 'crop' | 'resize' | 'rotate' | 'trim' | 'clip' | 'compress';

export type Produced = components['schemas']['Produced'];

/** What this file was made from, or null when Sift did not make it. */
async function producedFor(assetId: string): Promise<Produced | null> {
	return api.get<Produced | null>(`/assets/${encodeURIComponent(assetId)}/produced`);
}

/** One copy Sift made from a file, as the ORIGINAL's page sees it. */
export type MadeCopy = components['schemas']['MadeCopy'];

/** What was made from this file. Empty for most files, which are nobody's original. */
async function madeFrom(assetId: string): Promise<MadeCopy[]> {
	const answer = await api.get<components['schemas']['MadeCopies']>(
		`/assets/${encodeURIComponent(assetId)}/made-from`
	);
	return answer.copies ?? [];
}

/** The verb for what was done TO this file, said from the original's end of the line. */
function madeInto(operation: string): string {
	const verbs: Record<string, string> = {
		crop: 'Cropped into',
		resize: 'Resized into',
		rotate: 'Rotated into',
		trim: 'Trimmed into',
		clip: 'Trimmed into',
		compress: 'Compressed into',
		edit: 'Edited into'
	};
	return verbs[operation] ?? 'Made into';
}

/** The verb for what was done, in the past tense somebody would say it in.
 *
 * A name this does not know reads as the plainest true thing rather than as the raw word, because
 * the raw word is a database value and a screen is not the place to find out there is a sixth one. */
export function madeBy(operation: string): string {
	const verbs: Record<string, string> = {
		crop: 'Cropped from',
		resize: 'Resized from',
		rotate: 'Rotated from',
		// Both, deliberately: a length asked for and a piece dragged out by its ends are the same
		// thing to whoever made it. The two operations stay distinct in the record.
		trim: 'Trimmed from',
		clip: 'Trimmed from',
		compress: 'Compressed from',
		// One Save that did several things. The individual verbs are kept for a Save that did one,
		// because "Cropped from" says more than this does, but a line cannot name four verbs and
		// stay a sentence.
		edit: 'Edited from'
	};
	return verbs[operation] ?? 'Created from';
}
