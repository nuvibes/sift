/*
 * The panel that says who can see something, and the three words it has to get right.
 *
 * What is checked is the vocabulary and the moment of commitment. Not shared, Shared and Restricted
 * are three different promises, and the difference between the first and the third is why the
 * permission model has three states: "I never shared this" and "I said never" behave differently.
 *
 * The other half is that nothing is written until Apply, so a misread row is never already a fact
 * and the panel can sensibly be opened on forty files. Several tests below exist only to say that a
 * press moves a word and writes nothing.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { flushSync, mount, tick } from 'svelte';
import ShareDialog from './ShareDialog.svelte';
import type { Grant, ShareableUser, ShareTarget } from '$lib/library/sharing';

const mocks = vi.hoisted(() => ({
	fetchUsers: vi.fn(),
	fetchGrants: vi.fn(),
	fetchSources: vi.fn(),
	fetchVaultSources: vi.fn(),
	unhide: vi.fn(),
	put: vi.fn()
}));

vi.mock('$lib/library/sharing', async () => {
	const actual =
		await vi.importActual<typeof import('$lib/library/sharing')>('$lib/library/sharing');
	return {
		...actual,
		fetchUsers: mocks.fetchUsers,
		fetchGrants: mocks.fetchGrants,
		fetchSources: mocks.fetchSources,
		fetchVaultSources: mocks.fetchVaultSources,
		unhide: mocks.unhide,
		put: mocks.put
	};
});
vi.mock('$lib/shell/toasts.svelte', () => ({ toasts: { show: vi.fn() } }));

/* The tab strip's own counts route, which is where the panel reads how many labels a site carries.
   Mocked rather than left to fail quietly: unmocked it answers `{}` through its own catch, which is
   indistinguishable from a panel that never asked. */
const counts = vi.hoisted(() => ({ loadCounts: vi.fn() }));
vi.mock('$lib/entity/related.svelte', async () => {
	const actual = await vi.importActual<typeof import('$lib/entity/related.svelte')>(
		'$lib/entity/related.svelte'
	);
	return { ...actual, loadCounts: counts.loadCounts };
});

const ADMIN: ShareableUser = { id: 'u1', username: 'kate', role: 'admin' };
const GUEST: ShareableUser = { id: 'u2', username: 'sam', role: 'guest' };

const TARGET: ShareTarget = { type: 'item', id: 'a1', label: 'holiday.mp4' };
const OTHER: ShareTarget = { type: 'item', id: 'a2', label: 'birthday.mp4' };

let host: HTMLElement;

/**
 * Open the panel on one or more things.
 *
 * `grants` is per target, in the same order, because that is the whole of what makes a row read
 * Mixed rather than agreeing with itself.
 */
async function open(users: ShareableUser[], grants: Grant[][], targets: ShareTarget[] = [TARGET]) {
	mocks.fetchUsers.mockResolvedValue(users);
	mocks.fetchGrants.mockImplementation(async (target: ShareTarget) => {
		const index = targets.findIndex((each) => each.id === target.id);
		return grants[index] ?? [];
	});
	host = document.createElement('div');
	document.body.append(host);
	mount(ShareDialog, { target: host, props: { open: true, targets } });
	flushSync();
	await tick();
	await tick();
	flushSync();
}

/* Portalled to the end of the document, so the panel is not inside the host it was mounted into. */
function panel(): HTMLElement | null {
	return document.querySelector('.share-sheet');
}

function text(): string {
	return panel()?.textContent ?? '';
}

/* The word beside one user's name, which is the answer the panel is actually giving.
 *
 * Read from the row rather than from the panel's whole text, and that is not fussiness: the
 * explanation at the foot of the panel contains all three words on purpose, so asserting against
 * the whole thing passes whatever the rows say, even against a broken resolver.
 */
function standingShown(): string[] {
	return [...(panel()?.querySelectorAll('.standing') ?? [])].map((each) =>
		(each.textContent ?? '').replace(/\s+/g, ' ').trim()
	);
}

/* The icon inside a button renders its glyph as text, so the button's own text content is the
 * ligature name followed by the label, which is why this looks for the label rather than at the
 * start of the string. */
