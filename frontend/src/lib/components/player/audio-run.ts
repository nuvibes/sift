/*
 * The Audio player has no picture to show, so a run it walks passes pictures by: a picture opened
 * there would turn it back into the panel.
 */

/** How many pictures in a row are passed before the walk stops looking. */
export const MOST_PASSED = 50;

/** The next file along the walk from `from`, or null. */
export type Onward = (from: string) => string | null | Promise<string | null>;

/** What the walk needs of a file: its id and whether it is a clip. */
export interface Walked {
	id: string;
	media_type: string;
}

/**
 * The first clip from `id` on, stepping `onward` past every picture, or null where the walk runs
 * out, comes round to a picture it passed, or passes `MOST_PASSED`.
 */
export async function firstClip<File extends Walked>(
	id: string,
	onward: Onward,
	recordOf: (id: string) => Promise<File>
): Promise<File | null> {
	let file = await recordOf(id);
	const passed = new Set<string>();
	while (file.media_type !== 'video') {
		passed.add(file.id);
		if (passed.size > MOST_PASSED) return null;
		const next = await onward(file.id);
		if (next === null || passed.has(next)) return null;
		file = await recordOf(next);
	}
	return file;
}
