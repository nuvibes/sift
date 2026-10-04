import { describe, expect, it } from 'vitest';
import { buildTree, type Folder } from './tree';

/* The shape, and the one case that looks like a bug and is not. */

const ROOT = 'root-1';

function folder(id: string, name: string, parent_id: string | null, root_id = ROOT): Folder {
	return {
		id,
		name,
		parent_id,
		root_id,
		rel_path: name,
		shared: false,
		restricted: false,
		shared_here: false,
		restricted_here: false,
		hidden: false,
		keep_local: false,
		keep_from_swaps: false
	};
}

// Videos            (the folder standing for the root)
//   clips
//     holiday
//   archive
const LIBRARY: Folder[] = [
	folder('videos', 'Videos', null),
	folder('clips', 'clips', 'videos'),
	folder('holiday', 'holiday', 'clips'),
	folder('archive', 'archive', 'videos')
];

describe('buildTree', () => {
	it('nests each folder under its parent', () => {
		const tree = buildTree(LIBRARY);

		expect(tree).toHaveLength(1);
		expect(tree[0].label).toBe('Videos');
		expect(tree[0].children?.map((child) => child.label)).toEqual(['clips', 'archive']);
		expect(tree[0].children?.[0].children?.map((child) => child.label)).toEqual(['holiday']);
	});

	it('keeps the order the server sent, which is by path', () => {
		const tree = buildTree(LIBRARY);
		expect(tree[0].children?.map((child) => child.id)).toEqual(['clips', 'archive']);
	});

	it('leaves a folder with no children without a children array', () => {
		// What the Tree reads to decide whether there is anything to open.
		const tree = buildTree(LIBRARY);
		const archive = tree[0].children?.find((child) => child.id === 'archive');
		expect(archive?.children).toBeUndefined();
	});

	it('shows a shared folder whose parent is concealed, at the top', () => {
		// Not defensive: restrict a folder, share one inside it, and this is what the server sends.
		// The inner folder is visible and its parent is not: nearest-wins, which is the entire
		// point of restrict being overridable. Dropping it would make the share silently do nothing.
		const shared = [folder('videos', 'Videos', null), folder('holiday', 'holiday', 'clips')];

		const tree = buildTree(shared);

		expect(tree.map((node) => node.label)).toEqual(['Videos', 'holiday']);
	});

	it("carries a folder's own Don't enrich or Don't swap, Don't enrich first", () => {
		const both = {
			...folder('beach', 'Beach days', null),
			keep_local: true,
			keep_from_swaps: true
		};
		const swap = { ...folder('harbour', 'Harbour', null), keep_from_swaps: true };
		const tree = buildTree([both, swap, folder('free', 'Free', null)]);
		expect(tree.map((node) => node.refused)).toEqual(['local', 'swap', undefined]);
	});

	it('is empty for a viewer who may see nothing', () => {
		expect(buildTree([])).toEqual([]);
	});
});
