/* Settings > Music: two tasks, two rows. Making the fingerprints, and asking AcoustID about
 * them. */

import { afterEach, beforeAll, beforeEach, expect, it, vi } from 'vitest';
import type { TaskView, TasksView } from '$lib/jobs/tasks.svelte';

const mocks = vi.hoisted(() => ({
	get: vi.fn(),
	post: vi.fn(),
	fetchSettings: vi.fn(),
	saveSettings: vi.fn()
}));

vi.mock('$lib/api/client', async (importOriginal) => ({
	...(await importOriginal<typeof import('$lib/api/client')>()),
	api: { get: mocks.get, post: mocks.post }
}));

vi.mock('$lib/settings-ui/settings', async (importOriginal) => ({
	...(await importOriginal<typeof import('$lib/settings-ui/settings')>()),
	fetchSettings: mocks.fetchSettings,
	saveSettings: mocks.saveSettings
}));

let svelte: typeof import('svelte');

const WHENS = [
	{ value: 'work', label: 'As files arrive' },
	{ value: 'quiet', label: 'During quiet hours' },
	{ value: 'press', label: 'Only when I press it' }
];

function task(id: string, title: string, explain: string): TaskView {
	return {
		id,
		title,
		explain,
		unit: 'file',
		units: 'files',
		when: 'press',
		when_key: `tasks.${id}.when`,
		whens: WHENS,
		on: false,
		cadence: 'Only when you run it',
		setting_keys: [],
		drawn_keys: [],
		set_in: 'music',
		last: null,
		next_run: null,
		waiting: 0,
		held: 0,
		running: false,
		press: 'Run now',
		reads: [],
		off: null,
		parts: [],
		locations: false,
		dry: true,
		dry_run: null,
		dry_running: false
	};
}

const TASKS: TasksView = {
	quiet_hours: { starts: '23:00', ends: '07:00', open: false, opens_at: 0, closes_at: null },
	keep_awake: false,
	awake_now: false,
	tasks: [
		task('music', 'Generate music fingerprints', "Generating one reads the file's sound track."),
		task(
			'music-lookup',
			'Name songs with AcoustID',
			"Sends each file's music fingerprint and length to AcoustID, never the file."
		)
	],
	folders: []
};

const SWITCH = {
	key: 'music.lookup',
	value: false,
	default: false,
	label: 'Name songs with AcoustID',
	help: 'Sift asks AcoustID which song a full-length file uses.',
	disclosure: "Sends a fingerprint of the file's sound and its length to AcoustID, never the file."
};
const ROUTE = {
	key: 'music.lookup_route',
	value: null,
	default: null,
	label: 'Connect to AcoustID through',
	help: 'A tunnel you added under Sites.'
};

beforeAll(async () => {
	await import('./Music.svelte');
}, 60_000);

let host: HTMLElement;
let drawn: Record<string, unknown> | null = null;

beforeEach(async () => {
	vi.resetModules();
	svelte = await import('svelte');
	vi.clearAllMocks();
	mocks.fetchSettings.mockResolvedValue([{ name: 'Music', settings: [SWITCH, ROUTE] }]);
	host = document.createElement('div');
	document.body.append(host);
});

afterEach(() => {
	if (drawn) svelte.unmount(drawn);
	drawn = null;
	host.remove();
});

async function shown(on: boolean, owed: number, notKnown = 0, again = 0): Promise<string> {
	mocks.get.mockImplementation(async (path: string) => {
		if (path === '/music/lookup')
			return {
				on,
				key_set: on,
				key_ready: on,
				route: null,
				owed: on ? owed : 0,
				not_known: on ? notKnown : 0,
				ask_again: on ? again : 0,
				ask_again_after_days: 30,
				ready: on
			};
		if (path.startsWith('/tasks')) return TASKS;
		if (path.startsWith('/tunnels')) return [];
		if (path.startsWith('/download-routes')) return { default: 'direct', sites: {}, available: [] };
		if (path.startsWith('/auth')) return {};
		throw new Error(`unexpected read of ${path}`);
	});
	const { default: Music } = await import('./Music.svelte');
	drawn = svelte.mount(Music, { target: host }) as Record<string, unknown>;
	await vi.waitFor(() => expect(host.querySelector('[id="tasks.music.when"]')).not.toBeNull());
	await vi.waitFor(() => expect(host.textContent).toContain('Only when I press it'));
	return (host.textContent ?? '').replace(/\s+/g, ' ');
}

