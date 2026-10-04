import { afterEach, describe, expect, it, vi } from 'vitest';
import { createRawSnippet, flushSync, mount, unmount } from 'svelte';
import { reactiveProps } from '$lib/design/testing.svelte';
import Tree from './Tree.svelte';
import type { TreeNode } from './tree';

/* The tree from the keyboard, and the shape it says out loud.
 *
 * A folder tree is the one place in the app where the structure is the information, so the rows have
 * to carry it: what level each one is at, whether it opens, and whether it is the one being looked
 * at. None of that is visible from the fact that the folders are on screen and look right.
 */

/* A row's menu, as the folder list gives it: one row, named for the folder. */
const menuRows = createRawSnippet<[string, string]>((id, label) => ({
	render: () => `<div role="menuitem" data-id="${id()}">Share ${label()}</div>`
}));

const nodes: TreeNode[] = [
	{
		id: 'videos',
		label: 'Videos',
		children: [
			{ id: 'holiday', label: 'Holiday', count: 12 },
			{ id: 'archive', label: 'Archive', children: [{ id: 'old', label: 'Old' }] }
		]
	},
	{ id: 'photos', label: 'Photos', count: 40 }
];

let host: HTMLElement;
let mounted: Record<string, unknown> | null = null;

afterEach(() => {
	/* Unmounted, not just removed: a row's menu is portalled to the document, so a host taken away
	   leaves an open menu behind for the next test to find. */
	if (mounted) unmount(mounted);
	mounted = null;
	host?.remove();
});

function render(extra: Record<string, unknown> = {}) {
	host = document.createElement('div');
	document.body.append(host);

	const props = reactiveProps({
		nodes,
		label: 'Folders',
		selectedId: undefined as string | undefined,
		...extra
	});
	mounted = mount(Tree, { target: host, props });
	flushSync();

	const tree = host.querySelector('[role="tree"]') as HTMLElement;
	const press = (key: string) => {
		tree.dispatchEvent(new KeyboardEvent('keydown', { key, bubbles: true }));
		flushSync();
	};

	return {
		props,
		tree,
		press,
		rows: () => [...host.querySelectorAll('[role="treeitem"]')] as HTMLElement[],
		ids: () => [...host.querySelectorAll('[role="treeitem"]')].map((r) => r.id.replace('tree-', ''))
	};
}

describe('the rows', () => {
	it('group a count the way every other count on screen is grouped', () => {
		render({ nodes: [{ id: 'big', label: 'Big', count: 12345 }] });

		expect(host.querySelector('.count')?.textContent).toBe((12345).toLocaleString());
		expect(host.querySelector('.count')?.textContent).not.toBe('12345');
	});

	it('start with the roots alone', () => {
		const { ids } = render();

		expect(ids()).toEqual(['videos', 'photos']);
	});

	it('say how deep they are, counting from one', () => {
		// aria-level is 1-based. Off by one here and a screen reader describes a flat list.
		const { press, rows } = render();
		press('ArrowRight');

		expect(rows().map((r) => r.getAttribute('aria-level'))).toEqual(['1', '2', '2', '1']);
	});

	it('say whether they open, and only when they do', () => {
		const { rows } = render();
		const [videos, photos] = rows();

		expect(videos.getAttribute('aria-expanded')).toBe('false');
		// Nothing inside it, so there is nothing to be closed, and no promise of a chevron.
		expect(photos.hasAttribute('aria-expanded')).toBe(false);
	});
});

describe('the arrow keys', () => {
	it('right opens a closed folder', () => {
		const { press, ids } = render();

		press('ArrowRight');

		expect(ids()).toEqual(['videos', 'holiday', 'archive', 'photos']);
	});

	it('left closes an open one', () => {
		const { press, ids } = render();
		press('ArrowRight');

		press('ArrowLeft');

		expect(ids()).toEqual(['videos', 'photos']);
	});

	it('down steps to the next row on screen', () => {
		const { press, rows } = render();
		press('ArrowRight');
		press('ArrowDown');

		// Focus lands on the child, which is the next row, not on the next root, which is what a
		// tree that walked its own data rather than the screen would do.
		expect(document.activeElement?.id).toBe('tree-holiday');
		expect(rows()[1].tabIndex).toBe(0);
	});

	it('is the only tab stop: the rest of the rows are not', () => {
		// A library with four hundred folders must not be four hundred presses of tab to get past.
		const { press, rows } = render();
		press('ArrowRight');

		const stops = rows().filter((row) => row.tabIndex === 0);
		expect(stops).toHaveLength(1);
	});

	it('still has a tab stop after the focused row is collapsed out of sight', () => {
		// The way a tree locks the keyboard out of itself.
		//
		// Focus a child, then close its parent with the mouse. The row that was focused is not a row
		// any more (it is inside a shut folder) so nothing carries the tab stop, and the tree
		// becomes an element the keyboard cannot reach at all. Nothing looks wrong: the folders are
		// on screen and the mouse still works.
		const { rows, press } = render();
		press('ArrowRight');

		rows()[1].focus();
		flushSync();

		// The chevron on the parent, clicked the way a pointer would.
		const chevron = rows()[0].querySelector('.chevron') as HTMLElement;
		chevron.click();
		flushSync();

		const stops = rows().filter((row) => row.tabIndex === 0);
		expect(stops, 'the tree has no tab stop and cannot be reached').toHaveLength(1);
	});
});

