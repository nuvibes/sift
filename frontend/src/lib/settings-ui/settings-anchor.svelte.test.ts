/* The deep-link hunt: landing on a row and ringing it, every time.
 *
 * A fixed clock would give up while a pane on a big library is still drawing its rows: the link
 * opening the right pane and ringing nothing, silently. These drive the cases that matter and the
 * ones that must stay quiet: a row that arrives late because the pane is still loading, a row
 * folded inside a closed `<details>`, a row that is never coming (which has to SAY so), and a
 * hunt a newer link took over (which must not).
 */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const loading = vi.hoisted(() => ({ now: false }));
vi.mock('$lib/api/client', () => ({ requestsInFlight: () => loading.now }));

const shown = vi.hoisted(() => vi.fn());
vi.mock('$lib/shell/toasts.svelte', () => ({ toasts: { show: shown } }));

const { revealSetting, revealNamed, ROW_NOT_FOUND, explainAbsentRows, hiddenWhile } =
	await import('./settings-anchor.svelte');
const { byName, drilldown, filedUnder } = await import('./drilldown.svelte');

/** A settings pane, as `SettingsPane` draws one: its body carries `section-body`. */
function pane(): HTMLElement {
	const body = document.createElement('div');
	body.className = 'section-body';
	document.body.append(body);
	return body;
}

function row(key: string): HTMLElement {
	const one = document.createElement('div');
	one.id = key;
	one.scrollIntoView = vi.fn();
	return one;
}

beforeEach(() => {
	vi.useFakeTimers();
	loading.now = false;
	shown.mockClear();
});

afterEach(() => {
	vi.useRealTimers();
	document.body.innerHTML = '';
});

describe('a row that is drawn late', () => {
	it('is still found after a load of twenty seconds', async () => {
		const body = pane();
		loading.now = true;
		const found = revealSetting('performance.repair_playback');

		// Twenty seconds of a pane still waiting on its request: the hunt must not give up.
		await vi.advanceTimersByTimeAsync(20_000);
		loading.now = false;
		const late = row('performance.repair_playback');
		body.append(late);
		await vi.advanceTimersByTimeAsync(100);

		await expect(found).resolves.toBe(true);
		expect(late.dataset.siftFound).toBe('');
		expect(shown).not.toHaveBeenCalled();
	});
});

describe('a row folded away', () => {
	it('opens the closed fold it is in, then rings it', async () => {
		const body = pane();
		const fold = document.createElement('details');
		const inside = row('downloads.naming.per_site');
		fold.append(inside);
		body.append(fold);

		const found = revealSetting('downloads.naming.per_site');
		await vi.advanceTimersByTimeAsync(50);

		await expect(found).resolves.toBe(true);
		expect(fold.open).toBe(true);
		expect(inside.scrollIntoView).toHaveBeenCalled();
	});
});

describe('a row that is never coming', () => {
	it('ends once the pane has finished drawing, and says so', async () => {
		pane();
		const found = revealSetting('appearance.no_such_row');

		await vi.advanceTimersByTimeAsync(5_000);

		await expect(found).resolves.toBe(false);
		expect(shown).toHaveBeenCalledWith(ROW_NOT_FOUND);
	});

	it('ends quietly when a newer link takes over', async () => {
		pane();
		const first = revealSetting('appearance.first');
		void revealSetting('appearance.second');

		await expect(first).resolves.toBe(false);
		expect(shown).not.toHaveBeenCalled();
	});
});

describe('a row the pane pushes down as it goes on loading', () => {
	/* On Performance the "Is Sift keeping up?" row is drawn at once and the blocks above it
	   arrive afterwards, so the row would be scrolled to, rung, and then pushed two screens
	   down: a ring nobody could see. A row whose place moves while the pane is loading is put
	   back in view and rung again. */
	function movable(key: string): { target: HTMLElement; moveTo: (top: number) => void } {
		const target = row(key);
		let top = 100;
		target.getBoundingClientRect = () => ({ top }) as DOMRect;
		return { target, moveTo: (next) => (top = next) };
	}

	it('is scrolled back into view and rung again while the pane is still loading', async () => {
		const body = pane();
		const { target, moveTo } = movable('performance.keeping_up');
		body.append(target);
		loading.now = true;

		await expect(revealSetting('performance.keeping_up')).resolves.toBe(true);
		expect(target.scrollIntoView).toHaveBeenCalledTimes(1);

		moveTo(2100);
		await vi.advanceTimersByTimeAsync(100);

		expect(target.scrollIntoView).toHaveBeenCalledTimes(2);
		expect(target.dataset.siftFound).toBe('');
	});

	it('is let go the moment the person scrolls for themselves', async () => {
		const body = pane();
		const { target, moveTo } = movable('performance.keeping_up');
		body.append(target);
		loading.now = true;

		await revealSetting('performance.keeping_up');
		window.dispatchEvent(new Event('wheel'));
		moveTo(2100);
		await vi.advanceTimersByTimeAsync(100);

		expect(target.scrollIntoView).toHaveBeenCalledTimes(1);
	});

	it('is let go once the pane has finished loading', async () => {
		const body = pane();
		const { target, moveTo } = movable('performance.keeping_up');
		body.append(target);

		await revealSetting('performance.keeping_up');
		await vi.advanceTimersByTimeAsync(2_500);
		moveTo(2100);
		await vi.advanceTimersByTimeAsync(100);

		expect(target.scrollIntoView).toHaveBeenCalledTimes(1);
	});
});

