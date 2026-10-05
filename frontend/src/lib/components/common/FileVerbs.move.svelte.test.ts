import { flushSync, mount, unmount } from 'svelte';
import { afterEach, expect, it, vi } from 'vitest';
import { ApiError } from '$lib/api/client';
import { movable } from '$lib/library/movable.svelte';
import { session, type Viewer } from '$lib/shell/session.svelte';
import { toasts } from '$lib/shell/toasts.svelte';
import Probe from './FileVerbsDoorsProbe.test.svelte';
import { Selection } from './selection.svelte';
import { flatVerbs, type Verb } from './verbs';

const get = vi.hoisted(() => vi.fn());

vi.mock('$lib/api/client', async (importOriginal) => ({
	...(await importOriginal<typeof import('$lib/api/client')>()),
	api: { get, post: vi.fn(async () => ({})), put: vi.fn(async () => ({})), del: vi.fn() }
}));

const ROOTS = { roots: [{ id: 'r-fen', name: 'Fen' }] };
const FOLDERS = {
	folders: [
		{
			id: 'd-reeds',
			root_id: 'r-fen',
			parent_id: null,
			name: 'Reeds',
			rel_path: 'Reeds',
			writable: true
		}
	]
};

let instance: ReturnType<typeof mount> | null = null;

afterEach(() => {
	if (instance) unmount(instance);
	instance = null;
	document.body.innerHTML = '';
	session.viewer = undefined;
	vi.restoreAllMocks();
});

it("says the folders couldn't be read, not that every folder is read-only", async () => {
	let failing = false;
	get.mockImplementation(async (path: string) => {
		if (path === '/library/roots') return ROOTS;
		if (path === '/library/folders') {
			if (failing) throw new ApiError(500, 'Internal Server Error');
			return FOLDERS;
		}
		return {};
	});
	const shown = vi.spyOn(toasts, 'show').mockImplementation(() => 0 as never);
	session.viewer = { role: 'admin', can_save_to_device: true } as Viewer;
	movable.forget();
	await movable.ensure();

	let menu: ((ids: string[], subjectId?: string) => Verb[]) | null = null;
	instance = mount(Probe, {
		target: document.body,
		props: {
			items: [
				{
					id: 'f-otter',
					media_type: 'video',
					favorite: false,
					rating: null,
					concealed: false,
					original_filename: 'f-otter.mp4'
				}
			],
			selection: new Selection(),
			ondoors: (doors: { menu: typeof menu }) => (menu = doors.menu)
		}
	});
	flushSync();
	const move = flatVerbs(menu!(['f-otter'], 'f-otter')).find((verb) => verb.id === 'move');
	expect(move, 'Move is offered once the folders are read').toBeDefined();

	failing = true;
	movable.loaded = false;
	move!.run!(['f-otter']);
	await vi.waitFor(() => expect(shown).toHaveBeenCalled());

	const said = shown.mock.calls.map((call) => String(call[0]));
	expect(said).toContain("Sift couldn't read your folders");
	expect(said.join(' ')).not.toContain('read-only');
});
