/* Backup and restore, as far as the Database Switcher concerns it.
 *
 * The switcher is drawn on THIS pane, after putting a backup back, so a settings search for it,
 * which opens this section and rings `backup.switcher`, finds a heading to ring.
 */

import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { flushSync, mount } from 'svelte';
import Backup from './Backup.svelte';

const canSwitchLibrary = vi.hoisted(() => vi.fn(() => false));
/* Whether this window is the Sift app on the computer running Sift, which can show a folder. */
const canShowInFolder = vi.hoisted(() => vi.fn(() => false));
const showInFolder = vi.hoisted(() => vi.fn(async () => true));

/* The backup the last Save a backup wrote, as the server answered. None unless a test fills it. */
const savedNow = vi.hoisted(() => ({
	backup: null as null | { name: string; folder: string; path: string },
	copied: [] as string[]
}));

/* What the next backup holds, as the disk reported it. Empty unless a test fills it. */
const parts = vi.hoisted(() => ({
	now: [] as { label: string; files: number; bytes: number; included: boolean }[]
}));

vi.mock('$lib/bridge', () => ({
	bridge: {
		canSwitchLibrary,
		canShowInFolder,
		showInFolder,
		libraries: vi.fn(async () => ({ current: null, libraries: [] })),
		openLibrary: vi.fn(),
		addLibrary: vi.fn(),
		forgetLibrary: vi.fn()
	}
}));

/* The backups no rule deletes, as the server listed them. Empty unless a test fills it. */
const unmarkedNow = vi.hoisted(() => ({
	list: [] as { name: string; taken_at: number; size_bytes: number }[],
	bin: false
}));
const deleteUnmarked = vi.hoisted(() => vi.fn(async () => {}));

/* The two rows about the FILE, declared only when a test asks for them. */
const declared = vi.hoisted(() => ({ rows: false }));

const get = vi.hoisted(() => vi.fn(async () => ({ folder: '', can_switch: true, libraries: [] })));

vi.mock('$lib/api/client', () => ({
	ApiError: class extends Error {},
	api: { get, post: vi.fn() },
	// Read by a link's hunt for its row, which waits while any request is out.
	requestsInFlight: () => 0
}));

vi.mock('./backup-state.svelte', () => ({
	// The database switcher's copy is watched by its job type, read from the same module.
	DUPLICATE_JOB: 'library_duplicate',
	Backup: class {
		keep = 3;
		keepDays = 7;
		folder = '';
		parts = parts.now;
		unmarked = unmarkedNow.list;
		recycleBin = unmarkedNow.bin;
		problem = null;
		done = null;
		busy = false;
		loading = false;
		loaded = true;
		besideSiftData = false;
		async load() {}
		async loadContents() {}
		async loadUnmarked() {}
		deleteUnmarked = deleteUnmarked;
		saved = savedNow.backup;
		copyOf(name: string) {
			savedNow.copied.push(name);
		}
		async exportNow() {}
		async saveSchedule() {}
		async restore() {}
	}
}));

vi.mock('./panel.svelte', () => ({
	SettingsPanel: class {
		loading = false;
		async load() {}
		value() {
			return undefined;
		}
		entry(key: string) {
			if (!declared.rows) return undefined;
			if (key === 'backup.keep') {
				return { key, value: 3, default: 7, minimum: 1, maximum: 30, label: 'Backups to keep' };
			}
			if (key === 'backup.folder') return { key, value: '', default: '', label: 'Backup folder' };
			return undefined;
		}
		async save() {}
	}
}));

vi.mock('$lib/settings-ui/settings-view', () => ({ showSettingsSection: vi.fn() }));

/* The backup task's row reads the shared task list; nothing here fetches it. Its last run is
   filled only by the test that asks about it. */
const backupRow = vi.hoisted(() => ({
	last: null as null | { ended_at: number; outcome: string; said: string | null }
}));

