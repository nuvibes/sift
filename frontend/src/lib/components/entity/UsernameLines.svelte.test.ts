/*
 * A person's usernames on one Site, under a card: where a username is shown.
 *
 * Checked through the wiring the two pages use (`UsernamesUnderCardsHarness`): a person's Sites tab
 * draws each username under its Site's card, a Site's People tab draws each person's usernames on
 * it under that person's card, and a stash-box profile link with nothing under it is drawn under
 * neither. Then the sheet a line opens: the ID typed where none is known and corrected behind a
 * confirmation, an ID another username has answered with the server's own sentence, the display
 * name and page link edited, the files one press away, and a wrong join taken off.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { words as wordsOn } from '$lib/design/testing.svelte';
import { flushSync, mount, tick, unmount } from 'svelte';
import linesSource from './UsernameLines.svelte?raw';

const mocks = vi.hoisted(() => ({
	load: vi.fn(),
	save: vi.fn(),
	detach: vi.fn(),
	goto: vi.fn(),
	toast: vi.fn()
}));

vi.mock('$lib/entity/related.svelte', async (importOriginal) => ({
	...(await importOriginal<typeof import('$lib/entity/related.svelte')>()),
	loadRelated: mocks.load
}));
vi.mock('$lib/people/usernames.svelte', async (importOriginal) => ({
	...(await importOriginal<typeof import('$lib/people/usernames.svelte')>()),
	usernames: { save: mocks.save, detach: mocks.detach }
}));
vi.mock('$app/navigation', () => ({ goto: mocks.goto }));
vi.mock('$lib/shell/toasts.svelte', () => ({ toasts: { show: mocks.toast } }));
vi.mock('$lib/people/people-picker', () => ({ askPeople: vi.fn(), makePerson: vi.fn() }));
vi.mock('$lib/shell/session.svelte', () => ({ session: { isAdmin: true } }));
/* Which handles Sift keeps a picture for, by Site: one, so a line with none draws no picture. */
vi.mock('$lib/entity/creator-art.svelte', async (importOriginal) => ({
	...(await importOriginal<typeof import('$lib/entity/creator-art.svelte')>()),
	usernameArt: (one: { username: string; site_name?: string | null }) =>
		one.username === 'nevealder' ? `/api/creator-art/nevealder?site=${one.site_name}` : null
}));

import Harness from './UsernamesUnderCardsHarness.svelte';
import { ApiError } from '$lib/api/client';
import type { Username } from '$lib/people/usernames.svelte';
import { noServerAt } from '../../../test-setup';

/* Left unanswered on purpose: the card art and the stash-boxes, read on the way past. */
noServerAt('/api/creator-art', '/api/stash-boxes');

function username(over: Partial<Username> = {}): Username {
	return {
		size_bytes: null,
		id: 'a-1',
		username: 'esmewrenfield',
		display_name: null,
		asset_count: 3,
		site_id: 's-1',
		site_name: 'SomeSite',
		person_id: 'p-1',
		person_name: 'Neve',
		number: null,
		number_said: null,
		number_via: null,
		site_icon: null,
		url: null,
		name_candidates: 0,
		...over
	};
}

let host: HTMLElement;
let drawn: Record<string, unknown> | null = null;

beforeEach(() => {
	vi.clearAllMocks();
	host = document.createElement('div');
	document.body.append(host);
});

afterEach(() => {
	if (drawn) unmount(drawn, { outro: false });
	drawn = null;
	host?.remove();
	document.body.innerHTML = '';
});

async function settle(): Promise<void> {
	for (let at = 0; at < 6; at++) await tick();
	flushSync();
}

async function draw(
	on: 'person' | 'site',
	showing: 'sites' | 'people',
	rows: { id: string; name: string }[],
	usernames: Username[]
): Promise<void> {
	mocks.load.mockResolvedValue({ items: rows, total: rows.length });
	drawn = mount(Harness, { target: host, props: { on, showing, usernames } }) as Record<
		string,
		unknown
	>;
	flushSync();
	await settle();
}

/** The card whose name is `name`, as an element to look inside. */
function card(name: string): Element | null {
	return (
		[...host.querySelectorAll('.card')].find((one) =>
			one.querySelector('.name')?.textContent?.includes(name)
		) ?? null
	);
}

/** Press the button reading exactly `words`, else the first whose words include them. */
function press(words: string, within: ParentNode = document.body): void {
	const buttons = [...within.querySelectorAll<HTMLButtonElement>('button')];
	(
		buttons.find((one) => wordsOn(one) === words) ??
		buttons.find((one) => one.textContent?.includes(words))
	)?.click();
	flushSync();
}

