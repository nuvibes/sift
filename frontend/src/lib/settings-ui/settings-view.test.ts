import { beforeEach, describe, expect, it, vi } from 'vitest';

const pushState = vi.fn();
const replaceState = vi.fn();
const goto = vi.fn<(...args: unknown[]) => Promise<void>>(async () => undefined);
vi.mock('$app/navigation', () => ({
	goto: (...args: unknown[]) => goto(...args),
	pushState: (...args: unknown[]) => pushState(...args),
	replaceState: (...args: unknown[]) => replaceState(...args)
}));

/* The panel reads `page.state` to carry `direct` from one section to the next. A stand-in, so the
   flag can be set to both of the things it can be. A stand-in that could only ever answer one of
   them would make the carrying rule untestable in exactly the direction it matters. */
const pageState: { direct?: boolean } = {};
const address = { path: '/settings/privacy' };
vi.mock('$app/state', () => ({
	page: {
		get state() {
			return pageState;
		},
		get url() {
			return new URL(`http://localhost${address.path}`);
		}
	}
}));

/* The hunt for the row is its own module with its own test; here it is only asked the right key. */
const revealSetting = vi.fn<(key: string) => Promise<boolean>>(async () => true);
vi.mock('$lib/settings-ui/settings-anchor.svelte', () => ({
	revealSetting: (key: string) => revealSetting(key)
}));

const {
	enterSettings,
	openSettings,
	openSettingsInstead,
	SETTINGS_LIST_ON_A_PHONE,
	showSettingsSection
} = await import('./settings-view');

/* Opening settings without leaving the screen you were on.
 *
 * The address still changes, and it changes to the section's own real address: that is what keeps
 * a section linkable and the back button meaningful. What is worth pinning down is the click
 * handling, because the whole reason these stayed anchors is that a modified click must still do
 * what the browser would have done with it.
 */

beforeEach(() => {
	pushState.mockClear();
	replaceState.mockClear();
	goto.mockClear();
	address.path = '/settings/privacy';
});

function click(over: Partial<MouseEvent> = {}): MouseEvent {
	return {
		button: 0,
		metaKey: false,
		ctrlKey: false,
		shiftKey: false,
		altKey: false,
		defaultPrevented: false,
		preventDefault: vi.fn(),
		...over
	} as unknown as MouseEvent;
}

describe('opening the panel', () => {
	it('puts the section in the address, so it can be linked and gone back from', () => {
		openSettings('privacy');

		expect(pushState).toHaveBeenCalledWith('/settings/privacy', { settings: 'privacy' });
	});

	it('replaces rather than pushes when moving between sections', () => {
		/* Six sections looked at is six presses of Back before somebody is returned to the grid they
		 * opened settings from. The panel is one stop in the history; which section it happens to be
		 * showing is not a place you navigated to. */
		showSettingsSection('backup');

		expect(replaceState).toHaveBeenCalledWith('/settings/backup', { settings: 'backup' });
		expect(pushState).not.toHaveBeenCalled();
	});
});

describe('a click on a settings link', () => {
	it('opens the panel and does not follow the link', () => {
		const event = click();

		openSettingsInstead(event);

		expect(event.preventDefault).toHaveBeenCalled();
		expect(pushState).toHaveBeenCalled();
	});

	it('takes the section it was given', () => {
		openSettingsInstead(click(), 'updates');

		expect(pushState).toHaveBeenCalledWith('/settings/updates', { settings: 'updates' });
	});

	for (const [name, over] of [
		['ctrl-click', { ctrlKey: true }],
		['command-click', { metaKey: true }],
		['shift-click', { shiftKey: true }],
		['alt-click', { altKey: true }],
		['a middle click', { button: 1 }]
	] as const) {
		it(`leaves ${name} alone, because it asked for something this window cannot give`, () => {
			/* Every one of these means "open it somewhere else". Answering with a panel in THIS window
			 * ignores what was asked for AND swallows the navigation, so nothing happens at all. */
			const event = click(over);

			openSettingsInstead(event);

			expect(event.preventDefault).not.toHaveBeenCalled();
			expect(pushState).not.toHaveBeenCalled();
		});
	}

	it('leaves a click something else has already handled alone', () => {
		const event = click({ defaultPrevented: true });

		openSettingsInstead(event);

		expect(pushState).not.toHaveBeenCalled();
	});
});

/*
 * Landing on a settings address COLD, which is the case that has no screen behind it.
 *
 * There is no page form of settings: one address that draws a panel or a whole screen depending on
 * how it was reached is two screens, and refreshing while watching something would replace the
 * player with a different one. So the route puts the panel up either way, and the panel has to
 * know which it is, because closing one that was landed on cold cannot be a step BACK. There is
 * nothing behind it but the browser, and a step back leaves Sift.
 */
