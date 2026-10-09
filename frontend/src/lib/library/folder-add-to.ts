/* Add to, opened on a FOLDER: the declared door, aimed at every file the folder holds. */
import { api } from '$lib/api/client';
import type { components } from '$lib/api/schema';
import { counted, filesSaid } from '$lib/entity/entity-counts';
import { MOST_SELECTED, SERVER_PAGE_CAP } from '$lib/grid/grid.svelte';
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

/** Every file under this folder, read a page at a time. */
export async function filesUnder(folderId: string): Promise<string[]> {
	const ids: string[] = [];
	let total = Infinity;
	while (ids.length < total) {
		const page = await api.get<Page>('/assets', {
			query: { in: folderId, offset: ids.length, limit: SERVER_PAGE_CAP }
		});
		total = page.total;
		if (total > MOST_SELECTED) throw new TooManyFiles(total);
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
				` holds ${counted(why.total)} files. Add to takes ${counted(MOST_SELECTED)} at a time, so open the folder and select the files there.`
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

/** The folder's own naming, through the route "Who is this?" */
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

/** The declared Add to, with every row aimed at the files under one folder. */
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
