/*
 * The glyph each act wears, read by the app and by `scripts/check_button_glyphs.js` alike.
 *
 * The gate reads markup, so it sees `<Button icon="delete">Delete</Button>` and cannot see a verb
 * handed to a row primitive as a prop from a copy table. A row that draws its button from a verb
 * asks `actGlyph` instead, so the glyph follows the act on every row without each caller
 * remembering it. One table for both, so the gate and the rows cannot disagree.
 *
 * Only erasable TypeScript here: the gate imports this file with Node's own type stripping.
 */
import type { IconName } from '$lib/design/icons';

/**
 * The acts, by the first word of the button, and the glyphs each may wear: one verb, one glyph,
 * with the thing's own mark allowed where the thing is the point (a folder made on disk, a Site's
 * cookies, a person), as the declared verbs already wear it. The first glyph is the act's own.
 * Undo wears `undo`; the bin with an arrow means taking back a decision in a queue.
 */
export const ACTS: ReadonlyMap<string, readonly IconName[]> = new Map<string, readonly IconName[]>([
	['Add', ['add', 'person_add', 'cookie']],
	['Compress', ['compress']],
	['Copy', ['content_copy']],
	// A GIF made from a clip wears the GIF box the grid's own verb wears, not a plus.
	['Create', ['add', 'create_new_folder', 'gif_box']],
	['Delete', ['delete']],
	['Discard', ['remove']],
	['Download', ['download']],
	['Edit', ['edit']],
	['Export', ['save']],
	['Import', ['upload']],
	['Merge', ['merge']],
	['Move', ['drive_file_move']],
	['Play', ['play_arrow', 'pause']],
	['Refresh', ['cached']],
	['Remove', ['close']],
	['Rename', ['edit_square']],
	['Restore', ['history']],
	['Resume', ['play_arrow']],
	['Save', ['save']],
	['Search', ['search']],
	/* A share that hands you a file to take away (the facial fingerprints, as one file) wears the
	   download glyph Save to device wears, since what the press does is save that file here. */
	['Share', ['group', 'download']],
	['Unhide', ['visibility']],
	['Undo', ['undo']]
]);

/** The first word of a button's words, when they open on one. */
export function firstWord(words: string): string | null {
	return /^[A-Z][a-z]+/.exec(words.trim())?.[0] ?? null;
}

/**
 * The glyph a button saying `words` wears.
 *
 * An act wears its own glyph: the one `offered` when the act allows it, else the act's first.
 * Anything that is not an act (a navigation, an answer, a verb with no glyph of its own) keeps
 * what the caller offered, which is nothing unless it asked.
 */
export function actGlyph(words: string, offered?: IconName): IconName | undefined {
	const word = firstWord(words);
	const allowed = word === null ? undefined : ACTS.get(word);
	if (allowed === undefined) return offered;
	return offered !== undefined && allowed.includes(offered) ? offered : allowed[0];
}
