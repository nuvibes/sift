// SPDX-License-Identifier: AGPL-3.0-or-later
/* Turning a folder somebody chose for downloads into the library folder the server files them
 * in. */

import { api } from '$lib/api/client';
import type { components } from '$lib/api/schema';

/** A library folder's top, as `/library/roots` lists it. */
export interface RootAt {
	id: string;
	path: string;
}

/** A folder of a library, as `/library/folders` lists it: the four fields placing one reads. */
export type FolderAt = Pick<
	components['schemas']['FolderDetail'],
	'id' | 'root_id' | 'parent_id' | 'name'
>;

/** Where a chosen place sits in the library. */
type Placement =
	| { kind: 'folder'; id: string }
	| { kind: 'inside'; parentId: string; make: string[] }
	| { kind: 'outside' };

/** The steps `folderFor` takes, handed in so a test can stand in for the server. */
export interface FolderSteps {
	roots(): Promise<RootAt[]>;
	folders(): Promise<FolderAt[]>;
	/** Hand the folder to Sift. Says nothing when it was already handed over. */
	grant(path: string): Promise<void>;
	/** Add it as a library folder, and read what is in it. */
	addRoot(path: string): Promise<void>;
	/** Place one folder inside another: the one on the disk, or a new one where there is none. */
	makeFolder(parentId: string, name: string): Promise<FolderAt>;
}

/** Said when a folder was added and still cannot be found among the library's folders. */
const NOT_FOUND_AFTER_ADDING =
	"Sift added that folder but couldn't find it in your library. Choose it from the list instead.";

/* A path as its separate names, so `C:\Media\` and `C:/Media` are one place. */
function segments(path: string): string[] {
	return path
		.trim()
		.split(/[\\/]+/)
		.filter(Boolean);
}

function foldsCase(path: string): boolean {
	return /^[A-Za-z]:/.test(path.trim()) || path.trim().startsWith('\\\\');
}

function same(one: string, other: string, fold: boolean): boolean {
	return fold ? one.toLowerCase() === other.toLowerCase() : one === other;
}

/** Where `path` sits among the library's folders. The deepest library holding it decides. */
export function placeIn(path: string, roots: RootAt[], folders: FolderAt[]): Placement {
	const wanted = segments(path);
	const fold = foldsCase(path);
	const deepestFirst = [...roots].sort(
		(one, other) => segments(other.path).length - segments(one.path).length
	);
	for (const root of deepestFirst) {
		const base = segments(root.path);
		if (base.length > wanted.length) continue;
		if (!base.every((name, at) => same(name, wanted[at], fold))) continue;
		let parent = folders.find((one) => one.root_id === root.id && one.parent_id === null);
		if (!parent) continue;
		const rest = wanted.slice(base.length);
		let found = 0;
		for (const name of rest) {
			const at: string = parent.id;
			const child = folders.find(
				(one) => one.root_id === root.id && one.parent_id === at && same(one.name, name, fold)
			);
			if (!child) break;
			parent = child;
			found += 1;
		}
		if (found === rest.length) return { kind: 'folder', id: parent.id };
		return { kind: 'inside', parentId: parent.id, make: rest.slice(found) };
	}
	return { kind: 'outside' };
}

/** The id of the library folder at `path`, made or added first where it is not one yet. */
export async function folderFor(path: string, steps: FolderSteps): Promise<string> {
	let place = placeIn(path, await steps.roots(), await steps.folders());
	if (place.kind === 'outside') {
		await steps.grant(path);
		await steps.addRoot(path);
		place = placeIn(path, await steps.roots(), await steps.folders());
		if (place.kind === 'outside') throw new Error(NOT_FOUND_AFTER_ADDING);
	}
	if (place.kind === 'folder') return place.id;
	let parent = place.parentId;
	for (const name of place.make) parent = (await steps.makeFolder(parent, name)).id;
	return parent;
}

type RootsView = components['schemas']['RootsView'];
type FoldersView = components['schemas']['FoldersView'];
type FolderView = components['schemas']['FolderView'];

/** The steps, against the server. */
export const LIBRARY_STEPS: FolderSteps = {
	roots: async () => (await api.get<RootsView>('/library/roots')).roots,
	folders: async () => (await api.get<FoldersView>('/library/folders')).folders,
	grant: async (path) => {
		try {
			await api.post('/library/grants', { body: { path } });
		} catch {
			/* Already handed over, or inside a folder that was. */
		}
	},
	addRoot: async (path) => {
		await api.post('/library/roots', { body: { abs_path: path, scan: true } });
	},
	makeFolder: async (parentId, name) =>
		await api.post<FolderView>('/library/folders/placed', { body: { parent_id: parentId, name } })
};
