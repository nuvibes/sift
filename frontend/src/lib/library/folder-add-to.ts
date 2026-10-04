/*
 * Add to, opened on a FOLDER: the declared door, aimed at every file the folder holds.
 *
 * A folder's menu offers the same Add to a file's menu and the selection bar offer: the same six
 * rows, the same lists opening out of them, the same writes behind each pick. The rows come from
 * the one declaration (`grid/verbs.ts`, handed out by `FileVerbs`) and nothing here words or
 * draws a row. What differs is only WHAT the rows act on: a menu opened on files knows their ids,
 * and a menu opened on a folder knows a folder. So each row's list is handed the folder's files,
 * read when a row is used, and everything after that is the files' own act: the toast, the
 * History line per file and the Undo are the ones a pick over a selection makes.
 *
 * The folder's files are everything under it, the folders inside it included, which is what the
 * folder's tally counts and what "this folder" means to somebody pointing at it.
 *
 * ## Person and Site name the FOLDER
 *
 * Add to > Person and Add to > Site on a folder are the folder's own act, not a filing per file:
 * the pick names the folder (`sayWhoAFolderIs`, the route "Who is this?" and "Which Site is this?"
 * pressed). That act files everything under it however many files there are, links the name's
 * aliases and usernames, and is the one record the folder reader learns a MISS from, which a
 * per-file filing would lose. The picker is still Add to's own list; only the write differs, so the
 * selection ceiling below does not apply to those two rows.
 *
 * ## The ceiling is the selection's
 *
 * A pick from here is a selection of the folder's files made at the moment of the press, so it
 * keeps the ceiling "select all" keeps (`MOST_AT_ONCE`), for the same reason: a press nobody can
 * size by eye must not become tens of thousands of writes. Over it, the row writes nothing and
 * says why and what to do instead, rather than adding the first thousand and calling that the
 * folder.
 */
import { api } from '$lib/api/client';
import type { components } from '$lib/api/schema';
import { counted, filesSaid } from '$lib/entity/entity-counts';
import { MOST_AT_ONCE, SERVER_PAGE_CAP } from '$lib/grid/grid.svelte';
import { toasts } from '$lib/shell/toasts.svelte';
import { thing, type ToastPiece } from '$lib/components/common/toast-pieces';
import { sayWhoAFolderIs } from '$lib/search/suggestions.svelte';
import type { PickChoice, PickLanded, Verb, VerbPick } from '$lib/components/common/verbs';

type Page = Pick<components['schemas']['AssetPageResponse'], 'total'> & {
	items: { id: string }[];
};

/** A folder holding more files than one press takes. Carries the count, for the sentence. */
class TooManyFiles extends Error {
	constructor(readonly total: number) {
		super(`${counted(total)} files`);
	}
}

/**
 * Every file under this folder, read a page at a time.
 *
 * THROWS rather than answering short: over the ceiling with `TooManyFiles`, and on a failed read
 * with the read's own error. A short answer would be a pick that quietly missed part of a folder.
 */
export async function filesUnder(folderId: string): Promise<string[]> {
	const ids: string[] = [];
	let total = Infinity;
	while (ids.length < total) {
		const page = await api.get<Page>('/assets', {
			query: { in: folderId, offset: ids.length, limit: SERVER_PAGE_CAP }
		});
		total = page.total;
		if (total > MOST_AT_ONCE) throw new TooManyFiles(total);
		for (const one of page.items) ids.push(one.id);
		if (page.items.length === 0) break;
	}
	return ids;
}

/** A folder with nothing in it to add. */
class EmptyFolder extends Error {}

/** Say why a row wrote nothing. One sentence per reason, naming the folder. */
function refused(name: string | ToastPiece, why: unknown): void {
	if (why instanceof TooManyFiles) {
		toasts.show(
			[
				name,
				` holds ${counted(why.total)} files. Add to takes ${counted(MOST_AT_ONCE)} at a time, so open the folder and select the files there.`
			],
			{ tone: 'error' }
		);
		return;
	}
	if (why instanceof EmptyFolder) {
		toasts.show([name, ' has no files to add']);
		return;
	}
	toasts.show(["Sift couldn't read the files in ", name], { tone: 'error' });
}

