import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { flushSync, mount } from 'svelte';
import { api } from '$lib/api/client';
import { updates, type UpdateState } from '$lib/shell/updates.svelte';
import Updates from './Updates.svelte';
import { COPY } from './Updates.search';

/* Who is looking. The check is an admin's; the foot is everybody's. */
const viewer = vi.hoisted(() => ({ isAdmin: true }));
vi.mock('$lib/shell/session.svelte', () => ({ session: viewer }));

/* The update check's row is the task's own, read from the shared task list; nothing here fetches. */
/* An install asked of the computer running Sift waits for its next run; the wait itself is
   `follow-switch`'s own, and here it simply has not ended. */
const followSwitch = vi.hoisted(() => vi.fn(() => new Promise<boolean>(() => {})));
vi.mock('./follow-switch', () => ({ followSwitch }));
vi.mock('$lib/shell/health', async (actual) => ({
	...(await actual<typeof import('$lib/shell/health')>()),
	serverBootId: async () => 'run-1'
}));

vi.mock('$lib/jobs/tasks.svelte', () => ({
	taskList: {
		row: () => undefined,
		pressing: {},
		failed: false,
		ensure: async () => {},
		setWhen: async () => {}
	},
	pressTask: vi.fn(async () => true)
}));

/* What the Updates section actually puts in front of somebody: the versions, the notes drawn as
 * text rather than markup, and the one button that installs, offered only where it can.
 */

let host: HTMLElement;

const AVAILABLE: UpdateState = {
	current_version: '1.4.0',
	latest_version: '1.5.0',
	update_available: true,
	notes:
		'## What changed\n\nFaster **thumbnails**.\n\n- one fix\n- [read more](https://releases.example/sift/tag/v1.5.0)\n- [elsewhere](https://evil.example/)\n\n<img src=x onerror=steal(1)>',
	release_page: 'https://releases.example/sift/tag/v1.5.0',
	last_checked: 0,
	dismissed: false
};

const NOTHING_KNOWN: UpdateState = {
	current_version: '1.4.0',
	latest_version: null,
	update_available: false,
	notes: '',
	release_page: '',
	last_checked: 0,
	dismissed: false
};

function show(state: UpdateState) {
	// The foot's version comes from the public endpoint, answered here.
	vi.spyOn(api, 'get').mockImplementation(async (path: string) => {
		if (path === '/update/version') return { version: '1.4.0' } as never;
		throw new Error('offline');
	});
	// The section reads the shared instance, so the state is put there rather than fetched.
	vi.spyOn(updates, 'load').mockImplementation(async () => {
		updates.state = state;
		updates.loaded = true;
	});
	host = document.createElement('div');
	document.body.append(host);
	mount(Updates, { target: host });
	flushSync();
}

function text(): string {
	return host.textContent ?? '';
}

beforeEach(() => {
	viewer.isAdmin = true;
	updates.state = null;
	updates.loaded = false;
	updates.unavailable = false;
});

afterEach(() => {
	vi.restoreAllMocks();
	host?.remove();
	document.body.innerHTML = '';
});

/* WHEN Sift checks is the update check task's When, upkeep that runs on its own in the background,
   so no pane draws a row for it; WHETHER it checks is the one switch further down. */
it("draws no row for the update check's When, which runs on its own in the background", () => {
	show(NOTHING_KNOWN);
	expect(host.querySelector('[id="tasks.update-check.when"]')).toBeNull();
	expect(host.querySelector('#updates\\.when')).toBeNull();
});

/* THE FOOT. The licence is not decoration: Sift is AGPL-3.0, which asks a network
   service to offer its source to the people who use it, so the terms stay one press away for
   everybody, a guest included. */