describe('an address landed on cold', () => {
	it('opens the panel with nothing behind it, and says so', () => {
		enterSettings('playback');

		expect(replaceState).toHaveBeenCalledWith(window.location.href, {
			settings: 'playback',
			direct: true
		});
	});

	it('keeps the row it arrived with in the address, so a refresh rings it again', () => {
		// `''` resolves to this address WITHOUT its fragment, and the fragment is the row.
		window.history.replaceState(null, '', '/settings/performance#performance.keeping_up');

		enterSettings('performance', 'performance.keeping_up');

		expect(replaceState.mock.calls[0][0]).toMatch(
			/\/settings\/performance#performance\.keeping_up$/
		);
	});

	it('replaces rather than pushes, so one Back leaves the way it would from any first page', () => {
		enterSettings('playback');

		expect(pushState).not.toHaveBeenCalled();
	});

	it('carries that forward when somebody walks to another section', () => {
		// Walking between sections does not put a screen behind the panel, so a panel opened cold is
		// still one after five of them, and closing it still has to go to the library.
		pageState.direct = true;
		showSettingsSection('privacy');

		expect(replaceState).toHaveBeenCalledWith('/settings/privacy', {
			settings: 'privacy',
			direct: true
		});
	});

	it('and does not invent it for a panel opened from inside the app', () => {
		// The other value, because a flag that is always true is a flag that says nothing.
		pageState.direct = undefined;
		showSettingsSection('privacy');

		expect(replaceState).toHaveBeenCalledWith('/settings/privacy', {
			settings: 'privacy',
			direct: undefined
		});
	});
});

/*
 * EVERY DOOR THROUGH THE ONE RESOLVER. A link, a search result and a cold address can each carry an
 * OLD address (a section folded into another, one that became a tab, a row that moved panes),
 * and each has to write the CURRENT address and hunt for the row where it is drawn now.
 */
describe('an old address, through any door', () => {
	it('a link to a retired section opens the section that inherited it, keeping the row', () => {
		openSettings('theater', 'theater.layout');

		expect(pushState).toHaveBeenCalledWith('/settings/playback#theater.layout', {
			settings: 'playback'
		});
	});

	it('a section that became a tab is opened ON that tab', () => {
		showSettingsSection('ledger');

		expect(replaceState).toHaveBeenCalledWith('/settings/tasks?show=history', {
			settings: 'tasks',
			direct: undefined
		});
	});

	it('a row that moved off a section that still exists is followed to where it is now', () => {
		openSettings('appearance', 'appearance.closing_the_window');

		expect(pushState).toHaveBeenCalledWith('/settings/general#general.closing_the_window', {
			settings: 'general'
		});
		expect(revealSetting).toHaveBeenLastCalledWith('general.closing_the_window');
	});

	it('a cold old address is REWRITTEN, so a refresh or a copy after it is the current one', () => {
		enterSettings('logs');

		expect(replaceState).toHaveBeenCalledWith('/settings/tasks?show=log', {
			settings: 'tasks',
			direct: true
		});
	});
});

/*
 * On a phone the list of sections is a screen of its own (More), because there is room for the list
 * or a section and never both. An address that names no section is asking for that list, and landing
 * it on the first section instead would lead every way back to Folders again.
 */
describe('Settings with no section, on a phone', () => {
	function atWidth(phone: boolean) {
		vi.stubGlobal('matchMedia', (query: string) => ({ matches: phone, media: query }));
	}

	it('is the More list, in place of the address rather than after it', () => {
		atWidth(true);
		address.path = '/settings';

		enterSettings('library');

		expect(goto).toHaveBeenCalledWith(SETTINGS_LIST_ON_A_PHONE.href, { replaceState: true });
		expect(replaceState).not.toHaveBeenCalled();
		vi.unstubAllGlobals();
	});

	it('names the More tab as where the list is', () => {
		expect(SETTINGS_LIST_ON_A_PHONE).toEqual({ href: '/more', label: 'More' });
	});

	it('still opens a section an address names, on a phone', () => {
		atWidth(true);
		address.path = '/settings/privacy';

		enterSettings('privacy');

		expect(goto).not.toHaveBeenCalled();
		expect(replaceState).toHaveBeenCalled();
		vi.unstubAllGlobals();
	});

	it('opens on the first section on a desktop, where the list is beside it', () => {
		atWidth(false);
		address.path = '/settings';

		enterSettings('library');

		expect(goto).not.toHaveBeenCalled();
		expect(replaceState).toHaveBeenCalled();
		vi.unstubAllGlobals();
	});
});