describe('a row whose id a pointer on the pane being left also carries', () => {
	it('is found on the section being opened, never the pointer left behind', async () => {
		// "Change in Importing" on the Faces pane: the pointer row keeps the switch's id, and the
		// Faces pane is still mounted when the hunt starts. Ringing the pointer would end the hunt
		// and leave Importing open at its top.
		const leaving = pane();
		leaving.dataset.section = 'faces';
		const pointer = row('faces.enabled');
		leaving.append(pointer);

		const found = revealSetting('faces.enabled', 'importing');
		await vi.advanceTimersByTimeAsync(50);

		const opening = pane();
		opening.dataset.section = 'importing';
		const target = row('faces.enabled');
		opening.append(target);
		await vi.advanceTimersByTimeAsync(100);

		await expect(found).resolves.toBe(true);
		expect(target.dataset.siftFound).toBe('');
		expect(pointer.dataset.siftFound).toBeUndefined();
	});
});

describe('a row the pane is deliberately not drawing', () => {
	/* Save screenshots to is drawn only while Save is the answer, How often only while the backup
	   runs on its own. A search or a pasted path landing on one must not say the setting "may have
	   moved or been removed", which is false: the pane that leaves it out says why, and the row that
	   decides it is rung in its place. */
	it('rings the row that decides it and says why, never that it moved', async () => {
		const body = pane();
		const choice = row('playback.screenshot');
		body.append(choice);
		const why = hiddenWhile('Save screenshots to', 'When you take a screenshot', 'Copy');
		const release = explainAbsentRows((key) =>
			key === 'playback.screenshot_folder' ? { because: why, near: 'playback.screenshot' } : null
		);

		const found = revealSetting('playback.screenshot_folder');
		await vi.advanceTimersByTimeAsync(100);

		await expect(found).resolves.toBe(true);
		expect(choice.dataset.siftFound).toBe('');
		expect(shown).toHaveBeenCalledWith(why);
		expect(shown).not.toHaveBeenCalledWith(ROW_NOT_FOUND);
		release();
	});

	it('waits while the pane is still loading, so a late row is still found', async () => {
		const body = pane();
		loading.now = true;
		const release = explainAbsentRows(() => ({ because: 'hidden' }));

		const found = revealSetting('backup.every_days');
		await vi.advanceTimersByTimeAsync(3_000);
		expect(shown).not.toHaveBeenCalled();

		const late = row('backup.every_days');
		body.append(late);
		await vi.advanceTimersByTimeAsync(100);

		await expect(found).resolves.toBe(true);
		expect(late.dataset.siftFound).toBe('');
		release();
	});

	/* Scanning all files again is drawn only while one runs, and the press that starts one is on
	   Faces' More settings page: the row that decides it is opened from behind its sub-page the
	   way the key's own would be, and the sentence is said once it is rung. */
	it('opens the sub-page the deciding row is on, then rings it and says why', async () => {
		const body = pane();
		const why = 'Shown only while a scan runs.';
		const release = explainAbsentRows((key) =>
			key === 'faces.sweep' ? { because: why, near: 'faces.rescan' } : null
		);
		const start = row('faces.rescan');
		const releaseOwner = drilldown.own(['faces.rescan'], () => {
			if (!start.isConnected) body.append(start);
		});

		const found = revealSetting('faces.sweep');
		await vi.advanceTimersByTimeAsync(200);

		await expect(found).resolves.toBe(true);
		expect(start.dataset.siftFound).toBe('');
		expect(shown).toHaveBeenCalledWith(why);
		expect(shown).not.toHaveBeenCalledWith(ROW_NOT_FOUND);
		releaseOwner();
		release();
	});

	it('says why, never that it moved, when the deciding row never comes', async () => {
		pane();
		const why = 'Shown only while a scan runs.';
		const release = explainAbsentRows((key) =>
			key === 'faces.sweep' ? { because: why, near: 'faces.rescan' } : null
		);

		const found = revealSetting('faces.sweep');
		await vi.advanceTimersByTimeAsync(5_000);

		await expect(found).resolves.toBe(false);
		expect(shown).toHaveBeenCalledWith(why);
		expect(shown).not.toHaveBeenCalledWith(ROW_NOT_FOUND);
		release();
	});

	it('explains nothing once the pane that said it is gone', async () => {
		pane();
		const release = explainAbsentRows(() => ({ because: 'hidden' }));
		release();

		const found = revealSetting('backup.every_days');
		await vi.advanceTimersByTimeAsync(5_000);

		await expect(found).resolves.toBe(false);
		expect(shown).toHaveBeenCalledWith(ROW_NOT_FOUND);
	});
});