describe("a person's Sites tab", () => {
	it('draws the username under the card of the Site it is on, with its files and number', async () => {
		await draw(
			'person',
			'sites',
			[
				{ id: 's-1', name: 'SomeSite' },
				{ id: 's-2', name: 'OtherSite' }
			],
			[username({ number: '51234567' })]
		);

		expect(card('SomeSite')?.textContent).toContain('esmewrenfield');
		// What the count IS is said on the line, and the Site's number is its ID, never "no.".
		const line = card('SomeSite')?.textContent?.replace(/\s+/g, ' ') ?? '';
		expect(line).toContain('posted 3 files');
		expect(line).toContain('ID 51234567');
		expect(line).not.toContain('no.');
		expect(card('OtherSite')?.textContent).not.toContain('esmewrenfield');
	});

	it('does not draw a profile link with no files and no number', async () => {
		// A library can hold thousands of these: they are the record's Links, not usernames.
		await draw(
			'person',
			'sites',
			[{ id: 's-1', name: 'SomeSite' }],
			[username({ id: 'a-9', username: 'justalink', asset_count: 0 })]
		);

		expect(card('SomeSite')).not.toBeNull();
		expect(host.textContent).not.toContain('justalink');
		expect(host.querySelector('.usernames')).toBeNull();
	});
});

describe("a Site's People tab", () => {
	it("draws each person's username on this Site under that person's card", async () => {
		await draw(
			'site',
			'people',
			[
				{ id: 'p-1', name: 'Neve' },
				{ id: 'p-2', name: 'Wren' }
			],
			[
				username({ person_id: 'p-1' }),
				username({ id: 'a-2', username: 'wrenly', person_id: 'p-2', asset_count: 1 })
			]
		);

		expect(card('Neve')?.textContent).toContain('esmewrenfield');
		expect(card('Wren')?.textContent).toContain('wrenly');
		expect(card('Wren')?.textContent).toContain('1 file');
		expect(card('Neve')?.textContent).not.toContain('wrenly');
	});

	it("links the username's count to the Files wall narrowed to that username", async () => {
		await draw('site', 'people', [{ id: 'p-1', name: 'Neve' }], [username({ person_id: 'p-1' })]);

		const files = card('Neve')?.querySelector<HTMLAnchorElement>('a.files');
		expect(files?.getAttribute('href')).toBe('/browse?username=a-1');
		expect(files?.textContent).toBe('3 files');
		expect(files?.getAttribute('aria-label')).toBe('3 files posted under esmewrenfield');
	});
});

