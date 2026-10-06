/* Modify opens on the press from the record a file's sheet holds, then reads it fresh in place. */

import { flushSync, mount, unmount } from 'svelte';
import { afterEach, expect, it, vi } from 'vitest';
import { session, type Viewer } from '$lib/shell/session.svelte';
import Probe from './FileVerbsDoorsProbe.test.svelte';
import { Selection } from './selection.svelte';
import { flatVerbs, type Verb } from './verbs';

const get = vi.hoisted(() => vi.fn());

vi.mock('$lib/api/client', async (importOriginal) => ({
	...(await importOriginal<typeof import('$lib/api/client')>()),
	api: { get, post: vi.fn(async () => ({})), put: vi.fn(async () => ({})), del: vi.fn() }
}));

let instance: ReturnType<typeof mount> | null = null;

afterEach(() => {
	if (instance) unmount(instance);
	instance = null;
	document.body.innerHTML = '';
	session.viewer = undefined;
	vi.restoreAllMocks();
});

const RECORD = {
	id: 'f-heron',
	media_type: 'image',
	favorite: false,
	rating: null,
	concealed: false,
	original_filename: 'heron.jpg',
	filename: 'heron.jpg',
	width: 1200,
	height: 800,
	duration_ms: null,
	art: null,
	sprite: null
};

function menuFor(items: object[]): (ids: string[], subjectId?: string) => Verb[] {
	let menu: ((ids: string[], subjectId?: string) => Verb[]) | null = null;
	instance = mount(Probe, {
		target: document.body,
		props: {
			items: items as never,
			selection: new Selection(),
			ondoors: (doors: { menu: typeof menu }) => (menu = doors.menu)
		}
	});
	flushSync();
	return menu!;
}

it('opens Modify on the press from the held record, before the fresh read answers', async () => {
	let answer: (value: unknown) => void = () => {};
	get.mockImplementation((path: string) =>
		path === '/assets/f-heron' ? new Promise((resolve) => (answer = resolve)) : Promise.resolve({})
	);
	session.viewer = { role: 'admin', can_save_to_device: true } as Viewer;
	const menu = menuFor([RECORD]);
	const modify = flatVerbs(menu(['f-heron'], 'f-heron')).find((verb) => verb.id === 'edit');
	expect(modify, 'Modify is offered on a picture').toBeDefined();

	modify!.run!(['f-heron']);
	flushSync();
	expect(document.body.textContent).toContain('Modify this picture');
	expect(get).toHaveBeenCalledWith('/assets/f-heron');
	answer({ ...RECORD });
});

it('waits for the read where nothing held carries the record, as a grid tile does', async () => {
	get.mockImplementation(() => new Promise(() => {}));
	session.viewer = { role: 'admin', can_save_to_device: true } as Viewer;
	const { filename: _name, sprite: _sprite, ...tile } = RECORD;
	const menu = menuFor([tile]);
	const modify = flatVerbs(menu(['f-heron'], 'f-heron')).find((verb) => verb.id === 'edit');
	modify!.run!(['f-heron']);
	flushSync();
	expect(document.body.textContent).not.toContain('Modify this picture');
});
