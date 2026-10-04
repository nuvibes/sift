import type { components } from '$lib/api/schema';
/* Turning the flat list of folders the server sends into the tree the browser draws.
 *
 * The server sends every folder the viewer may see, in path order, each carrying its parent's id.
 * Flat, because the alternative is a request per row: asked a level at a time, a client cannot tell
 * a leaf from an unopened folder without opening it, and the server answering that question means
 * writing the visibility rule a second time in a second query that can disagree with the first.
 *
 * So the shape is assembled here, where getting it wrong costs a wrong indent rather than a leak.
 */

export type Folder = components['schemas']['FolderView'];

export interface FolderNode {
	id: string;
	label: string;
	shared?: boolean;
	restricted?: boolean;
	shared_here?: boolean;
	restricted_here?: boolean;
	hidden?: boolean;
	/** The folder's own "Don't enrich" (`local`) or "Don't swap", reaching every file inside. */
	refused?: 'local' | 'swap';
	children?: FolderNode[];
}

/**
 * The tree, from the flat list.
 *
 * A folder whose parent is not in the list is put at the top rather than dropped, and that case is
 * real rather than defensive: restrict a folder, share one folder inside it, and the inner one is
 * visible while its parent is not. That is nearest-wins working exactly as intended, and it is the
 * whole reason restrict is worth having. Dropping the orphan would hide a folder somebody
 * deliberately shared: the sharing would silently do nothing, which is the worst way for a
 * permission to fail.
 *
 * Order is preserved from the server, which sends them by path, so siblings come out alphabetical
 * without this having an opinion about anybody's locale.
 */
export function buildTree(folders: readonly Folder[]): FolderNode[] {
	const nodes = new Map<string, FolderNode>();
	for (const folder of folders) {
		nodes.set(folder.id, {
			id: folder.id,
			label: folder.name,
			shared: folder.shared,
			restricted: folder.restricted,
			shared_here: folder.shared_here,
			restricted_here: folder.restricted_here,
			hidden: folder.hidden,
			...(folder.keep_local
				? { refused: 'local' as const }
				: folder.keep_from_swaps
					? { refused: 'swap' as const }
					: {})
		});
	}

	const roots: FolderNode[] = [];
	for (const folder of folders) {
		const node = nodes.get(folder.id);
		if (!node) continue;

		const parent = folder.parent_id === null ? undefined : nodes.get(folder.parent_id);
		if (!parent) {
			// No parent at all, or a parent this viewer cannot see. Both belong at the top.
			roots.push(node);
			continue;
		}
		(parent.children ??= []).push(node);
	}

	return roots;
}