describe('choosing a folder', () => {
	it('marks it selected, and only it', () => {
		const { rows, press } = render();
		press('ArrowRight');

		rows()[1].click();
		flushSync();

		const selected = rows().filter((r) => r.getAttribute('aria-selected') === 'true');
		expect(selected).toHaveLength(1);
		expect(selected[0].id).toBe('tree-holiday');
	});

	it('tells the screen which one', () => {
		const { props, rows } = render();

		rows()[1].click();
		flushSync();

		expect(props.selectedId).toBe('photos');
	});
});

describe("a row's menu", () => {
	/*
	 * The dots and the right button open the menu; a plain press opens the folder, the obvious act,
	 * rather than being a third door to the menu.
	 */
	it('is not what a plain press opens', async () => {
		const { rows } = render({ nodes, rowMenu: menuRows });

		expect(host.querySelector('button.more'), 'the three dots').not.toBeNull();

		rows()[0].click();
		flushSync();

		expect(document.querySelector('[role="menu"]')).toBeNull();
	});

	it('opens from the dots', async () => {
		render({ nodes, rowMenu: menuRows });

		(host.querySelector('button.more') as HTMLElement).click();
		flushSync();

		await vi.waitFor(() => {
			expect(document.querySelector('[role="menu"]')).not.toBeNull();
		});

		/* Closed before the tree is torn down. The menu is portalled and settles its position after
		   the fact; unmounting under an open one leaves that work reading a destroyed row, which
		   Svelte reports on the NEXT test rather than this one. */
		document
			.querySelector('[role="menu"]')
			?.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }));
		await vi.waitFor(() => {
			expect(document.querySelector('[role="menu"]')).toBeNull();
		});
	});
});

describe('a press on a folder', () => {
	it('opens it out, the same as the chevron beside its name', () => {
		const { rows, ids } = render({ nodes, rowMenu: menuRows });
		expect(ids()).toEqual(['videos', 'photos']);

		rows()[0].click();
		flushSync();

		expect(ids()).toEqual(['videos', 'holiday', 'archive', 'photos']);
	});

	it('shuts one that is already open', () => {
		const { rows, ids } = render({ nodes });

		rows()[0].click();
		flushSync();
		rows()[0].click();
		flushSync();

		expect(ids()).toEqual(['videos', 'photos']);
	});

	it('does not open it twice when the press was the chevron', () => {
		const { ids } = render({ nodes, rowMenu: menuRows });

		(host.querySelector('.chevron') as HTMLElement).click();
		flushSync();

		expect(ids()).toEqual(['videos', 'holiday', 'archive', 'photos']);
		expect(document.querySelector('[role="menu"]')).toBeNull();
	});

	it('is reached by Enter as well, on the row the keyboard is on', () => {
		const { press, ids } = render({ nodes });

		press('Enter');

		expect(ids()).toEqual(['videos', 'holiday', 'archive', 'photos']);
	});
});

describe("a row's sharing mark", () => {
	/* A shared folder, so there is a mark to press at all. */
	const marked: TreeNode[] = [
		{ id: 'videos', label: 'Videos', shared: true, shared_here: true },
		{ id: 'photos', label: 'Photos' }
	];

	it('opens sharing on that folder, by name', () => {
		const onsharing = vi.fn();
		render({ nodes: marked, onsharing });

		const mark = host.querySelector('.mark-slot button') as HTMLElement;
		expect(mark, 'the mark says click to see the sharing menu and is not a button').not.toBeNull();

		mark.click();
		flushSync();

		expect(onsharing).toHaveBeenCalledWith('videos', 'Videos');
	});

	it('does not also select the row it sits in', () => {
		// The row is the click target for choosing a folder, and the mark is inside it. Without the
		// press being stopped, opening the panel also moves what the whole screen is looking at,
		// so the panel comes up over a grid that has just changed underneath it.
		const { props } = render({ nodes: marked, onsharing: vi.fn() });

		(host.querySelector('.mark-slot button') as HTMLElement).click();
		flushSync();

		expect(props.selectedId, 'pressing the mark selected the folder too').toBeUndefined();
	});

	it('is drawn as plain text where there is nothing to open', () => {
		// A tree with nowhere to send this must not draw a control that says click and does nothing.
		render({ nodes: marked });

		expect(host.querySelector('.mark-slot')).not.toBeNull();
		expect(host.querySelector('.mark-slot button')).toBeNull();
	});

	it('takes up no room on a folder nothing has been said about', () => {
		// The row is a flex line with a gap between everything in it, so an empty slot is a visible
		// hole, on every folder in the library, which is nearly all of them.
		render({ nodes: marked, onsharing: vi.fn() });

		const photos = host.querySelector('#tree-photos') as HTMLElement;
		expect(photos.querySelector('.mark-slot')).toBeNull();
	});
});
