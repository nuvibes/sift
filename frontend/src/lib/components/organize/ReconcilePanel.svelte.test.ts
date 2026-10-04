/* Where a stash-box disagrees, one field at a time.
 *
 * Two values and two buttons. What the tests hold is the pair of things a reader could not check
 * for themselves: that keeping YOURS is sent as a decision rather than quietly dropped (the list
 * is worked out on every read, so a row that was not settled comes straight back) and that a
 * field is named from the record registry rather than by its column name, which is what stops the
 * screen reading `birth_date` at somebody.
 */

import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { flushSync, mount, tick, unmount } from 'svelte';

import type { Disagreement } from '$lib/entity/reconcile.svelte';

const mocks = vi.hoisted(() => ({
	disagreementsOf: vi.fn(),
	settle: vi.fn(),
	labels: [] as { subject: string; key: string; label: string }[]
}));

vi.mock('$lib/entity/reconcile.svelte', async (importOriginal) => ({
	...(await importOriginal<typeof import('$lib/entity/reconcile.svelte')>()),
	disagreementsOf: mocks.disagreementsOf,
	settle: mocks.settle
}));

/* The registry, stood in for by a real one whose single request is replaced. See the record
   components' tests for why an object shaped like one drifts. */
vi.mock('$lib/entity/records.svelte', async (importOriginal) => {
	const real = await importOriginal<typeof import('$lib/entity/records.svelte')>();
	class Standing extends real.Fields {
		override of(subject: string): never[] {
			return mocks.labels.filter((one) => one.subject === subject) as never[];
		}
	}
	return { ...real, fields: new Standing() };
});

import { answered } from '$lib/organize/organize.svelte';
import { toasts } from '$lib/shell/toasts.svelte';
import ReconcilePanel from './ReconcilePanel.svelte';

function row(over: Partial<Disagreement> = {}): Disagreement {
	return {
		subject: 'person',
		local_id: 'p-1',
		name: 'Jane',
		box_id: 'box-1',
		box_name: 'StashDB',
		key: 'birth_date',
		mine: '1990-01-01',
		theirs: '1991-02-02',
		mine_said: null,
		theirs_said: null,
		...over
	};
}

let host: HTMLElement;
let drawn: Record<string, unknown> | null = null;

beforeEach(() => {
	vi.clearAllMocks();
	mocks.labels = [];
	mocks.disagreementsOf.mockResolvedValue([]);
	mocks.settle.mockResolvedValue({ files: 0, fields: 0, created: 0, decision_id: 'd-1' });
	host = document.createElement('div');
	document.body.append(host);
});

afterEach(() => {
	if (drawn) unmount(drawn);
	drawn = null;
	host?.remove();
});

async function draw(onchange?: (waiting: number) => void, onwritten?: () => void): Promise<void> {
	drawn = mount(ReconcilePanel, {
		target: host,
		/*
		 * It draws one record's disagreements, so it must be told which: the record is the only
		 * thing that answers its questions.
		 */
		props: { subject: 'person', localId: 'p-1', onchange, onwritten }
	}) as Record<string, unknown>;
	flushSync();
	await tick();
	await tick();
	flushSync();
}

/** Press a button on the row whose field reads `field`. */
function pressIn(field: string, label: string): void {
	const line = [...host.querySelectorAll('tbody tr')].find(
		(one) => one.querySelector('th')?.textContent?.trim() === field
	);
	const button = [...(line?.querySelectorAll('button') ?? [])].find(
		(one) => one.textContent?.trim() === label
	);
	if (!button) throw new Error(`no button reading "${label}" on "${field}"`);
	button.click();
	flushSync();
}

function press(label: string): void {
	const button = [...host.querySelectorAll('button')].find(
		(one) => one.textContent?.trim() === label
	);
	if (!button) throw new Error(`no button reading "${label}"`);
	button.click();
	flushSync();
}

it('draws both answers on the row, so the question can be read without opening anything', async () => {
	mocks.disagreementsOf.mockResolvedValue([row()]);

	await draw();

	expect(host.textContent).toContain('1990-01-01');
	expect(host.textContent).toContain('1991-02-02');
	expect(host.textContent).toContain('StashDB');
});

it('puts the whole question on ONE row, so it reads at the height of a history row', async () => {
	/*
	 * The shape, held rather than the pixels: one element per disagreement carrying all six things,
	 * rather than a stack of boxes. The height follows from that and from the row's own inset, and
	 * the pixels are measured in a real engine.
	 */
	mocks.labels = [{ subject: 'person', key: 'birth_date', label: 'Birthdate' }];
	mocks.disagreementsOf.mockResolvedValue([row()]);

	await draw();

	const rows = [...host.querySelectorAll('tbody tr')];
	expect(rows).toHaveLength(1);
	const only = rows[0];
	expect(only.textContent).toContain('Birthdate');
	expect(only.textContent).toContain('1990-01-01');
	expect(only.textContent).toContain('1991-02-02');
	expect(only.querySelectorAll('button')).toHaveLength(2);
	// Cells, not boxes. A list or a nested table inside the row would be a card under another name,
	// and the height would go with it.
	expect(only.querySelectorAll('ul, ol, table')).toHaveLength(0);
});

