/* The screen that makes a guest, and the promises its words make.
 *
 * The wording is asserted as hard as the behaviour here, and deliberately. This is the only place
 * a user is created in the whole application, and what it says decides what an admin believes
 * about the password they are about to choose for somebody else: whether it will be forced to
 * change, whether an email is going anywhere, what blocking somebody actually does to a browser
 * they left open. Every one of those is a sentence, and every one of them can be wrong.
 */

import { readFileSync } from 'node:fs';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { flushSync, mount, tick } from 'svelte';
import Users from './Users.svelte';

const mocks = vi.hoisted(() => ({
	get: vi.fn(),
	post: vi.fn(),
	put: vi.fn(),
	del: vi.fn(),
	session: { viewer: { id: 'u1', username: 'kate', role: 'admin' }, isAdmin: true }
}));

vi.mock('$lib/api/client', async () => {
	const actual = await vi.importActual<typeof import('$lib/api/client')>('$lib/api/client');
	return {
		...actual,
		api: { get: mocks.get, post: mocks.post, put: mocks.put, del: mocks.del }
	};
});
vi.mock('$lib/shell/session.svelte', () => ({ session: mocks.session }));
vi.mock('$lib/shell/toasts.svelte', () => ({ toasts: { show: vi.fn() } }));

const ADMIN = { id: 'u1', username: 'kate', role: 'admin', disabled: false, created_at: 0 };
const GUEST = { id: 'u2', username: 'sam', role: 'guest', disabled: false, created_at: 1 };
const BLOCKED = { ...GUEST, id: 'u3', username: 'alex', disabled: true };

let host: HTMLElement;

async function show(users: unknown[]) {
	mocks.get.mockResolvedValue(users);
	host = document.createElement('div');
	document.body.append(host);
	mount(Users, { target: host });
	flushSync();
	await tick();
	await tick();
	flushSync();
}

function text(): string {
	return host.textContent?.toLowerCase() ?? '';
}

function buttons(): HTMLButtonElement[] {
	return [...host.querySelectorAll('button')];
}

/* A guest's actions are behind one control, the same as a folder's, so reaching one means
 * opening it first. The menu is portalled to the end of the document, not inside the host. */
async function openMenu(): Promise<void> {
	const trigger = buttons().find((each) =>
		(each.getAttribute('aria-label') ?? '').startsWith('More for')
	);
	expect(trigger, 'no actions control on the row').toBeDefined();
	trigger?.click();
	await until(() => document.querySelectorAll('[role="menuitem"]').length > 0);
}

/** One of the actions, by the words on it. */
function action(words: string): HTMLElement | undefined {
	// The icon on a menu row is a LIGATURE, so its glyph is a private-use character sitting in front
	// of the words: part of the row's own text, which `trim()` does not touch. Matching on the label
	// alone would find nothing, which reads as the row having disappeared.
	return [...document.querySelectorAll<HTMLElement>('[role="menuitem"]')].find(
		(item) => item.textContent?.replace(/[\uE000-\uF8FF]/g, '').trim() === words
	);
}

/** Open the menu and press one of its actions. */
async function press(words: string): Promise<void> {
	await openMenu();
	const item = action(words);
	expect(item, `no "${words}" in the menu`).toBeDefined();
	item?.click();
	await tick();
}

/* The dialog is portalled to the end of the document, so it is not inside the mounted host. */
function cancel(): HTMLElement {
	const button = document.querySelector('.cancel');
	expect(button, 'the confirm dialog was not on screen').not.toBeNull();
	return button as HTMLElement;
}

/* The dialog leaves on a transition rather than in the same frame, so "it went" is a thing to wait
 * for. Polled with a deadline rather than a fixed number of ticks: a tick count is a guess about
 * how long an animation takes, and the guess is what makes a test like this flaky later. */
async function until(condition: () => boolean, within = 2000): Promise<void> {
	const deadline = Date.now() + within;
	while (!condition() && Date.now() < deadline) {
		await new Promise((resolve) => setTimeout(resolve, 10));
	}
	expect(condition()).toBe(true);
}

beforeEach(() => {
	mocks.get.mockReset();
	mocks.post.mockReset();
	mocks.put.mockReset();
	mocks.del.mockReset();
});

afterEach(() => {
	host?.remove();
	/* And everything portalled out of it. The confirm renders at the end of the document rather
	 * than where it was written, so removing the host leaves it behind, and the next test then
	 * reads a dialog the last one opened as though it were its own. */
	document.body.innerHTML = '';
});

describe('what the screen says about a new guest', () => {
	it('says the admin chooses the first password and the guest can change it', async () => {
		/* The decision this screen has to communicate honestly. Nothing forces a change, so nothing
		 * here may imply one: an admin who believes the password they said out loud expires on
		 * first use will keep handing out the same one. */
		await show([ADMIN]);

		expect(text()).toContain('first password');
		expect(text()).toContain('change it');
	});

	it('says plainly that no email is involved', async () => {
		// The absence people expect to be present. Somebody who assumes an invitation went out will
		// wait for the guest to receive one.
		await show([ADMIN]);

		expect(text()).toContain('no email');
	});

	it('says a guest sees nothing until something is shared', async () => {
		/* Default-deny is the single most surprising thing about this feature: adding a user
		 * gives that person access to nothing whatsoever. An admin who does not know that has added
		 * a guest and will believe the library is now readable by them. */
		await show([ADMIN]);

		expect(text()).toContain('nothing');
		expect(text()).toContain('share');
	});
});