function button(label: string): HTMLButtonElement | undefined {
	return [...(panel()?.querySelectorAll('button') ?? [])].find((each) =>
		each.textContent?.includes(label)
	);
}

async function press(label: string) {
	button(label)?.click();
	await tick();
	flushSync();
}

/* The two answer buttons of the one guest row, Share first. */
function choices(): HTMLButtonElement[] {
	return [...(panel()?.querySelectorAll<HTMLButtonElement>('.choices button') ?? [])];
}

/** Which of the two is lit, which is what is set HERE; 'none' when neither is. */
function chosen(): string {
	const lit = choices().find((one) => one.getAttribute('aria-pressed') === 'true');
	if (!lit) return 'none';
	return lit.classList.contains('restrict') ? 'Restricted' : 'Shared';
}

/* One of the three answers, the way a person gives it: press Share, press Restrict, or press the
   lit one again to take what is set here off. */
async function choose(answer: 'Shared' | 'Restricted' | 'none') {
	const [share, restrict] = choices();
	expect(share, 'there are no buttons to press').toBeTruthy();
	const target =
		answer === 'Shared'
			? share
			: answer === 'Restricted'
				? restrict
				: choices().find((one) => one.getAttribute('aria-pressed') === 'true');
	expect(target, `nothing to press for ${answer}`).toBeTruthy();
	target?.click();
	await tick();
	flushSync();
}

beforeEach(() => {
	mocks.fetchUsers.mockReset();
	mocks.fetchGrants.mockReset();
	mocks.fetchSources.mockReset();
	mocks.fetchSources.mockResolvedValue([]);
	mocks.fetchVaultSources.mockReset();
	mocks.fetchVaultSources.mockResolvedValue([]);
	mocks.unhide.mockReset();
	mocks.unhide.mockResolvedValue(undefined);
	mocks.put.mockReset();
	mocks.put.mockResolvedValue(undefined);
});

afterEach(() => {
	host?.remove();
	/* And everything portalled out of it. The panel renders at the end of the document rather than
	 * where it was written, so removing the host leaves it behind, and the next test then reads
	 * the last one's rows as though they were its own. */
	document.body.innerHTML = '';
});

describe('the three words', () => {
	it('says Not shared when nothing has been shared', async () => {
		/* The default for everything in a library. It has to read as a state somebody is looking at
		 * rather than as an empty screen, or the panel looks like it failed to load. */
		await open([ADMIN, GUEST], [[]]);

		expect(text()).toContain('sam');
		expect(standingShown()).toEqual(['Not shared']);
	});

	it('says Shared once a share names them', async () => {
		await open(
			[ADMIN, GUEST],
			[[{ subject_user_id: 'u2', username: 'sam', effect: 'share', created_at: 0 }]]
		);

		expect(standingShown()).toEqual(['Shared']);
	});

	it('says Restricted when they hold both, because that is what the server resolves to', async () => {
		/* The mirroring that matters. Both rows can exist on a library that predates the panel
		 * refusing to write them; restrict wins, always. Drawing "Shared" here would tell an admin
		 * something is reachable that the server refuses: the one wrong answer on this panel that
		 * would be believed and acted on. */
		await open(
			[ADMIN, GUEST],
			[
				[
					{ subject_user_id: 'u2', username: 'sam', effect: 'share', created_at: 0 },
					{ subject_user_id: 'u2', username: 'sam', effect: 'restrict', created_at: 1 }
				]
			]
		);

		expect(standingShown()).toEqual(['Restricted']);
	});

	it('explains under the rows that Restricted is a promise, and how to open one file', async () => {
		// The one thing about this model somebody has to be told rather than shown.
		await open([ADMIN, GUEST], [[]]);

		const notes = [...(panel()?.querySelectorAll('.note') ?? [])].map((one) => one.textContent);
		expect(notes[0]).toContain('never sees this');
		expect(notes[1]).toContain('take the restriction off the folder first');
		expect(panel()?.querySelector('.rows-box ~ .note')).not.toBeNull();
	});

	/* Both answers on screen at the same time, as a pair of buttons, and no menu to open. */
	it('offers the answers as a pair of buttons, Share and Restrict, never a menu', async () => {
		await open([ADMIN, GUEST], [[]]);

		expect(chosen()).toBe('none');
		expect(choices().map((one) => one.textContent?.replace(/[^A-Za-z]/g, ''))).toEqual([
			'Share',
			'Restrict'
		]);
		expect(panel()?.querySelector('.ui-select')).toBeNull();
	});

	it('says Mixed when the things it is open on do not agree', async () => {
		/* The fourth answer, and it only exists because the panel opens on a selection. Picking one
		 * of the two would say something about the other file that is not true. */
		await open(
			[ADMIN, GUEST],
			[[{ subject_user_id: 'u2', username: 'sam', effect: 'share', created_at: 0 }], []],
			[TARGET, OTHER]
		);

		expect(standingShown()).toEqual(['Mixed']);
		expect(chosen(), 'a disagreeing selection lights neither').toBe('none');
	});
});