vi.mock('$lib/jobs/tasks.svelte', () => ({
	taskList: {
		row: (id: string) =>
			id === 'backup' && backupRow.last ? { id, last: backupRow.last } : undefined,
		pressing: {},
		failed: false,
		ensure: async () => {},
		setWhen: async () => {}
	},
	pressTask: vi.fn(async () => true)
}));

let host: HTMLDivElement;

beforeEach(() => {
	host = document.createElement('div');
	document.body.append(host);
});

afterEach(() => {
	host.remove();
	parts.now = [];
	unmarkedNow.list = [];
	unmarkedNow.bin = false;
	declared.rows = false;
	backupRow.last = null;
	savedNow.backup = null;
	savedNow.copied = [];
	canShowInFolder.mockReturnValue(false);
	vi.clearAllMocks();
});

async function draw() {
	mount(Backup, { target: host });
	for (let turn = 0; turn < 8; turn += 1) await Promise.resolve();
	flushSync();
}

it('draws the Database Switcher as its own section, after restoring', async () => {
	await draw();

	const headings = [...host.querySelectorAll('h2')].map((one) => one.id || one.textContent);
	expect(headings).toContain('backup.switcher');
	expect(headings.indexOf('backup.switcher')).toBeGreaterThan(headings.indexOf('backup.restore'));
});

/* A browser is not told to go to the app: the switcher asks the SERVER, which is on the
   computer holding the libraries wherever the page is read from. */
it('draws the switcher in a browser too, reading the list from the server', async () => {
	await draw();

	expect(host.textContent).toContain('Sift opens one library at a time');
	expect(get).toHaveBeenCalledWith('/libraries');
});

it('draws saving and restoring as rows, with the button on the right, never under a paragraph', async () => {
	await draw();

	const save = [...host.querySelectorAll('button')].find((one) =>
		one.textContent?.includes('Save a backup')
	);
	expect(save?.closest('.ruled-row'), 'Save a backup sits in a row').toBeTruthy();
	expect(host.textContent).toContain('Your whole library, in one file');
	expect(host.textContent).toContain('A backup file');
});

it('groups the count of what a backup holds, as every other count is', async () => {
	parts.now = [
		{ label: 'Confirmed faces', files: 500, bytes: 511 * 1024, included: true },
		{ label: 'Detected face pictures', files: 24000, bytes: 21 * 1024 * 1024, included: true }
	];
	await draw();

	expect(host.textContent).toContain(
		'confirmed faces (500 files, 511 KB), detected face pictures (24,000 files, 21 MB)'
	);
});

it('draws how many to keep and where, editable, while the cadence under Tasks is off', async () => {
	/* The cadence is edited under Tasks. A row here greyed by a choice made there reads as a
	   broken control, so the two rows about the file stay live and the line above them says where
	   the cadence is. */
	declared.rows = true;
	await draw();

	const keep = host.querySelector('[id="backup.keep"]');
	const folder = host.querySelector('[id="backup.folder"]');
	expect(keep).not.toBeNull();
	expect(folder).not.toBeNull();
	for (const row of [keep, folder]) {
		expect(row?.classList.contains('inert')).toBe(false);
		expect(row?.querySelector('input:disabled, button:disabled')).toBeNull();
	}
	expect(host.textContent).toContain('Choose how often backups run under');
});

it('says when the last automatic backup ran, from the task on record, beside where to set it', async () => {
	backupRow.last = {
		ended_at: Math.floor(Date.now() / 1000) - 3 * 3600,
		outcome: 'done',
		said: null
	};
	await draw();

	expect(host.textContent?.replace(/\s+/g, ' ')).toContain(
		'The last automatic backup ran 3 hours ago. Choose how often backups run under'
	);
});

it('says how the count and the age read together, under the rows', async () => {
	await draw();

	expect(host.querySelector('[data-kept]')?.textContent).toBe(
		'Sift keeps the newest 3 automatic backups, and none older than 7 days.'
	);
});