describe('the list', () => {
	it('shows the guests and leaves the admin out of the manageable rows', async () => {
		/* An admin is named on the screen (they are signed in as one and the screen says so),
		 * but they are not a row with Block and Remove beside them. The server refuses either way;
		 * this is about not offering a button that cannot work. */
		await show([ADMIN, GUEST]);

		expect(text()).toContain('sam');
		// One actions control per manageable row, named for whose row it is. An admin has none.
		const triggers = [...host.querySelectorAll('[aria-label]')]
			.map((element) => element.getAttribute('aria-label') ?? '')
			.filter((label) => label.startsWith('More for'));
		expect(triggers).toEqual(['More for sam']);
	});

	it('says out loud that blocking ends the sessions they already have', async () => {
		/* The half an admin would not assume. "Blocked" reads as "cannot sign in next time", and a
		 * guest sitting in front of an open browser is exactly the case somebody blocks a user
		 * for. */
		await show([ADMIN, GUEST]);

		expect(text()).toContain('signs them out of every browser');
	});

	it('keeps a blocked user visible rather than hiding it', async () => {
		// Somebody wondering why a guest cannot get in needs to find the row, not lose it.
		await show([ADMIN, BLOCKED]);

		expect(text()).toContain('alex');
		expect(text()).toContain('sign-in turned off');
	});

	it('offers to allow a blocked user back in', async () => {
		await show([ADMIN, BLOCKED]);

		mocks.put.mockResolvedValue(BLOCKED);
		await press('Turn on sign-in');

		expect(mocks.put).toHaveBeenCalledWith('/auth/users/u3/disabled', {
			body: { disabled: false }
		});
	});

	it('says nobody can see anything when there are no guests', async () => {
		await show([ADMIN]);

		expect(text()).toContain('no guests yet');
	});
});

describe('the confirmation in front of removing somebody', () => {
	it('asks before removing, and removes nothing until it is answered', async () => {
		/* No bin and no undo: removing a user takes their sessions and every share ever made to
		 * them. The click that opens the question must not be the click that answers it. */
		await show([ADMIN, GUEST]);

		await press('Delete');

		expect(mocks.del).not.toHaveBeenCalled();
		expect(document.body.textContent).toContain('Delete sam?');
	});

	it('goes away when it is dismissed', async () => {
		await show([ADMIN, GUEST]);
		await press('Delete');

		cancel().click();

		await until(() => !document.body.textContent?.includes('Delete sam?'));
		expect(mocks.del).not.toHaveBeenCalled();
	});

	it('and can be asked again after saying no once', async () => {
		/* The bug this pins, and it is the quiet kind.
		 *
		 * Deriving the dialog's open state from "is somebody being removed" means the dialog owns
		 * that flag while it is up and this screen never hears about Cancel. The user is still
		 * the one being removed, so the expression handed down has not changed, so nothing is
		 * pushed back, and the Remove button for that row silently stops working for the rest of
		 * the session. Nothing looks broken; the button simply does nothing.
		 */
		await show([ADMIN, GUEST]);
		await press('Delete');
		cancel().click();
		await until(() => !document.body.textContent?.includes('Delete sam?'));

		await press('Delete');

		await until(() => Boolean(document.body.textContent?.includes('Delete sam?')));
	});
});

describe('when the users cannot be loaded', () => {
	it('says so rather than drawing an empty list that reads as no guests', async () => {
		/* An empty list and a failed load look identical, and one of them is a lie that would have an
		 * admin believe they had removed somebody. */
		mocks.get.mockRejectedValue(new Error('nope'));
		host = document.createElement('div');
		document.body.append(host);
		mount(Users, { target: host });
		flushSync();
		await tick();
		await tick();
		flushSync();

		expect(text()).toContain("couldn't be loaded");
		expect(text()).not.toContain('no guests yet');
	});
});

describe('the guest rows are the shared row', () => {
	it('opens its verbs from a right-click as well as from the three dots', () => {
		/*
		 * A list of rows carrying verbs must answer the right-click too. An `<li>` of its own with
		 * a bare `RowMenu` hung on it would make the three dots work and the gesture every other
		 * row in the app answers do nothing, and nothing could say so, because a hand-written row
		 * is invisible to a gate that counts shared ones.
		 *
		 * Asserted on the source rather than by dispatching `contextmenu`, for the reason
		 * `PickPicture.contract.test.ts` gives: what is being held is that this screen goes through
		 * the shared component, and a rendered assertion would pass just as well against a second
		 * hand-written menu that happened to open. `DataRow` has its own test that verbs given to
		 * it reach both doors; this is the half that says this screen hands them over.
		 */
		const source = readFileSync('src/lib/settings-ui/Users.svelte', 'utf8');
		expect(source).toContain('<DataRow');
		expect(source).toMatch(/<DataRow[\s\S]*?verbs=\{userVerbs\(user\)\}/);
		expect(source, 'a bare RowMenu answers no right-click').not.toContain('<RowMenu');
	});

	it('draws the list through DataRows, which holds it still under a pointer', () => {
		// A live list that re-sorts under the cursor moves the row somebody is reaching for. The
		// shared row is the only place the rule against it is written.
		const source = readFileSync('src/lib/settings-ui/Users.svelte', 'utf8');
		expect(source).toContain('<DataRows');
	});
});
