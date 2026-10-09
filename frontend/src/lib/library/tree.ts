import type { components } from '$lib/api/schema';
/* Turning the flat list of folders the server sends into the tree the browser draws. */

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

/** The tree, from the flat list. */
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