describe('a row whose choice is made in a menu', () => {
	/* A task's When is chosen in the menu behind the chevron beside Run now. Rung alone, the row
	   would leave the answers behind a press nobody had been told to make; the ring opens the menu, the
	   way the keyboard does, and following the row goes on (the hunt's own press is not somebody
	   taking over). */
	function rowWithDoor(key: string): { one: HTMLElement; door: HTMLButtonElement } {
		const one = row(key);
		const holder = document.createElement('div');
		holder.dataset.settingDoor = '';
		const door = document.createElement('button');
		door.setAttribute('aria-haspopup', 'menu');
		door.setAttribute('aria-expanded', 'false');
		holder.append(door);
		one.append(holder);
		return { one, door };
	}

	it('opens the menu the row marks as its door', async () => {
		const body = pane();
		const { one, door } = rowWithDoor('tasks.duplicates.when');
		body.append(one);
		const pressed = vi.fn();
		door.addEventListener('keydown', (event) => pressed(event.key));

		const found = revealSetting('tasks.duplicates.when');
		await vi.advanceTimersByTimeAsync(50);

		await expect(found).resolves.toBe(true);
		expect(one.dataset.siftFound).toBe('');
		expect(pressed).toHaveBeenCalledWith('Enter');
	});

	it("keeps following the row after its own press, and stops at the person's", async () => {
		const body = pane();
		const { one } = rowWithDoor('tasks.backup.when');
		body.append(one);
		loading.now = true;

		void revealSetting('tasks.backup.when');
		await vi.advanceTimersByTimeAsync(100);
		(one.scrollIntoView as ReturnType<typeof vi.fn>).mockClear();
		// The pane goes on loading above the row, which moves: it is scrolled to again.
		const shifted = vi.spyOn(one, 'getBoundingClientRect').mockReturnValue({ top: 400 } as DOMRect);
		await vi.advanceTimersByTimeAsync(100);
		expect(one.scrollIntoView).toHaveBeenCalled();

		// The person presses a key: their own press ends the following.
		window.dispatchEvent(new KeyboardEvent('keydown', { key: 'a' }));
		(one.scrollIntoView as ReturnType<typeof vi.fn>).mockClear();
		shifted.mockReturnValue({ top: 800 } as DOMRect);
		await vi.advanceTimersByTimeAsync(200);
		expect(one.scrollIntoView).not.toHaveBeenCalled();
	});

	it('leaves a door alone that is disabled or already open', async () => {
		const body = pane();
		const { one, door } = rowWithDoor('tasks.music.when');
		door.disabled = true;
		body.append(one);
		const pressed = vi.fn();
		door.addEventListener('keydown', pressed);

		await revealSetting('tasks.music.when');
		await vi.advanceTimersByTimeAsync(50);

		expect(pressed).not.toHaveBeenCalled();
	});

	it('opens nothing on a row that marks no door', async () => {
		const body = pane();
		const plain = row('editing.delete.ask');
		const menu = document.createElement('button');
		menu.setAttribute('aria-haspopup', 'menu');
		plain.append(menu);
		body.append(plain);
		const pressed = vi.fn();
		menu.addEventListener('keydown', pressed);

		await revealSetting('editing.delete.ask');
		await vi.advanceTimersByTimeAsync(50);

		expect(pressed).not.toHaveBeenCalled();
	});
});

/* A search result or a pasted path naming something with no key (the heading over a group, a
   card) is looked for by its name where a pane writes names, and whatever hides it is opened. */