describe('the sheet a username line opens', () => {
	/** Type into the sheet's field labelled `label`. */
	function type(label: string, value: string): void {
		const field = [...document.body.querySelectorAll('label')].find(
			(one) => one.textContent?.trim() === label
		);
		const input = field?.htmlFor ? document.getElementById(field.htmlFor) : null;
		if (!(input instanceof HTMLInputElement)) throw new Error(`no field labelled ${label}`);
		input.value = value;
		input.dispatchEvent(new Event('input', { bubbles: true }));
		flushSync();
	}

	it('saves a typed ID where the Site has none known yet, with no question asked', async () => {
		mocks.save.mockResolvedValue(username({ number: '4242' }));
		await draw('person', 'sites', [{ id: 's-1', name: 'SomeSite' }], [username()]);

		press('esmewrenfield', host);
		await settle();
		type('SomeSite ID', ' 4242 ');
		press('Save ID');
		await settle();

		expect(mocks.save).toHaveBeenCalledWith('a-1', { number: '4242', replaceNumber: false });
		expect(document.body.textContent).not.toContain('Change the SomeSite ID?');
	});

	it('shows a known ID and where it came from, and corrects it only after asking', async () => {
		mocks.save.mockResolvedValue(username({ number: '5151' }));
		await draw(
			'person',
			'sites',
			[{ id: 's-1', name: 'SomeSite' }],
			[username({ number: '51234567', number_said: 'You typed this ID.' })]
		);

		press('esmewrenfield', host);
		await settle();
		expect(document.body.textContent).toContain('SomeSite ID 51234567');
		expect(document.body.textContent).toContain('You typed this ID.');

		press('Edit ID');
		await settle();
		type('SomeSite ID', '5151');
		press('Save ID');
		await settle();
		// Asked first: nothing written until the question is answered.
		expect(mocks.save).not.toHaveBeenCalled();
		expect(document.body.textContent).toContain('Change the SomeSite ID?');
		expect(document.body.textContent).toContain(
			'Future downloads and file names will match the new ID. Files already filed stay where they are.'
		);

		const confirm = [...document.body.querySelectorAll<HTMLButtonElement>('button.confirm')].at(-1);
		confirm?.click();
		await settle();
		expect(mocks.save).toHaveBeenCalledWith('a-1', { number: '5151', replaceNumber: true });
	});

	it("says the server's own sentence when another username on the Site has the ID", async () => {
		const said = 'wrenly already has this SomeSite ID. These may be the same person, renamed.';
		mocks.save.mockRejectedValue(new ApiError(409, 'That could not be done.', said));
		await draw('person', 'sites', [{ id: 's-1', name: 'SomeSite' }], [username()]);

		press('esmewrenfield', host);
		await settle();
		type('SomeSite ID', '51234567');
		press('Save ID');
		await settle();

		expect(document.body.textContent).toContain(said);
	});

	it('edits the display name and the page link', async () => {
		mocks.save.mockResolvedValue(username({ display_name: 'Neve A.' }));
		await draw('person', 'sites', [{ id: 's-1', name: 'SomeSite' }], [username()]);

		press('esmewrenfield', host);
		await settle();
		type('Display name', ' Neve A. ');
		type('Page link', 'https://somesite.example/esmewrenfield');
		press('Save');
		await settle();

		expect(mocks.save).toHaveBeenCalledWith('a-1', {
			display_name: 'Neve A.',
			url: 'https://somesite.example/esmewrenfield'
		});
	});

	it('offers no way to the files of a username with none', async () => {
		await draw(
			'person',
			'sites',
			[{ id: 's-1', name: 'SomeSite' }],
			[username({ asset_count: 0, number: '51234567' })]
		);
		press('esmewrenfield', host);
		await settle();
		expect(document.body.textContent).toContain('Belongs to');
		expect(document.body.textContent).not.toContain('Show the 0 files');
	});

	it('keeps naming the username once a display name is saved', async () => {
		await draw(
			'person',
			'sites',
			[{ id: 's-1', name: 'SomeSite' }],
			[username({ display_name: 'Neve A.' })]
		);
		expect(host.querySelector('[aria-label="esmewrenfield, username details"]')).not.toBeNull();

		press('esmewrenfield', host);
		await settle();
		expect(document.body.textContent).toContain('esmewrenfield on SomeSite');
		expect(document.body.textContent).not.toContain('Neve A. on SomeSite');
	});

	it('says why the username itself cannot be edited, and offers who it belongs to', async () => {
		await draw('person', 'sites', [{ id: 's-1', name: 'SomeSite' }], [username()]);

		press('esmewrenfield', host);
		await settle();

		expect(document.body.textContent).toContain('Belongs to Neve');
		expect(document.body.textContent).toContain('Choose another person');
		expect(document.body.textContent?.replace(/\s+/g, ' ')).toContain(
			"The username itself can't be edited: the files were filed under it, and Sift recognizes a rename by the ID."
		);
	});

	it("opens the username's files, never a page of its own", async () => {
		await draw('person', 'sites', [{ id: 's-1', name: 'SomeSite' }], [username()]);

		press('esmewrenfield', host);
		await settle();
		press('Show the 3 files');

		expect(mocks.goto).toHaveBeenCalledWith('/browse?username=a-1');
	});

	it('takes a wrongly joined username off the person', async () => {
		mocks.detach.mockResolvedValue(undefined);
		await draw('person', 'sites', [{ id: 's-1', name: 'SomeSite' }], [username()]);

		press('esmewrenfield', host);
		await settle();
		press('Remove from Neve');
		await settle();

		expect(mocks.detach).toHaveBeenCalledWith('a-1');
	});
});

describe('the picture a username is shown with', () => {
	it('draws it beside the handle, asked by that Site, and nothing where there is none', async () => {
		await draw(
			'person',
			'sites',
			[{ id: 's-1', name: 'SomeSite' }],
			[username(), username({ id: 'a-2', username: 'nevealder' })]
		);

		const marks = [...(card('SomeSite')?.querySelectorAll<HTMLImageElement>('img.mark') ?? [])];
		expect(marks.map((one) => one.getAttribute('src'))).toEqual([
			'/api/creator-art/nevealder?site=SomeSite'
		]);
	});
});

describe('a handle longer than its card', () => {
	/* At a phone's width a handle is one word, so the link's own wrapping cannot break it, and a
	   long one like "orlafennimore" would run past a card's inner edge and be cut by its border. */
	it('is cut with an ellipsis inside the card, as the name above it is', () => {
		const rule = linesSource.match(/\n\t\.usernames li > :global\(\.btn\)\s*\{([^}]*)\}/);
		expect(rule, 'no rule bounds the handle to its row').not.toBeNull();
		for (const line of [
			'min-inline-size: 0;',
			'max-inline-size: 100%;',
			'overflow: hidden;',
			'text-overflow: ellipsis;',
			'white-space: nowrap;'
		])
			expect(rule![1]).toContain(line);
	});
});