it("draws the lookup task's own row while the lookup is on, with what waits and where it is run", async () => {
	const words = await shown(true, 12, 3);
	const row = host.querySelector('[id="tasks.music-lookup.when"]');
	expect(row, "the lookup task's row").not.toBeNull();
	expect(host.querySelector('[id="music.acoustid-when"]')?.contains(row)).toBe(true);
	expect(words).toContain('When songs are looked up');
	expect(words).toContain('12 files have a music fingerprint and no song yet.');
	// Asked and not known: said apart, and never in the count of what is still owed.
	expect(words).toContain("AcoustID didn't know the song in 3 files.");
	expect(words).toContain('Run it in Tasks and Activity.');
	const link = [...host.querySelectorAll('a')].find(
		(one) => one.textContent?.trim() === 'Tasks and Activity'
	);
	expect(link?.getAttribute('href')).toContain('/settings/tasks');
	// No press on the pane: the library's fingerprints leave only from Tasks or a chosen When.
	const presses = [...host.querySelectorAll('button')].map((one) => one.textContent?.trim());
	expect(presses).not.toContain('Run now');
	expect(mocks.post).not.toHaveBeenCalled();
});

it('counts the files Ask again would ask about, and asks only when pressed', async () => {
	const words = await shown(true, 0, 5, 2);
	const row = host.querySelector('[id="music.acoustid-again"]');
	expect(row, 'the Ask again row').not.toBeNull();
	expect(host.querySelector('[id="music.acoustid-when"]')?.contains(row)).toBe(true);
	expect(words).toContain("2 files AcoustID didn't know can be asked again.");
	expect(mocks.post).not.toHaveBeenCalled();
	mocks.post.mockResolvedValue({
		job_id: 'j1',
		queued: 2,
		left_out: 0,
		said: 'Asking AcoustID again about 2 files.'
	});
	const press = [...(row?.querySelectorAll('button') ?? [])].find(
		(one) => one.textContent?.trim() === 'Ask again'
	);
	press?.click();
	await vi.waitFor(() => expect(mocks.post).toHaveBeenCalledWith('/music/lookup/again'));
});

it('draws Ask again refused while no file is waiting to be asked', async () => {
	const words = await shown(true, 0, 5, 0);
	expect(words).toContain('No file is waiting to be asked again.');
	const press = [...host.querySelectorAll('[id="music.acoustid-again"] button')].find(
		(one) => one.textContent?.trim() === 'Ask again'
	);
	expect((press as HTMLButtonElement | undefined)?.disabled).toBe(true);
});

it('draws no lookup row while the lookup is off, and the fingerprints row either way', async () => {
	await shown(false, 0);
	expect(host.querySelector('[id="tasks.music-lookup.when"]')).toBeNull();
	expect(host.querySelector('[id="tasks.music.when"]')).not.toBeNull();
});

it('answers a link to a lookup row while the lookup is off by ringing its switch and saying why', async () => {
	host.className = 'section-body';
	await shown(false, 0);
	const { toasts } = await import('$lib/shell/toasts.svelte');
	const { revealSetting } = await import('$lib/settings-ui/settings-anchor.svelte');
	const show = vi.spyOn(toasts, 'show');
	const scrolled = Element.prototype.scrollIntoView;
	Element.prototype.scrollIntoView = vi.fn();
	try {
		for (const key of ['music.acoustid-when', 'music.acoustid-again']) {
			show.mockClear();
			await expect(revealSetting(key), key).resolves.toBe(true);
			expect(show).toHaveBeenCalledWith(
				expect.stringContaining('is hidden while \u201cName songs with AcoustID\u201d')
			);
		}
		// A key the pane draws whatever the lookup says is not explained away.
		await expect(revealSetting('tasks.music.when')).resolves.toBe(true);
		expect(show).toHaveBeenCalledTimes(1);
	} finally {
		Element.prototype.scrollIntoView = scrolled;
	}
});