describe('a thing with a name and no key', () => {
	/** A group's heading as `SectionHeading` draws one: the words in an h2 inside its block. */
	function heading(words: string): { block: HTMLElement; title: HTMLElement } {
		const block = document.createElement('div');
		block.className = 'section-heading';
		block.scrollIntoView = vi.fn();
		const title = document.createElement('h2');
		title.textContent = words;
		block.append(title);
		return { block, title };
	}

	it('rings the block of the heading that says the name', async () => {
		const body = pane();
		const first = heading('Ratings');
		const wanted = heading('Auto-lock');
		body.append(first.block, wanted.block);

		const found = revealNamed('Auto-lock');
		await vi.advanceTimersByTimeAsync(50);

		await expect(found).resolves.toBe(true);
		expect(wanted.block.dataset.siftFound).toBe('');
		expect(wanted.block.scrollIntoView).toHaveBeenCalled();
		expect(first.block.dataset.siftFound).toBeUndefined();
	});

	it('finds a name written with other punctuation or case on the pane', async () => {
		const body = pane();
		const wanted = heading('Sign in');
		body.append(wanted.block);

		const found = revealNamed('Sign-in');
		await vi.advanceTimersByTimeAsync(50);

		await expect(found).resolves.toBe(true);
		expect(wanted.block.dataset.siftFound).toBe('');
	});

	it('opens the closed fold the name is inside', async () => {
		const body = pane();
		const fold = document.createElement('details');
		const wanted = heading('Saved files');
		fold.append(wanted.block);
		body.append(fold);

		const found = revealNamed('Saved files');
		await vi.advanceTimersByTimeAsync(50);

		await expect(found).resolves.toBe(true);
		expect(fold.open).toBe(true);
		expect(wanted.block.scrollIntoView).toHaveBeenCalled();
	});

	it('rings the whole row where the name is a row drawn by hand, a heading first', async () => {
		const body = pane();
		const line = document.createElement('div');
		line.className = 'row';
		line.scrollIntoView = vi.fn();
		const name = document.createElement('span');
		name.className = 'name';
		name.textContent = 'Hidden';
		line.append(name);
		const group = heading('Hidden');
		body.append(line, group.block);

		const found = revealNamed('Hidden');
		await vi.advanceTimersByTimeAsync(50);
		await expect(found).resolves.toBe(true);
		expect(group.block.dataset.siftFound).toBe('');
		expect(line.dataset.siftFound).toBeUndefined();

		group.block.remove();
		const again = revealNamed('Hidden');
		await vi.advanceTimersByTimeAsync(50);
		await expect(again).resolves.toBe(true);
		expect(line.dataset.siftFound).toBe('');
	});

	it('looks on the section being opened, never a pane still mounted beside it', async () => {
		const leaving = pane();
		leaving.dataset.section = 'appearance';
		const other = heading('Links');
		leaving.append(other.block);
		const arriving = pane();
		arriving.dataset.section = 'general';
		const wanted = heading('Links');
		arriving.append(wanted.block);

		const found = revealNamed('Links', 'general');
		await vi.advanceTimersByTimeAsync(50);

		await expect(found).resolves.toBe(true);
		expect(wanted.block.dataset.siftFound).toBe('');
		expect(other.block.dataset.siftFound).toBeUndefined();
	});

	it('opens the sub-page that claims the name, then rings it there', async () => {
		const body = pane();
		const wanted = heading('Stash-box search');
		const page = document.createElement('div');
		page.className = 'sub-page';
		page.append(wanted.block);
		const claimed = filedUnder(
			[{ name: 'Stash-box search', page: 'More settings' }, { name: 'Elsewhere' }],
			'More settings'
		);
		expect(claimed).toEqual([byName('Stash-box search')]);
		const release = drilldown.own(claimed, () => {
			if (!page.isConnected) body.after(page);
		});

		const found = revealNamed('Stash-box search');
		await vi.advanceTimersByTimeAsync(50);

		await expect(found).resolves.toBe(true);
		expect(wanted.block.dataset.siftFound).toBe('');
		release();
	});

	it('waits while the pane loads, and ends in silence where the name never comes', async () => {
		const body = pane();
		loading.now = true;
		const found = revealNamed('Learning paths');
		await vi.advanceTimersByTimeAsync(10_000);
		const late = heading('Learning paths');
		body.append(late.block);
		await vi.advanceTimersByTimeAsync(50);
		await expect(found).resolves.toBe(true);
		expect(late.block.dataset.siftFound).toBe('');

		loading.now = false;
		const never = revealNamed('No such heading');
		await vi.advanceTimersByTimeAsync(5_000);
		await expect(never).resolves.toBe(false);
		expect(shown).not.toHaveBeenCalled();
	});
});