describe('where the answer came from', () => {
	it('names the thing each grant was made on', async () => {
		/* The question somebody actually has when they see a mark they did not make. A file inside
		 * three folders, carrying four tags, has eight places a decision could have come from. */
		mocks.fetchSources.mockResolvedValue([
			{
				subject_user_id: 'u2',
				username: 'sam',
				effect: 'share',
				source_type: 'folder',
				source_id: 'f1',
				source_name: 'Holiday',
				here: false,
				decides: false
			},
			{
				subject_user_id: 'u2',
				username: 'sam',
				effect: 'restrict',
				source_type: 'tag',
				source_id: 't1',
				source_name: 'private',
				here: false,
				decides: true
			}
		]);
		await open([ADMIN, GUEST], [[]]);

		expect(text().replace(/\s+/g, ' ')).toContain('Shared by the folder Holiday');
		expect(text().replace(/\s+/g, ' ')).toContain('Restricted by the tag private');
	});

	it('says what the user can actually do, not only what was written here', async () => {
		/* The panel must not contradict itself in two lines: a file reachable through its folder
		 * reading "Not shared" over "Shared by the folder Holiday". The word beside the
		 * name is the resolved answer; the buttons stay about what is written HERE, because pressing
		 * one writes or removes a grant on this file. */
		mocks.fetchSources.mockResolvedValue([
			{
				subject_user_id: 'u2',
				username: 'sam',
				effect: 'share',
				source_type: 'folder',
				source_id: 'f1',
				source_name: 'Holiday',
				here: false,
				decides: true
			}
		]);
		await open([ADMIN, GUEST], [[]]);

		expect(standingShown()).toEqual(['Shared']);
		// Nothing is recorded on the file, so neither button is lit: the Share button wears the
		// reached tint, so the row does not look untouched.
		expect(chosen()).toBe('none');
		expect(choices()[0].classList.contains('reached')).toBe(true);
		expect(choices()[1].classList.contains('reached')).toBe(false);
	});

	it('previews what is left when the local decision is taken off', async () => {
		/*
		 * A file shared both on itself and by its folder. Pressing the lit Shared must not say "Not
		 * shared" and then, once applied, come back saying Shared: that preview describes a state
		 * that never exists. Taking the local grant off leaves whatever reaches the file from
		 * everything it is in, and that is what the row has to say it is about to become.
		 */
		mocks.fetchSources.mockResolvedValue([
			{
				subject_user_id: 'u2',
				username: 'sam',
				effect: 'share',
				source_type: 'item',
				source_id: 'a1',
				source_name: null,
				here: true,
				decides: true
			},
			{
				subject_user_id: 'u2',
				username: 'sam',
				effect: 'share',
				source_type: 'folder',
				source_id: 'f1',
				source_name: 'Holiday',
				here: false,
				decides: false
			}
		]);
		await open(
			[ADMIN, GUEST],
			[[{ subject_user_id: 'u2', username: 'sam', effect: 'share', created_at: 0 }]]
		);
		expect(standingShown()).toEqual(['Shared']);

		// Take the grant on the file itself off.
		await choose('none');

		// Still shared (by the folder), and neither button is lit here.
		expect(standingShown()).toEqual(['Shared']);
		expect(chosen()).toBe('none');
		// ...and the line about the grant being removed has gone with it.
		expect(text()).not.toContain('on this file itself');
	});

	it('greys out the grant that lost, and keeps the one in force in its colour', async () => {
		/* A share a restrict is beating is worth showing (it is what happens the day the restrict
		 * comes off) and it must not read as though it is in force. A green "Shared" on a line
		 * that is doing nothing is the one thing this panel must not draw. */
		mocks.fetchSources.mockResolvedValue([
			{
				subject_user_id: 'u2',
				username: 'sam',
				effect: 'share',
				source_type: 'folder',
				source_id: 'f1',
				source_name: 'Holiday',
				here: false,
				decides: false
			},
			{
				subject_user_id: 'u2',
				username: 'sam',
				effect: 'restrict',
				source_type: 'item',
				source_id: 'a1',
				source_name: null,
				here: true,
				decides: true
			}
		]);
		await open([ADMIN, GUEST], [[]]);

		const beaten = panel()?.querySelectorAll('.reason.beaten') ?? [];
		expect(beaten).toHaveLength(1);
		expect(beaten[0].textContent).toContain('Shared');
		// ...and the one in force is not greyed, or the fade would be saying nothing.
		expect(panel()?.querySelectorAll('.reason:not(.beaten)')).toHaveLength(1);
	});

	it('names the thing itself rather than repeating its name back', async () => {
		mocks.fetchSources.mockResolvedValue([
			{
				subject_user_id: 'u2',
				username: 'sam',
				effect: 'share',
				source_type: 'item',
				source_id: 'a1',
				source_name: null,
				here: true,
				decides: true
			}
		]);
		await open([ADMIN, GUEST], [[]]);

		expect(text().replace(/\s+/g, ' ')).toContain('Shared on this file itself');
	});

	it('asks nothing when it is open on several things', async () => {
		/* The honest answer across a selection is a different list per file, and there is nowhere
		 * sensible to draw that. */
		await open([ADMIN, GUEST], [[], []], [TARGET, OTHER]);

		expect(mocks.fetchSources).not.toHaveBeenCalled();
	});
});

