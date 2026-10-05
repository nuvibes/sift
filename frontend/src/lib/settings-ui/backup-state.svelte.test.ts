import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { setCsrfToken } from '$lib/api/client';
import { Backup, copyAddress, type ScheduleView } from './backup-state.svelte';
import { toasts } from '$lib/shell/toasts.svelte';

/* Backup, as the settings screen sees it.
 *
 * The behaviour worth testing here is what the screen says when it does not know something. A
 * schedule it failed to read looks exactly like a schedule that is off, and telling somebody
 * automatic backups are off when the truth is that nobody could ask is the reassuring reading of
 * silence, which is the wrong one for a screen about not losing things.
 *
 * The other half is that restoring never happens by itself. It replaces the library's records and
 * cannot be undone from this screen, so nothing here calls it except the confirm.
 */

const fetchMock = vi.fn();
const clickMock = vi.fn();
const createObjectURL = vi.fn(() => 'blob:sift');
const revokeObjectURL = vi.fn();

const SCHEDULE: ScheduleView = {
	every_days: 1,
	at: '03:00',
	keep: 5,
	keep_days: 0,
	folder: '/media/nas/backups',
	beside_sift_data: false,
	working: null
};

beforeEach(() => {
	vi.stubGlobal('fetch', fetchMock);
	vi.stubGlobal('window', { location: { origin: 'http://sift.test' } });
	vi.stubGlobal('URL', Object.assign(URL, { createObjectURL, revokeObjectURL }));
	vi.stubGlobal('document', {
		createElement: () => ({ click: clickMock, href: '', download: '' })
	});
	fetchMock.mockReset();
	clickMock.mockReset();
	createObjectURL.mockClear();
	revokeObjectURL.mockClear();
	setCsrfToken('a-token');
});

afterEach(() => {
	vi.restoreAllMocks();
	vi.unstubAllGlobals();
	setCsrfToken(null);
});

function answers(...responses: Array<{ ok: boolean; status?: number; body?: unknown }>) {
	for (const response of responses) {
		fetchMock.mockResolvedValueOnce({
			ok: response.ok,
			status: response.status ?? (response.ok ? 200 : 409),
			json: async () => response.body ?? {}
		});
	}
}

describe('reading the schedule', () => {
	it('takes what the server says', async () => {
		answers({ ok: true, body: SCHEDULE });
		const view = new Backup();

		await view.load();

		expect(view.keep).toBe(5);
		expect(view.folder).toBe('/media/nas/backups');
		expect(view.besideSiftData).toBe(false);
		expect(view.loaded).toBe(true);
		expect(view.working).toBeNull();
	});

	it('takes the whole-library work the server says is running, a save from elsewhere included', async () => {
		answers({ ok: true, body: { ...SCHEDULE, working: 'backup' } });
		const view = new Backup();

		await view.load();

		expect(view.working).toBe('backup');
	});

	it('does not claim backups are off when it could not ask', async () => {
		/* The one that matters. `loaded` staying false is what lets the screen say "Sift could not
		   read your backup settings" rather than drawing a form that says Never. */
		answers({ ok: false, status: 500 });
		const view = new Backup();

		await view.load();

		expect(view.loaded).toBe(false);
		expect(view.problem).toBeTruthy();
	});
});

describe('saving a backup now', () => {
	const SAVED = {
		name: 'sift-backup-0123456789ab-20260720-141500-0400-1.2.3-saved.zip',
		folder: 'C:\\Users\\somebody\\AppData\\Local\\Sift\\data\\backups',
		path: 'C:\\Users\\somebody\\AppData\\Local\\Sift\\data\\backups\\sift-backup-0123456789ab-20260720-141500-0400-1.2.3-saved.zip',
		taken_at: 1784556900,
		size_bytes: 2048
	};

	it('saves into the backup folder and keeps where it went, handing nothing to the browser', async () => {
		answers({ ok: true, body: SAVED }, { ok: true, body: { backups: [], recycle_bin: false } });
		const said = vi.spyOn(toasts, 'show');
		const view = new Backup();

		const running = view.exportNow();
		// Its own press turns its arc; nothing else on the pane does.
		expect(view.exporting).toBe(true);
		await running;
		expect(view.exporting).toBe(false);
		// Said in a toast as well, which outlives the pane.
		expect(said).toHaveBeenCalledWith(`Saved a backup in ${SAVED.folder}`);

		expect(String(fetchMock.mock.calls[0][0])).toContain('/backup/export');
		expect(view.saved).toEqual(SAVED);
		expect(view.problem).toBeNull();
		// Nothing was downloaded: a copy for this device is its own press.
		expect(clickMock).not.toHaveBeenCalled();
		// And the list of backups no rule takes is read again, so the new one is on it.
		expect(String(fetchMock.mock.calls[1][0])).toContain('/backup/unmarked');
	});

	it('is still saving when the pane is opened again, and says where it went when it ends', async () => {
		let answer: (value: unknown) => void = () => {};
		fetchMock.mockReturnValueOnce(new Promise((ok) => (answer = ok)));
		fetchMock.mockResolvedValue({ ok: true, status: 200, json: async () => ({}) });
		const view = new Backup();

		const running = view.exportNow();
		view.arrive();
		expect(view.exporting).toBe(true);
		expect(view.busy).toBe(true);

		answer({ ok: true, status: 200, json: async () => SAVED });
		await running;
		expect(view.exporting).toBe(false);
		expect(view.saved).toEqual(SAVED);
		view.arrive();
		expect(view.saved).toBeNull();
	});

	it('hands a copy to this device only when asked, by the name the list shows', () => {
		const view = new Backup();

		view.copyOf(SAVED.name);

		expect(clickMock).toHaveBeenCalledOnce();
		expect(copyAddress(SAVED.name)).toBe(`/api/backup/saved/${encodeURIComponent(SAVED.name)}`);
	});

	it('says the server-s own reason when it could not take one, and keeps nothing', async () => {
		answers({ ok: false, status: 409, body: { detail: 'A restore is running.' } });
		const said = vi.spyOn(toasts, 'show');
		const view = new Backup();

		await view.exportNow();

		expect(view.saved).toBeNull();
		expect(said).toHaveBeenCalledWith('A restore is running.', { tone: 'error' });
	});
});