it('lines the same field up under the same heading on every row', async () => {
	/*
	 * Why this is a table: a flex line sizes every cell to its own content, so with several
	 * disagreements each fact would begin at a different x.
	 *
	 * jsdom lays nothing out, so the x is measured in a real engine. What a test can hold is what
	 * the alignment follows from: a declared column per fact, in one order, with every row filling
	 * the same five in the same order. A row that dropped or added a cell is the only way a table's
	 * columns go ragged, and that is what this catches.
	 */
	mocks.labels = [{ subject: 'person', key: 'birth_date', label: 'Birthdate' }];
	mocks.disagreementsOf.mockResolvedValue([
		row(),
		row({ key: 'height_cm', mine: 170, theirs: 172 }),
		row({ box_id: 'box-2', box_name: 'Another box', key: 'eyes', mine: 'brown', theirs: 'blue' })
	]);

	await draw();

	const headings = [...host.querySelectorAll('thead th')].map((cell) => cell.textContent?.trim());
	// The last column is the two answers, and it carries no word: both buttons say what they do.
	expect(headings).toEqual(['Field', 'Stash-box', 'Yours', 'Theirs', '']);

	const rows = [...host.querySelectorAll('tbody tr')];
	expect(rows).toHaveLength(3);
	for (const one of rows) expect(one.querySelectorAll('th, td')).toHaveLength(headings.length);

	// And the words are said once, at the top, rather than beside every value.
	expect(host.querySelectorAll('tbody')[0].textContent).not.toContain('Yours');
});

it('calls a field what the record calls it, not what the column is called', async () => {
	// The registry is where every other record surface reads its labels, and a screen that spelled
	// them itself would be a second vocabulary the day a field is renamed.
	mocks.labels = [{ subject: 'person', key: 'birth_date', label: 'Birthdate' }];
	mocks.disagreementsOf.mockResolvedValue([row()]);

	await draw();

	expect(host.textContent).toContain('Birthdate');
});

it('falls back to the key when this server has never described that field', async () => {
	// An older server, or a field added since. A row with no name at all is worse than a row with a
	// technical one.
	mocks.disagreementsOf.mockResolvedValue([row({ key: 'invented_later' })]);

	await draw();

	expect(host.textContent).toContain('invented_later');
});

it('says a value nobody filled in, rather than leaving half the comparison blank', async () => {
	// A blank half of a two-column comparison reads as a missing row, not as an empty field.
	mocks.disagreementsOf.mockResolvedValue([row({ mine: null })]);

	await draw();

	expect(host.textContent).toContain('nothing');
});

it("draws a box's constant in the server's words, the ones its History line uses", async () => {
	// The line recording this answer says "Fake" and "Natural", and so does the table.
	mocks.disagreementsOf.mockResolvedValue([
		row({
			key: 'breast_type',
			mine: 'FAKE',
			theirs: 'NATURAL',
			mine_said: 'Fake',
			theirs_said: 'Natural'
		})
	]);

	await draw();

	const cells = [...host.querySelectorAll('td.value')].map((one) => one.textContent?.trim());
	expect(cells).toEqual(['Fake', 'Natural']);
});

it('reads a list as its entries rather than as a shape', async () => {
	mocks.disagreementsOf.mockResolvedValue([row({ theirs: ['Jane', 'JD'] })]);

	await draw();

	expect(host.textContent).toContain('Jane, JD');
});

it('sends the decision even when the answer is to keep what is already there', async () => {
	// The one that looks skippable. Nothing is written either way, but the row only leaves the
	// pile because the server was told, and the pile is worked out on every read.
	mocks.disagreementsOf.mockResolvedValue([row()]);
	await draw();

	press('Keep yours');

	expect(mocks.settle).toHaveBeenCalledWith(expect.objectContaining({ key: 'birth_date' }), false);
});

it('sends the other answer when that is the one pressed', async () => {
	mocks.disagreementsOf.mockResolvedValue([row()]);
	await draw();

	press('Take theirs');

	expect(mocks.settle).toHaveBeenCalledWith(expect.objectContaining({ key: 'birth_date' }), true);
});

it('reads the pile again once one has been settled', async () => {
	// Settling one field can change another: two boxes can disagree about the same value, and a list
	// left as it was would offer a question that has just been answered.
	mocks.disagreementsOf.mockResolvedValue([row()]);
	await draw();
	mocks.disagreementsOf.mockClear();

	press('Take theirs');
	await tick();
	await tick();

	expect(mocks.disagreementsOf).toHaveBeenCalled();
});

