/*
 * What a toast says, as runs: the shape a History line is drawn from (`HistorySentence`), so a
 * toast the server words arrives as its pieces and one written here is built of them. Apart from
 * the toast store so a test that stands in for the store still builds real pieces.
 */
import type { HistoryPiece } from './history';

/** One run: plain words, or a thing named by its kind and id, drawn as the way to it. */
export type ToastPiece = Pick<HistoryPiece, 'text'> &
	Partial<Pick<HistoryPiece, 'kind' | 'id' | 'href'>>;

/** What a toast says: plain words, or its runs in order (a bare string in the list is words). */
export type ToastWords = string | readonly (string | ToastPiece)[];

/** A thing a toast names, by its kind and id: a person, a Site, a folder, a file. */
export function thing(kind: string, id: string, text: string): ToastPiece {
	return { text, kind, id };
}

/** A place in the app a toast names (`Activity`, a Settings row), by its address. */
export function place(text: string, href: string): ToastPiece {
	return { text, kind: 'place', id: null, href };
}

/** The words a toast says, its runs joined: what a screen reader is handed, and a test reads. */
export function wordsOf(words: ToastWords): string {
	return typeof words === 'string'
		? words
		: words.map((one) => (typeof one === 'string' ? one : one.text)).join('');
}
