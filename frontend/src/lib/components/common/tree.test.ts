import { describe, expect, it } from 'vitest';
import { flattenTree, type TreeNode } from './tree';

/* What is on screen, and at what depth.
 *
 * The rows are what the arrow keys walk and what a screen reader reads, so the thing that matters
 * is that a collapsed folder's children are not rows at all. A row for something nobody can see is
 * a place for focus to disappear into.
 */

const tree: TreeNode[] = [
	{
		id: 'videos',
		label: 'Videos',
		children: [
			{ id: 'holiday', label: 'Holiday', count: 12 },
			{ id: 'archive', label: 'Archive', children: [{ id: 'old', label: 'Old', count: 3 }] }
		]
	},
	{ id: 'photos', label: 'Photos', count: 40 }
];

const ids = (rows: ReturnType<typeof flattenTree>) => rows.map((row) => row.node.id);

describe('with everything collapsed', () => {
	it('only the roots are rows', () => {
		expect(ids(flattenTree(tree, new Set()))).toEqual(['videos', 'photos']);
	});

	it('a folder with children says it has them, and that it is closed', () => {
		const [videos, photos] = flattenTree(tree, new Set());

		expect(videos.hasChildren).toBe(true);
		expect(videos.expanded).toBe(false);
		// And one without children is not a folder that is merely closed: it has nothing to open,
		// which is why it gets no chevron and no aria-expanded rather than a false one.
		expect(photos.hasChildren).toBe(false);
	});
});

describe('with a folder open', () => {
	it('its children become rows, directly under it', () => {
		expect(ids(flattenTree(tree, new Set(['videos'])))).toEqual([
			'videos',
			'holiday',
			'archive',
			'photos'
		]);
	});

	it('a grandchild stays hidden while its own parent is closed', () => {
		// `archive` is expanded, but nobody can see it: `videos` is shut. Reading the expanded set
		// without walking the path is how a row for an invisible folder gets rendered.
		expect(ids(flattenTree(tree, new Set(['archive'])))).toEqual(['videos', 'photos']);
	});

	it('opens all the way down when the whole path is open', () => {
		expect(ids(flattenTree(tree, new Set(['videos', 'archive'])))).toEqual([
			'videos',
			'holiday',
			'archive',
			'old',
			'photos'
		]);
	});
});

describe('depth', () => {
	it('counts from zero at the roots', () => {
		const rows = flattenTree(tree, new Set(['videos', 'archive']));
		const levels = Object.fromEntries(rows.map((row) => [row.node.id, row.level]));

		// This becomes aria-level, which is 1-based: the component adds the one. Getting that wrong
		// is how a tree tells a screen reader everything is a root.
		expect(levels).toEqual({ videos: 0, holiday: 1, archive: 1, old: 2, photos: 0 });
	});
});

describe('an empty children array', () => {
	it('is not a folder with children', () => {
		// A real shape: the server knows the folder and knows it has nothing in it. It must not get a
		// chevron that opens onto nothing.
		const [row] = flattenTree([{ id: 'empty', label: 'Empty', children: [] }], new Set(['empty']));

		expect(row.hasChildren).toBe(false);
		expect(row.expanded).toBe(false);
	});
});