describe('what it says it is acting on', () => {
	it('names one thing', async () => {
		await open([ADMIN, GUEST], [[]]);
		expect(text()).toContain('holiday.mp4');
	});

	it('counts several rather than listing them', async () => {
		await open([ADMIN, GUEST], [[], []], [TARGET, OTHER]);
		expect(text()).toContain('2 files');
	});
});

describe('who it offers', () => {
	it('lists guests and not admins', async () => {
		/* An admin is past every access rule already, so a grant made to one does nothing at all and
		 * the server refuses to store it. A row for them would be a control that cannot work. */
		await open([ADMIN, GUEST], [[]]);

		expect(text()).toContain('sam');
		expect(text()).not.toContain('kate');
	});

	it('says where guests come from when there are none', async () => {
		/* Not an error. An install with no guests is the ordinary state of a Sift, and the person
		 * reading this needs the next step rather than a blank panel. */
		await open([ADMIN], [[]]);

		expect(text()).toContain('no guests yet');
		expect(text()).toContain('Users');
	});
});

describe('pressing the controls', () => {
	it('writes nothing until Apply', async () => {
		/* The point of staging. A press is a decision on screen; the panel is where
		 * it is reconsidered, and Cancel is what throws it away. */
		await open([ADMIN, GUEST], [[]]);

		await choose('Shared');

		expect(mocks.put).not.toHaveBeenCalled();
		expect(standingShown()).toEqual(['Shared']);
	});

	it('applies the chosen word, once, to the thing it is open on', async () => {
		await open([ADMIN, GUEST], [[]]);

		await choose('Shared');
		await press('Apply');

		expect(mocks.put).toHaveBeenCalledTimes(1);
		expect(mocks.put).toHaveBeenCalledWith(TARGET, 'u2', 'shared', []);
	});

	it('applies it to every one of them when it is open on several', async () => {
		/* A selection of six must share all six, not the one that happened to be clicked first
		 * while the action bar says six. */
		await open([ADMIN, GUEST], [[], []], [TARGET, OTHER]);

		await choose('Shared');
		await press('Apply');

		expect(mocks.put.mock.calls.map((call) => call[0])).toEqual([TARGET, OTHER]);
	});

	it('lands on Private when the lit button is pressed again', async () => {
		/* Without this a restrict could be added but never removed. */
		await open(
			[ADMIN, GUEST],
			[[{ subject_user_id: 'u2', username: 'sam', effect: 'restrict', created_at: 0 }]]
		);

		await choose('none');
		await press('Apply');

		expect(mocks.put).toHaveBeenCalledWith(TARGET, 'u2', 'private', [
			{ subject_user_id: 'u2', username: 'sam', effect: 'restrict', created_at: 0 }
		]);
	});

	it('moves straight across rather than needing the other one turned off first', async () => {
		/*
		 * Shared and Restricted are mutually exclusive. Holding both would resolve to Restricted,
		 * so the share underneath would do nothing, look like it did, and come quietly into force
		 * the day the restrict was lifted.
		 */
		await open(
			[ADMIN, GUEST],
			[[{ subject_user_id: 'u2', username: 'sam', effect: 'restrict', created_at: 0 }]]
		);

		await choose('Shared');
		await press('Apply');

		expect(mocks.put).toHaveBeenCalledTimes(1);
		expect(mocks.put.mock.calls[0][2]).toBe('shared');
	});

	it('writes nothing when the choice is put back where it started', async () => {
		/* Pressed twice is not a change, and Apply should not write a grant that is already there:
		 * every write announces itself to every screen holding a scoped list. */
		await open([ADMIN, GUEST], [[]]);

		await choose('Shared');
		await choose('none');
		await press('Apply');

		expect(mocks.put).not.toHaveBeenCalled();
	});

	it('counts what has been chosen and not yet saved', async () => {
		/*
		 * The row already reads the word somebody chose, so the count on the button is what says
		 * there is something outstanding. It is said once, not once per row.
		 */
		await open([ADMIN, GUEST], [[]]);
		await choose('Restricted');

		expect(button('Apply')?.textContent).toContain('(1)');
	});

	it('throws the staged decision away on Cancel', async () => {
		await open([ADMIN, GUEST], [[]]);

		await choose('Shared');
		await press('Cancel');

		expect(mocks.put).not.toHaveBeenCalled();
	});

	it('lights the button for what is set here', async () => {
		await open(
			[ADMIN, GUEST],
			[[{ subject_user_id: 'u2', username: 'sam', effect: 'share', created_at: 0 }]]
		);

		expect(chosen()).toBe('Shared');
	});
});