/** The two places a folder is NAMED as, by the row's id in the declaration (`grid/verbs.ts`). */
const NAMES_THE_FOLDER: Readonly<Record<string, 'person' | 'site'>> = {
	assign: 'person',
	site: 'site'
};

/** Name a folder as a person or a Site, as the pick on Add to > Person or > Site. */
type NameFolder = (kind: 'person' | 'site', choice: PickChoice) => Promise<PickLanded>;

/**
 * The folder's own naming, through the route "Who is this?" presses. Says what it filed,
 * or the server's sentence for why it would not.
 */
export function namingFolder(folderId: string): NameFolder {
	return async (kind, choice) => {
		try {
			const applied = await sayWhoAFolderIs(folderId, choice.name, kind);
			const named = thing(kind, choice.id, choice.name);
			toasts.show([`Added ${filesSaid(applied.files)} to `, named], { tone: 'success' });
			return 'landed';
		} catch (error) {
			toasts.show(error instanceof Error ? error.message : "That couldn't be applied", {
				tone: 'error'
			});
			return 'refused';
		}
	};
}

/**
 * The declared Add to, with every row aimed at the files under one folder.
 *
 * `files` is read once per door and shared by its rows, so opening a list (which asks what the
 * files are already on) and then picking from it reads the folder once. A failed read is not
 * kept: the next row used asks again.
 */
export function overFolder(
	door: Verb,
	folder: string | { id: string; name: string },
	files: () => Promise<string[]>,
	nameFolder?: NameFolder
): Verb {
	const name = typeof folder === 'string' ? folder : thing('folder', folder.id, folder.name);
	let reading: Promise<string[]> | null = null;
	function read(): Promise<string[]> {
		reading ??= files().then(
			(ids) => {
				if (ids.length === 0) throw new EmptyFolder();
				return ids;
			},
			(why: unknown) => {
				reading = null;
				throw why;
			}
		);
		return reading;
	}

	/** One write over the folder's files, or the reason it wrote nothing. */
	async function over(write: (ids: string[]) => Promise<PickLanded>): Promise<PickLanded> {
		let ids: string[];
		try {
			ids = await read();
		} catch (why) {
			refused(name, why);
			return 'refused';
		}
		return write(ids);
	}

	function aimed(pick: VerbPick): VerbPick {
		const { already, unpick } = pick;
		return {
			...pick,
			pick: (_ids, choice) => over((ids) => pick.pick(ids, choice)),
			...(unpick ? { unpick: (_ids, choice) => over((ids) => unpick(ids, choice)) } : {}),
			...(already
				? {
						// What the folder's files are already on. Unknown draws no marks, which is
						// what a picker with no answer draws anyway; the reason is said by a pick.
						already: async () => {
							try {
								return await already(await read());
							} catch {
								return {};
							}
						}
					}
				: {})
		};
	}

	return {
		...door,
		children: door.children?.map((row) => {
			const naming = NAMES_THE_FOLDER[row.id];
			if (row.pick && naming && nameFolder) {
				/* The folder's own act: no ceiling, nothing to take off per file (its decision in
				   History is the way back), and no marks read from a capped list of its files. */
				const { kind, plural, ask, create } = row.pick;
				return {
					...row,
					pick: {
						kind,
						plural,
						ask,
						...(create ? { create } : {}),
						pick: (_ids: string[], choice: PickChoice) => nameFolder(naming, choice)
					}
				};
			}
			if (row.pick) return { ...row, pick: aimed(row.pick) };
			const run = row.run;
			if (run)
				return {
					...row,
					run: () =>
						void read().then(
							(ids) => run(ids),
							(why: unknown) => refused(name, why)
						)
				};
			return row;
		})
	};
}