describe('saving the schedule', () => {
	it('reports back what the server stored rather than what was typed', async () => {
		/* The server is what decides: it may refuse a folder, and the screen has to show the state
		   that really took rather than the one somebody was in the middle of. */
		answers({ ok: true, body: { ...SCHEDULE, keep: 3, keep_days: 7 } });
		const view = new Backup();
		view.keep = 99;
		view.keepDays = 0;

		await view.saveSchedule();

		expect(view.keep).toBe(3);
		expect(view.keepDays).toBe(7);
		/* Only what this pane draws: How often and the time of day are the task's rows on Tasks,
		   and a body without them keeps what is stored there. */
		const sent = JSON.parse(String(fetchMock.mock.calls[0][1].body));
		expect(Object.keys(sent).sort()).toEqual(['folder', 'keep', 'keep_days']);
		expect(sent.keep_days).toBe(0);
		// Saved quietly, as every other setting is.
		expect(view.done).toBeNull();
	});

	it('saves a rule the moment it changes, each save carrying the values of its own moment', async () => {
		answers(
			{ ok: true, body: { ...SCHEDULE, keep: 6 } },
			{ ok: true, body: { backups: [], recycle_bin: false } },
			{ ok: true, body: { ...SCHEDULE, keep: 6, keep_days: 3 } },
			{ ok: true, body: { backups: [], recycle_bin: false } }
		);
		const view = new Backup();
		view.keep = 5;

		const first = view.saveRules({ keep: 6 });
		const second = view.saveRules({ keepDays: 3 });
		await Promise.all([first, second]);

		const puts = fetchMock.mock.calls.filter(([, init]) => init?.method === 'PUT');
		expect(puts.map(([, init]) => JSON.parse(String(init.body)))).toEqual([
			{ keep: 6, keep_days: 7, folder: '' },
			{ keep: 6, keep_days: 3, folder: '' }
		]);
		expect(view.keepDays).toBe(3);
	});

	it('says the refusal in the server-s words and goes back to what is stored', async () => {
		answers(
			{ ok: false, status: 409, body: { detail: 'Sift cannot write to that folder.' } },
			{ ok: true, body: SCHEDULE }
		);
		const said = vi.spyOn(toasts, 'show');
		const view = new Backup();

		await view.saveRules({ folder: 'Z:\\nowhere' });

		expect(said).toHaveBeenCalledWith('Sift cannot write to that folder.', { tone: 'error' });
		expect(view.folder).toBe(SCHEDULE.folder);
		expect(view.done).toBeNull();
	});
});

describe('restoring', () => {
	it('sends the file and then re-reads the schedule from the restored install', async () => {
		answers(
			{ ok: true, body: { app_version: '1.2.3', created_at: 1_700_000_000 } },
			{ ok: true, body: SCHEDULE }
		);
		const view = new Backup();

		await view.restore(new File(['a database'], 'backup.sqlite3'));

		expect(fetchMock.mock.calls[0][1].body).toBeInstanceOf(FormData);
		expect(view.done).toContain('Re-scan your folders');
		// The settings shown afterwards are the restored install's, not the ones from before.
		expect(view.folder).toBe('/media/nas/backups');
	});

	it('passes on the server refusal for a backup from a newer Sift', async () => {
		answers({
			ok: false,
			status: 422,
			body: { detail: 'This backup was made by a newer version of Sift than the one running.' }
		});
		const view = new Backup();

		await view.restore(new File(['a database'], 'backup.sqlite3'));

		expect(view.problem).toContain('newer version of Sift');
		expect(view.done).toBeNull();
	});
});

describe('the backups no rule deletes', () => {
	const LISTED = {
		backups: [{ name: 'sift-backup-20260918-030012-0.1.198.zip', taken_at: 1, size_bytes: 9 }],
		recycle_bin: true
	};

	it('reads the list and what a Delete does', async () => {
		answers({ ok: true, body: LISTED });
		const view = new Backup();

		await view.loadUnmarked();

		expect(String(fetchMock.mock.calls[0][0])).toContain('/backup/unmarked');
		expect(view.unmarked).toEqual(LISTED.backups);
		expect(view.recycleBin).toBe(true);
	});

	it('deletes one by its name and takes the list the server answers', async () => {
		answers({ ok: true, body: LISTED }, { ok: true, body: { backups: [], recycle_bin: true } });
		const view = new Backup();
		await view.loadUnmarked();

		await view.deleteUnmarked(LISTED.backups[0].name);

		const [path, init] = fetchMock.mock.calls[1];
		expect(String(path)).toContain('/backup/unmarked/sift-backup-20260918-030012-0.1.198.zip');
		expect(init.method).toBe('DELETE');
		expect(view.unmarked).toEqual([]);
		expect(view.done).toBe('sift-backup-20260918-030012-0.1.198.zip is in the Recycle Bin.');
	});

	it('says what the server said when the backup is not there any more', async () => {
		answers({
			ok: false,
			status: 404,
			body: { detail: "That backup isn't in the backup folder any more." }
		});
		const view = new Backup();

		await view.deleteUnmarked('sift-backup-20260918-030012-0.1.198.zip');

		expect(view.problem).toBe("That backup isn't in the backup folder any more.");
		expect(view.done).toBeNull();
	});
});