describe('the foot', () => {
	it('names the licence, the source and the notices, after everything else', () => {
		show(NOTHING_KNOWN);

		// The address is on each block, which is what a search result rings. No About block: the
		// version is the pane's first line.
		const version = host.querySelector('#updates\\.version');
		const license = host.querySelector('#updates\\.license')?.closest('section');
		expect(version, 'the block the Version search result rings').not.toBeNull();
		expect(host.querySelector('#updates\\.about')).toBeNull();
		expect(license?.textContent).toContain('GNU AGPL-3.0-or-later');
		const links = [...(license?.querySelectorAll('a') ?? [])].map((one) =>
			one.getAttribute('href')
		);
		expect(links).toContain('https://www.gnu.org/licenses/agpl-3.0.html');
		expect(links.some((href) => href?.endsWith('/NOTICE'))).toBe(true);
		// Last on the pane: nothing an admin sets comes after the terms.
		const blocks = [...host.querySelectorAll('[id]')].map((one) => one.id);
		expect(blocks.indexOf('updates.license')).toBeGreaterThan(
			blocks.indexOf('updates.download_tools')
		);
	});

	it('says the version once, first: from the check for an admin, from the public read for a guest', async () => {
		show(NOTHING_KNOWN);
		await Promise.resolve();
		flushSync();
		expect(host.querySelectorAll('#updates\\.version')).toHaveLength(1);
		expect(text().split('You are running')).toHaveLength(2);
		host.remove();

		viewer.isAdmin = false;
		show(NOTHING_KNOWN);
		for (let turn = 0; turn < 4; turn += 1) await Promise.resolve();
		flushSync();
		expect(text()).not.toContain('Download tools');
		const said = host.querySelector('#updates\\.version')?.textContent ?? '';
		expect(said).toContain(COPY.running);
		expect(said).toContain('Sift 1.4.0');
		expect(text()).toContain(COPY.license.name);
	});

	/* On a client there are two copies of Sift in play, the library's and the one in front of you,
	   and they update separately. Somebody checking whether this computer is up to date has to
	   read both, agreeing or not; on one computer there is one. */
	it('names the library and this device separately on a client, and one version otherwise', async () => {
		viewer.isAdmin = false;
		const two = Promise.resolve('1.3.0');
		window.sift = { shellVersion: () => two };
		show(NOTHING_KNOWN);
		await two;
		for (let turn = 0; turn < 4; turn += 1) await Promise.resolve();
		flushSync();
		const foot = () => host.querySelector('#updates\\.version')?.textContent ?? '';
		expect(foot()).toContain(COPY.thisCopy);
		expect(foot()).toContain(COPY.elsewhere);
		expect(foot()).toContain('1.3.0');
		expect(foot()).toContain('Sift 1.4.0');
		host.remove();
		delete window.sift;

		const one = Promise.resolve(null);
		window.sift = { shellVersion: () => one };
		show(NOTHING_KNOWN);
		await one;
		for (let turn = 0; turn < 4; turn += 1) await Promise.resolve();
		flushSync();
		expect(foot()).toContain(COPY.running);
		expect(foot()).not.toContain(COPY.thisCopy);
		delete window.sift;
	});
});

/* The download tools' versions and the yt-dlp check live here: "is what Sift runs up to date" is
   this pane's question. Drawn whatever the update state is, with the
   address a search result and a moved link ring. */
it('draws the download tools and their check under the update', () => {
	show(NOTHING_KNOWN);
	expect(text()).toContain('Download tools');
	expect(host.querySelector('#updates\\.download_tools')).not.toBeNull();
});