it('draws nothing at all when this record has no disagreement', async () => {
	/*
	 * No "Nothing disagrees." line: this sits at the top of the History tab of every person, site
	 * and tag, and most records are never linked to a box, so that sentence would be a line about a
	 * feature on almost every page. The block draws its heading only when there is something, so
	 * silence here makes the whole thing disappear.
	 */
	await draw();

	expect(host.textContent?.trim()).toBe('');
});

it('says so when the pile could not be read', async () => {
	mocks.disagreementsOf.mockRejectedValue(new Error('offline'));

	await draw();

	expect(host.textContent).toContain("That didn't work.");
});

it('keeps the other rows on screen while it reloads after a decision', async () => {
	/*
	 * Answering a question re-reads the list, and the panel must not draw "Looking..." over
	 * everything while it does, or the screen blinks for every answer. `EntityGrid` draws its
	 * skeleton only when there is nothing on screen yet, and these panels do the same. The row
	 * pressed leaves at once (see below); the one beside it stays.
	 */
	mocks.labels = [
		{ subject: 'person', key: 'birth_date', label: 'Born' },
		{ subject: 'person', key: 'height', label: 'Height' }
	];
	mocks.disagreementsOf.mockResolvedValue([row(), row({ key: 'height', mine: 160, theirs: 165 })]);
	await draw();
	expect(host.textContent).toContain('Born');

	// The reload after the decision never finishes, so what is drawn is what a person sees while
	// they wait.
	mocks.disagreementsOf.mockReturnValue(new Promise(() => {}));
	pressIn('Born', 'Keep yours');
	await tick();
	flushSync();

	expect(host.textContent).not.toContain('Born');
	expect(host.textContent).toContain('Height');
	expect(host.querySelector('.bone')).toBeNull();
});

it('takes the row away THE MOMENT it is pressed, before the server has answered', async () => {
	/*
	 * The row must leave as soon as a choice is pressed, not after the server and a re-read with
	 * every button disabled. The server here never answers at all.
	 */
	mocks.disagreementsOf.mockResolvedValue([row()]);
	const told: number[] = [];
	await draw((waiting) => told.push(waiting));
	mocks.settle.mockReturnValue(new Promise(() => {}));

	press('Take theirs');

	expect(host.querySelector('tbody tr')).toBeNull();
	expect(told.at(-1)).toBe(0);
});

it('lets the next row be pressed while the first is still on its way', async () => {
	mocks.disagreementsOf.mockResolvedValue([row(), row({ key: 'height', mine: 160, theirs: 165 })]);
	await draw();
	mocks.settle.mockReturnValue(new Promise(() => {}));

	press('Keep yours');
	press('Keep yours');

	expect(mocks.settle).toHaveBeenCalledTimes(2);
	expect(host.querySelector('tbody tr')).toBeNull();
});

it('puts the row back where it stood when the server refuses it, and says so', async () => {
	mocks.labels = [
		{ subject: 'person', key: 'birth_date', label: 'Born' },
		{ subject: 'person', key: 'height', label: 'Height' }
	];
	mocks.disagreementsOf.mockResolvedValue([row(), row({ key: 'height', mine: 160, theirs: 165 })]);
	await draw();
	let refuse: (error: Error) => void = () => {};
	mocks.settle.mockReturnValue(new Promise((_, no) => (refuse = no)));

	pressIn('Born', 'Take theirs');
	expect(host.textContent).not.toContain('Born');
	refuse(new Error('offline'));
	await tick();
	await tick();
	flushSync();

	const rows = [...host.querySelectorAll('tbody tr th')].map((one) => one.textContent?.trim());
	expect(rows).toEqual(['Born', 'Height']);
	expect(host.textContent).toContain("That didn't work.");
});

it('does not bring a pressed row back from a read that answers before its write lands', async () => {
	// Height is pressed and its write never lands; Born is pressed and lands at once, which reads
	// the rows again, and the server, not having Height's answer yet, still lists it.
	mocks.disagreementsOf.mockResolvedValue([row(), row({ key: 'height', mine: 160, theirs: 165 })]);
	await draw();
	mocks.settle.mockReturnValueOnce(new Promise(() => {}));
	mocks.disagreementsOf.mockResolvedValue([row({ key: 'height', mine: 160, theirs: 165 })]);

	pressIn('height', 'Keep yours');
	pressIn('birth_date', 'Keep yours');
	await tick();
	await tick();
	await tick();
	flushSync();

	expect(mocks.disagreementsOf).toHaveBeenCalledTimes(2);
	expect(host.querySelector('tbody tr')).toBeNull();
});

