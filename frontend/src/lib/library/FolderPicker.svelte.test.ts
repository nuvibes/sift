/* The picker's own drawing, whose top level is not a folder.
 *
 * The walking is tested in `picker.test.ts` against a stubbed server. What is left here is what a
 * person sees, and two parts of it follow from that top level: there is a way back from the
 * outermost granted folder to the list of them, and the file-count footnote must not appear on that
 * list: a count of "files in this folder" over something that is not a folder is a sentence about
 * nothing.
 */

import { afterEach, describe, expect, it, vi } from 'vitest';
import { flushSync, mount, unmount } from 'svelte';

import FolderPicker from './FolderPicker.svelte';
import type { Picker } from './picker.svelte';

let host: HTMLElement;
let mounted: Record<string, unknown> | null = null;

afterEach(() => {
	if (mounted) void unmount(mounted);
	mounted = null;
	host?.remove();
});

/* A stand-in for the store: this file is about what gets drawn for a given state, and every state
 * below is one the real store reaches. The store's own transitions are its tests' business. */
function picker(over: Partial<Picker> = {}): Picker {
	return {
		entries: [],
		breadcrumb: [],
		path: '',
		fileCount: 0,
		nothingGranted: false,
		writable: false,
		readOnlyMount: false,
		loading: false,
		failed: null,
		chosen: [],
		canGoUp: false,
		atTopLevel: true,
		selected: null,
		open: vi.fn(),
		up: vi.fn(),
		isChosen: () => false,
		choose: vi.fn(),
		unchoose: vi.fn(),
		...over
	} as unknown as Picker;
}

function render(one: Picker, selectable = false) {
	host = document.createElement('div');
	document.body.append(host);
	mounted = mount(FolderPicker, {
		target: host,
		props: { picker: one, labelledBy: 'label', selectable }
	});
	flushSync();
	return host;
}

const TOP = {
	entries: [
		{ name: 'media', path: 'D:\\media' },
		{ name: 'nas', path: '\\\\nas\\media' }
	],
	breadcrumb: [],
	path: '',
	atTopLevel: true,
	canGoUp: false
};

const INSIDE = {
	entries: [{ name: 'holidays', path: 'D:\\media\\holidays' }],
	breadcrumb: [{ name: 'media', path: 'D:\\media' }],
	path: 'D:\\media',
	atTopLevel: false,
	canGoUp: true,
	fileCount: 3
};

describe('the list of granted folders', () => {
	it('draws one row per folder somebody handed over', () => {
		const where = render(picker(TOP));

		expect([...where.querySelectorAll('.name')].map((one) => one.textContent)).toEqual([
			'media',
			'nas'
		]);
	});

	it('has no way back, because there is nothing above it', () => {
		const where = render(picker(TOP));

		expect(where.querySelector('.up')).toBeNull();
	});

	it('draws no breadcrumb band, since there is no place to name yet', () => {
		const where = render(picker(TOP));

		expect(where.querySelector('.crumbs')).toBeNull();
	});

	it('draws one box, not a box inside a box', () => {
		const where = render(picker(TOP));

		expect(where.querySelectorAll('.picker')).toHaveLength(1);
	});

	/* The footnote is about a FOLDER. Drawn over the list of granted folders it would be counting
	 * the files in something that is not one, and the server answers zero there, so it would say
	 * "this folder is empty" about somebody's whole set of libraries. */
	it('says nothing about file counts', () => {
		const where = render(picker({ ...TOP, fileCount: 0 }));

		expect(where.textContent).not.toContain('empty');
		expect(where.textContent).not.toContain('file');
	});
});

describe('inside a granted folder', () => {
	it('offers the way back', () => {
		const where = render(picker(INSIDE));

		expect(where.querySelector('.up')).not.toBeNull();
	});

	it('asks the store to go back rather than working out a path itself', () => {
		const one = picker(INSIDE);
		const where = render(one);

		(where.querySelector('.up') as HTMLElement).click();

		expect(one.up).toHaveBeenCalled();
	});

	it('says how many files are here, without naming any', () => {
		const where = render(picker(INSIDE));

		expect(where.textContent).toContain('3 files');
	});

	it('draws the breadcrumb it was given', () => {
		const where = render(picker(INSIDE));

		expect(where.querySelector('.crumbs')?.textContent).toContain('media');
	});
});

describe('gathering several folders together', () => {
	it('offers a tick per row only where a set is being gathered', () => {
		expect(render(picker(TOP), false).querySelectorAll('.tick')).toHaveLength(0);
		if (mounted) void unmount(mounted);
		mounted = null;
		host.remove();
		expect(render(picker(TOP), true).querySelectorAll('.tick')).toHaveLength(2);
	});

	it('ticking a folder chooses it without leaving where you are', () => {
		const one = picker(TOP);
		const where = render(one, true);

		// `Checkbox` draws a button inside the row's tick slot, not an input.
		(where.querySelector('.tick button.box') as HTMLButtonElement).click();

		expect(one.choose).toHaveBeenCalledWith(TOP.entries[0]);
		expect(one.open).not.toHaveBeenCalled();
	});
});

describe('when a request failed', () => {
	it('says so where the person is looking', () => {
		const where = render(picker({ ...INSIDE, failed: 'That folder is not one Sift was given.' }));

		expect(where.textContent).toContain('That folder is not one Sift was given.');
	});
});