describe('when an update is available', () => {
	afterEach(() => {
		delete window.sift;
	});

	it('shows both versions and the notes', () => {
		show(AVAILABLE);

		expect(text()).toContain('1.4.0');
		expect(text()).toContain('1.5.0 is available');
		expect(text()).toContain('Faster thumbnails.');
	});

	/* A safe subset of Markdown, drawn as text nodes: a heading, bold, a list. And an HTML tag in
	   the notes is shown as the characters it is, never made into an element. */
	it('draws the notes as a small Markdown subset, with no markup of their own', () => {
		show(AVAILABLE);
		const notes = host.querySelector('.notes-body');

		expect(notes?.querySelector('.note-heading[data-level="2"]')?.textContent?.trim()).toBe(
			'What changed'
		);
		expect(notes?.querySelector('strong')?.textContent).toBe('thumbnails');
		expect(notes?.querySelectorAll('li')).toHaveLength(3);
		expect(notes?.querySelector('img')).toBeNull();
		expect(notes?.textContent).toContain('<img src=x onerror=steal(1)>');
	});

	it('links only to the release own page', () => {
		show(AVAILABLE);
		const links = [...(host.querySelector('.notes-body')?.querySelectorAll('a') ?? [])];

		expect(links.map((one) => one.getAttribute('href'))).toEqual([
			'https://releases.example/sift/tag/v1.5.0'
		]);
		expect(host.querySelector('.notes-body')?.textContent).toContain('elsewhere');
	});

	/* Anywhere but the desktop app on the device running this library, the button would install on
	   the wrong machine or on none, so the screen says where to go instead. */
	it('says where to install it when this window cannot', () => {
		show(AVAILABLE);

		expect(text()).toContain('Install it from the Sift app on the device that runs this library');
		const labels = [...host.querySelectorAll('button')].map((b) => b.textContent?.toLowerCase());
		for (const forbidden of ['update now', 'install', 'apply', 'restart', 'upgrade', 'copy']) {
			expect(labels.some((label) => label?.includes(forbidden))).toBe(false);
		}
	});

	it('offers the button where the desktop app can install', () => {
		window.sift = { applyUpdate: async () => ({ ok: true, version: '1.5.0' }) };
		show(AVAILABLE);

		const labels = [...host.querySelectorAll('button')].map((b) => b.textContent ?? '');
		expect(labels.some((label) => label.includes('Download and install Sift 1.5.0'))).toBe(true);
		expect(text()).not.toContain('Install it from the Sift app');
	});

	/* From ANOTHER computer, where the Sift app on the computer running Sift answers through the
	   server: the same one button, installing THERE, whose installer opens on that computer. */
	function showThere(answer: { ok: boolean; version: string | null; reason: string | null }) {
		const post = vi.spyOn(api, 'post').mockResolvedValue(answer as never);
		vi.spyOn(api, 'get').mockImplementation(async (path: string) => {
			if (path === '/update/version') return { version: '1.4.0' } as never;
			if (path === '/desktop') {
				return {
					has_app: true,
					machine: 'DESK-ONE',
					starts_with_windows: null,
					sharing: null
				} as never;
			}
			throw new Error('offline');
		});
		vi.spyOn(updates, 'load').mockImplementation(async () => {
			updates.state = AVAILABLE;
			updates.loaded = true;
		});
		host = document.createElement('div');
		document.body.append(host);
		mount(Updates, { target: host });
		flushSync();
		return post;
	}

	async function settleThere() {
		for (let turn = 0; turn < 12; turn += 1) await Promise.resolve();
		flushSync();
	}

	it('installs on the computer running Sift, and says to confirm the installer there', async () => {
		const post = showThere({ ok: true, version: '1.5.0', reason: null });
		await settleThere();

		const press = [...host.querySelectorAll('button')].find((b) =>
			b.textContent?.includes(COPY.there.action('1.5.0', 'DESK-ONE'))
		);
		expect(press, 'the install-there button').toBeTruthy();
		press?.click();
		await settleThere();

		expect(post).toHaveBeenCalledWith('/desktop/update');
		expect(text()).toContain(COPY.there.agreeThere('DESK-ONE'));
		expect(followSwitch).toHaveBeenCalledOnce();
	});

	it('says why the app there installs nothing, and waits for nothing', async () => {
		followSwitch.mockClear();
		showThere({ ok: false, version: null, reason: 'none' });
		await settleThere();

		[...host.querySelectorAll('button')]
			.find((b) => b.textContent?.includes(COPY.there.action('1.5.0', 'DESK-ONE')))
			?.click();
		await settleThere();

		expect(text()).not.toContain(COPY.there.agreeThere('DESK-ONE'));
		expect(followSwitch).not.toHaveBeenCalled();
	});

	it('names the release page', () => {
		show(AVAILABLE);

		const page = [...host.querySelectorAll('a')].find((one) =>
			one.textContent?.includes('Read about this release')
		);
		expect(page?.getAttribute('href')).toBe('https://releases.example/sift/tag/v1.5.0');
	});
});