describe('what is hiding this', () => {
	/*
	 * The other rule. Hidden is not Restricted pointed harder: it withholds the thing from every
	 * user, this one included, and no share overrides it. The panel says so, so "Shared with sam"
	 * over a file nobody can see is not left unexplained.
	 */
	it('names the thing whose flag is doing it', async () => {
		mocks.fetchVaultSources.mockResolvedValue([
			{ source_type: 'folder', source_id: 'f1', source_name: 'Holiday', here: false }
		]);
		await open([ADMIN, GUEST], [[]]);

		expect(text()).toContain('Hidden');
		expect(text(), 'the panel says hidden without saying by what').toContain(
			'by the folder Holiday'
		);
	});

	it('draws the mark solid where the switch is on this very thing', async () => {
		// The rule the whole app follows: solid is "this is the thing doing the controlling", hollow
		// is "something above it is". A file hidden by itself is the one you can undo where you are.
		mocks.fetchVaultSources.mockResolvedValue([
			{ source_type: 'item', source_id: 'a1', source_name: null, here: true }
		]);
		await open([ADMIN, GUEST], [[]]);

		expect(panel()?.querySelector('.hidden-by .icon.filled')).not.toBeNull();
		expect(text()).toContain('on this file itself');
	});

	it('draws it hollow when the switch is somewhere above', async () => {
		mocks.fetchVaultSources.mockResolvedValue([
			{ source_type: 'person', source_id: 'p1', source_name: 'Reya Solberg', here: false }
		]);
		await open([ADMIN, GUEST], [[]]);

		expect(panel()?.querySelector('.hidden-by .icon')).not.toBeNull();
		expect(
			panel()?.querySelector('.hidden-by .icon.filled'),
			'a decision made above this file is drawn as though it were made on it'
		).toBeNull();
		expect(text()).toContain('by the person Reya Solberg');
	});

	it('says nothing at all about an ordinary file', async () => {
		// Which is nearly every file. A block that is always there is furniture, and furniture stops
		// being read, so the one time it matters it would not be noticed either.
		await open([ADMIN, GUEST], [[]]);

		expect(panel()?.querySelector('.hidden-by')).toBeNull();
		expect(text()).not.toContain('Hidden');
	});

	it('does not ask across a selection, where the answer differs per file', async () => {
		await open([ADMIN, GUEST], [[], []], [TARGET, OTHER]);

		expect(mocks.fetchVaultSources).not.toHaveBeenCalled();
	});
});