it('signs for the answer with the Undo toast and the decided signal, once the write has landed', async () => {
	mocks.disagreementsOf.mockResolvedValue([row()]);
	await draw();
	const before = answered.stamp;
	let land: (value: unknown) => void = () => {};
	mocks.settle.mockReturnValue(new Promise((yes) => (land = yes)));

	press('Keep yours');
	// Not before: a History thread re-read now would read the history without this answer in it.
	expect(answered.stamp).toBe(before);

	land({ files: 0, fields: 0, created: 0, decision_id: 'd-1' });
	await tick();
	await tick();

	expect(answered.stamp).toBeGreaterThan(before);
	const toast = toasts.items.at(-1);
	expect(toast?.message).toBe(
		"Kept your birth_date for Jane: 1990-01-01, not StashDB's 1991-02-02"
	);
	expect(toast?.action?.label).toBe('Undo');
});

it("says what the receipt says, from the server's own sentence, set-aside boxes and all", async () => {
	mocks.disagreementsOf.mockResolvedValue([row()]);
	await draw();
	const receipt =
		"Took StashDB's birth_date for Jane: 1991-02-02, where you had 1990-01-01; FansDB's 1992-03-03 set aside";
	mocks.settle.mockResolvedValue({
		files: 0,
		fields: 1,
		created: 0,
		decision_id: 'd-3',
		said: receipt
	});

	press('Take theirs');
	await tick();
	await tick();

	expect(toasts.items.at(-1)?.message).toBe(receipt);
});

it('tells the page to read the record again when a press wrote a field onto it', async () => {
	mocks.disagreementsOf.mockResolvedValue([row()]);
	const onwritten = vi.fn();
	await draw(undefined, onwritten);
	mocks.settle.mockResolvedValue({ files: 0, fields: 1, created: 0, decision_id: 'd-2' });

	press('Take theirs');
	await tick();
	await tick();

	expect(onwritten).toHaveBeenCalledTimes(1);
});

it('does not, when keeping yours wrote nothing', async () => {
	mocks.disagreementsOf.mockResolvedValue([row()]);
	const onwritten = vi.fn();
	await draw(undefined, onwritten);

	press('Keep yours');
	await tick();
	await tick();

	expect(onwritten).not.toHaveBeenCalled();
});

it('draws nothing at all while it waits, because it draws nothing when there is nothing', async () => {
	/*
	 * No skeleton lines: a skeleton promises space is about to be filled, and this block is absent
	 * by default (most records are linked to no box), so the promise would almost always be false.
	 */
	let answer: (rows: Disagreement[]) => void = () => {};
	mocks.disagreementsOf.mockReturnValue(
		new Promise<Disagreement[]>((resolve) => {
			answer = resolve;
		})
	);

	await draw();

	expect(host.querySelector('.skeleton')).toBeNull();
	expect(host.textContent?.trim()).toBe('');

	answer([row()]);
	await tick();
	await tick();
	flushSync();

	expect(host.textContent).toContain('1990-01-01');
});

it('asks about THIS record and nothing else', async () => {
	/*
	 * The server answers the narrow question, per record, rather than this reading the whole
	 * library's list and filtering it, which would cost tens of seconds per page view to draw a few rows.
	 * It builds both lists from the one rule.
	 */
	mocks.disagreementsOf.mockResolvedValue([]);

	await draw();

	expect(mocks.disagreementsOf).toHaveBeenCalledWith('person', 'p-1');
});

it('but a real failure is still reported', async () => {
	mocks.disagreementsOf.mockRejectedValue(new Error('the database is gone'));

	await draw();

	// `problemFrom` says the one-liner for anything that is not the server's own sentence, which is
	// what a reader sees; what matters here is that the block is NOT silent about it.
	expect(host.textContent).toContain("That didn't work.");
});

/*
 * How many are waiting is reported in every case. Two things count what is in this panel, the line
 * above it and the mark beside the History tab, so a read that failed must not leave the rows empty
 * and the number untouched ("One field a stash-box disagrees with" over nothing).
 */
async function drawTelling(): Promise<ReturnType<typeof vi.fn>> {
	const told = vi.fn();
	drawn = mount(ReconcilePanel, {
		target: host,
		props: { subject: 'person', localId: 'p-1', onchange: told }
	}) as Record<string, unknown>;
	flushSync();
	await tick();
	await tick();
	flushSync();
	return told;
}

it('says how many are waiting when the read answers', async () => {
	mocks.disagreementsOf.mockResolvedValue([row()]);

	expect(await drawTelling()).toHaveBeenCalledWith(1, ['StashDB']);
});

it('says none are waiting when the read is refused, for the same reason', async () => {
	mocks.disagreementsOf.mockRejectedValue(new Error('the database is gone'));

	expect(await drawTelling()).toHaveBeenCalledWith(0, []);
});