describe('when nothing is known', () => {
	it('says only when it last checked, once there is a last check to show', () => {
		/* "Sift hasn't been able to check" over "Last checked 2 hours ago" would say two things about
		   one check. */
		show({ ...NOTHING_KNOWN, last_checked: Math.floor(Date.now() / 1000) - 7200 });

		expect(text()).toContain('Last checked');
		expect(text()).not.toContain("hasn't been able to check");
	});

	it('says so calmly and does not read as a failure', () => {
		show(NOTHING_KNOWN);

		expect(text()).toContain("hasn't been able to check");
		expect(text()).toContain('Everything else works as normal');
		// Nothing to install: no install section at all.
		expect(text()).not.toContain('How to update');
	});

	it('never claims an update it does not have', () => {
		show(NOTHING_KNOWN);

		expect(text()).not.toContain('is available');
	});
});

describe('when the running version is the newest', () => {
	it('says so and offers nothing to install', () => {
		show({ ...NOTHING_KNOWN, latest_version: '1.4.0' });

		expect(text()).toContain('This is the newest version');
		expect(text()).not.toContain('How to update');
	});
});

/* TWO COMPUTERS, AND WHICH NUMBER BELONGS TO WHICH.
 *
 * In client mode this whole screen is served by the machine holding the library, so every version
 * on it is that machine's, while the install button acts on the one in front of you. A copy two
 * releases behind must not read as "the newest version" because the number it was compared
 * against was never its own.
 */
describe('when the application and the library are different computers', () => {
	afterEach(() => {
		delete window.sift;
	});

	/* THESE WAIT ON THE ANSWER RATHER THAN ON THE SCREEN, and the difference is not pedantry.
	 *
	 * The ones asserting an ABSENCE expect a sentence that is also on the screen before the version
	 * has arrived at all. Written as `waitFor(...)` on the text, the first poll would pass on the
	 * not-yet-answered screen and the assertion run against it, so breaking the rule they exist
	 * for would leave them green. Awaiting the same promise the component awaits, then flushing, is what
	 * makes them about the settled screen. */
	async function settled(answer: Promise<string | null>): Promise<void> {
		await answer;
		// The component's own `.then` is queued behind this one; let it run, then apply its effect.
		await Promise.resolve();
		flushSync();
	}

	/* An ANSWER means there are two computers: the shell gives one only in client mode. So these
	 * are tests of what the screen does with an answer, not of a comparison it makes. */
	it('names both, and says which is which', async () => {
		const answer = Promise.resolve('1.3.0');
		window.sift = { shellVersion: () => answer };
		show(NOTHING_KNOWN);

		await settled(answer);
		expect(text()).toContain('This copy of Sift is');
		expect(text()).toContain('1.3.0');
		expect(text()).toContain('another device');
		expect(text()).toContain('1.4.0');
	});

	it('says plainly that updating here leaves the library where it was', async () => {
		const answer = Promise.resolve('1.3.0');
		window.sift = { shellVersion: () => answer };
		show(NOTHING_KNOWN);

		await settled(answer);
		expect(text()).toContain('only this device');
	});

	/* BOTH NUMBERS EVEN WHEN THEY AGREE, the case a comparison would get wrong. Somebody checking
	 * whether their client is up to date has to be able to read both numbers; a screen that speaks
	 * up only when they disagree answers that question by silence. */
	it('names both even when the two agree', async () => {
		const answer = Promise.resolve('1.4.0');
		window.sift = { shellVersion: () => answer };
		show(NOTHING_KNOWN);

		await settled(answer);
		expect(text()).toContain('This copy of Sift is');
		expect(text()).toContain('another device');
	});

	/* The known negative. On the machine holding the library the backend travels inside the
	 * application, so there is no second version: the shell answers nothing there, and a screen
	 * saying it twice would invite somebody to look for a difference that cannot exist. */
	it('says it once where there is only one computer', async () => {
		const answer = Promise.resolve(null);
		window.sift = { shellVersion: () => answer };
		show(NOTHING_KNOWN);

		await settled(answer);
		expect(text()).toContain('You are running');
		expect(text()).not.toContain('another device');
	});

	/* A browser has no copy of its own to be out of date. Claiming one there would be a sentence
	 * about a second computer to somebody who has one computer. */
	it('says nothing about a second computer in a browser', async () => {
		const answer = Promise.resolve(null);
		show(NOTHING_KNOWN);

		await settled(answer);
		expect(text()).toContain('You are running');
		expect(text()).not.toContain('another device');
	});
});