describe('acting on what is hiding this', () => {
	const FOLDER = {
		source_type: 'folder' as const,
		source_id: 'f1',
		source_name: 'Holiday',
		here: false
	};

	it('offers a way out on the line that names the thing', async () => {
		/* Each line is a different thing (unhiding the folder is not unhiding the file) so the
		 * button belongs to the line rather than to the block. */
		mocks.fetchVaultSources.mockResolvedValue([FOLDER]);
		await open([ADMIN, GUEST], [[]]);

		const out = panel()?.querySelector('.hidden-by button');
		expect(out, 'the panel says what is hiding this and offers no way to undo it').not.toBeNull();

		(out as HTMLButtonElement).click();
		await tick();

		expect(mocks.unhide).toHaveBeenCalledWith(expect.objectContaining({ source_id: 'f1' }));
	});

	it('takes the line away once the thing is out', async () => {
		mocks.fetchVaultSources.mockResolvedValue([FOLDER]);
		await open([ADMIN, GUEST], [[]]);

		(panel()?.querySelector('.hidden-by button') as HTMLButtonElement).click();
		await tick();
		await tick();
		flushSync();

		expect(panel()?.querySelector('.hidden-by')).toBeNull();
	});

	it('names the folder as a link nowhere, because a folder has no page', async () => {
		// The tree lives inside Settings, which is a panel rather than an address. Saying so here
		// stops somebody "fixing" it into a link that goes to the wrong place.
		mocks.fetchVaultSources.mockResolvedValue([FOLDER]);
		await open([ADMIN, GUEST], [[]]);

		expect(panel()?.querySelector('a.named')).toBeNull();
		expect(text()).toContain('Holiday');
	});

	it('links a person, so the line is a way to them', async () => {
		mocks.fetchVaultSources.mockResolvedValue([
			{ source_type: 'person', source_id: 'p1', source_name: 'Reya Solberg', here: false }
		]);
		await open([ADMIN, GUEST], [[]]);

		const link = panel()?.querySelector('a.named') as HTMLAnchorElement | null;
		expect(link?.getAttribute('href')).toBe('/people/p1');
		expect(link?.textContent).toContain('Reya Solberg');
	});
});

/*
 * A site can be part of another site, the one case where the heading names much less than the
 * decision covers: a network holds Sites within it, the files are filed under those, and the
 * network's own file count is nought. Sharing "Northlight Media" hands over everything its Sites
 * released, and the panel says so. The counts are the route's own shape for a Site: the Sites within
 * it under `sites_within`, and `sites` null (a Site's files come from no other Site).
 */
describe('a site that holds Sites within it', () => {
	it('says how many the decision reaches, in the words its card says', async () => {
		counts.loadCounts.mockResolvedValue({ files: 0, sites_within: 3 });
		await open([ADMIN, GUEST], [[]], [{ type: 'site', id: 's1', label: 'Northlight Media' }]);

		expect(text()).toContain('Includes the 3 Sites within it');
	});

	it('says nothing at all about an ordinary site', async () => {
		// The common case, and a line reading "Includes the 0 Sites within it" on every site is what
		// teaches people to stop reading the place the real warning appears.
		counts.loadCounts.mockResolvedValue({ files: 4, sites_within: 0 });
		await open([ADMIN, GUEST], [[]], [{ type: 'site', id: 's2', label: 'Solo Site' }]);

		expect(panel()?.querySelector('.reaches')).toBeNull();
	});
});