it('draws nothing about unmarked backups where the folder holds none', async () => {
	await draw();

	expect(host.querySelector('[id="backup.unmarked"]')).toBeNull();
});

it('answers a link to the unmarked backups where there are none by ringing Save a backup and saying why', async () => {
	host.className = 'section-body';
	await draw();
	const { toasts } = await import('$lib/shell/toasts.svelte');
	const { revealSetting } = await import('./settings-anchor.svelte');
	const said = vi.spyOn(toasts, 'show');
	const scrolled = Element.prototype.scrollIntoView;
	Element.prototype.scrollIntoView = vi.fn();
	try {
		await expect(revealSetting('backup.unmarked')).resolves.toBe(true);
		expect(said).toHaveBeenCalledWith(
			"Every backup in the folder is one a rule keeps, so there's none to list."
		);
		await vi.waitFor(() =>
			expect(host.querySelector<HTMLElement>('[id="backup.now"]')?.dataset.siftFound).toBe('')
		);
	} finally {
		Element.prototype.scrollIntoView = scrolled;
		said.mockRestore();
	}
});

it('lists the backups no rule deletes, each with its size and a Delete behind a confirm', async () => {
	unmarkedNow.list = [
		{
			name: 'sift-backup-20260918-030012-0.1.198.zip',
			taken_at: 1789714812,
			size_bytes: 412_381_118
		}
	];
	await draw();

	expect(host.querySelector('[id="backup.unmarked"]')?.textContent).toBe(
		'Backups Sift leaves alone'
	);
	expect(host.textContent).toContain(
		"Sift never deletes these by itself: you saved them, or their names don't say which library made them."
	);
	expect(host.textContent).toContain('sift-backup-20260918-030012-0.1.198.zip');
	expect(host.textContent).toContain('393 MB');
	const remove = [...host.querySelectorAll('button')].find((one) =>
		one.getAttribute('aria-label')?.startsWith('Delete the backup from ')
	);
	expect(remove?.textContent).toContain('Delete');

	remove?.click();
	flushSync();
	expect(deleteUnmarked).not.toHaveBeenCalled();
	expect(document.body.textContent).toContain(
		"sift-backup-20260918-030012-0.1.198.zip is deleted permanently: the backup folder's drive has no Recycle Bin."
	);
});

/* SAVE A BACKUP SAYS WHERE IT WENT. The press writes into the backup folder on the computer
 * running Sift; the pane says that folder, and offers the one way this device has to reach it. */
const SAVED = {
	name: 'sift-backup-0123456789ab-20260720-141500-0400-1.2.3-saved.zip',
	folder: 'C:\\Sift\\data\\backups',
	path: 'C:\\Sift\\data\\backups\\sift-backup-0123456789ab-20260720-141500-0400-1.2.3-saved.zip'
};

it('says the folder a saved backup went to, and hands a browser a copy only when asked', async () => {
	savedNow.backup = SAVED;
	await draw();

	const said = host.querySelector('.saved');
	expect(said?.textContent).toContain('Saved in C:\\Sift\\data\\backups.');
	const copy = [...host.querySelectorAll('button')].find((one) =>
		one.textContent?.includes('Download a copy')
	);
	expect(copy, 'a browser is offered a copy').toBeTruthy();
	expect(savedNow.copied).toEqual([]);
	copy?.click();
	expect(savedNow.copied).toEqual([SAVED.name]);
	expect(host.textContent).not.toContain('Show in folder');
});

it('shows the folder itself in the Sift app on the computer running Sift', async () => {
	canShowInFolder.mockReturnValue(true);
	savedNow.backup = SAVED;
	await draw();

	const show = [...host.querySelectorAll('button')].find((one) =>
		one.textContent?.includes('Show in folder')
	);
	expect(show).toBeTruthy();
	show?.click();
	expect(showInFolder).toHaveBeenCalledWith(SAVED.path);
	expect(host.textContent).not.toContain('Download a copy');
});