/*
 * ONE PLAIN SWITCH over the one request Sift makes on its own. It is the update check's retired
 * on/off key, which the server answers from the check's When and writes into it; the row carries
 * that key's address, so an old link to the switch lands on it. And a Check now beside it, so off
 * still leaves a way to ask.
 */
describe('the update switch', () => {
	function withWhen(when: string) {
		const put = vi.spyOn(api, 'put').mockResolvedValue(undefined as never);
		vi.spyOn(api, 'get').mockImplementation(async (path: string) => {
			if (path === '/update/version') return { version: '1.4.0' } as never;
			if (path === '/settings')
				return {
					sections: [
						{
							name: 'Scheduled tasks',
							settings: [{ key: 'tasks.update-check.when', value: when, default: 'work' }]
						}
					]
				} as never;
			throw new Error('offline');
		});
		vi.spyOn(updates, 'load').mockImplementation(async () => {
			updates.state = NOTHING_KNOWN;
			updates.loaded = true;
		});
		host = document.createElement('div');
		document.body.append(host);
		mount(Updates, { target: host });
		flushSync();
		return put;
	}

	async function settled() {
		for (let turn = 0; turn < 6; turn += 1) await Promise.resolve();
		flushSync();
	}

	it('reads on from a When that starts on its own, and off from Only when I press it', async () => {
		withWhen('work');
		await settled();
		const row = host.querySelector('[id="updates.check_for_new_versions"]');
		expect(row?.textContent).toContain(COPY.checking.label);
		expect(row?.querySelector('[role="switch"]')?.getAttribute('aria-checked')).toBe('true');
		expect(row?.textContent).toContain(COPY.checking.now);
	});

	it('writes the retired key, which the server turns into the When', async () => {
		const put = withWhen('press');
		await settled();
		const toggle = host.querySelector<HTMLElement>(
			'[id="updates.check_for_new_versions"] [role="switch"]'
		);
		expect(toggle?.getAttribute('aria-checked')).toBe('false');
		toggle!.click();
		await settled();
		expect(put).toHaveBeenCalledWith('/settings', {
			body: { values: { 'updates.check_for_new_versions': true } }
		});
	});

	it('waits on Check now until Last checked moves, not until the answer is read again', async () => {
		// The bell rings as the run is queued too, and that re-read hands back the old time in a new
		// object. Ending the wait there would leave Last checked where it was until a reload.
		withWhen('press');
		await settled();
		const now = [...host.querySelectorAll<HTMLButtonElement>('button')].find((one) =>
			one.textContent?.includes(COPY.checking.now)
		);
		now!.click();
		await settled();
		expect(now!.disabled).toBe(true);

		updates.state = { ...NOTHING_KNOWN };
		flushSync();
		expect(now!.disabled).toBe(true);

		updates.state = { ...NOTHING_KNOWN, last_checked: 1_700_000_000 };
		flushSync();
		expect(now!.disabled).toBe(false);
	});
});

it('copies the version when it is pressed, the one copy helper the file name uses', async () => {
	show(NOTHING_KNOWN);
	await Promise.resolve();
	flushSync();
	const press = host.querySelector('.running .copyable');
	expect(press?.textContent).toContain('Sift 1.4.0');
});

/* A link to a row the pane leaves out says why rather than that the setting moved. */
describe('a link to a row that is not drawn', () => {
	it('says no notice is hidden, and that the notes wait for a new version', async () => {
		show(NOTHING_KNOWN);
		host.className = 'section-body';
		const { toasts } = await import('$lib/shell/toasts.svelte');
		const { revealSetting, ROW_NOT_FOUND } = await import('./settings-anchor.svelte');
		const said = vi.spyOn(toasts, 'show');

		for (const key of ['updates.dismissed_version', 'updates.notice']) {
			said.mockClear();
			await expect(revealSetting(key), key).resolves.toBe(false);
			expect(said).toHaveBeenCalledWith(COPY.nothingHidden);
		}
		said.mockClear();
		await expect(revealSetting('notes-heading')).resolves.toBe(false);
		expect(said).toHaveBeenCalledWith('Release notes are shown here when a new version is out.');
		expect(said).not.toHaveBeenCalledWith(ROW_NOT_FOUND);
	});
});
