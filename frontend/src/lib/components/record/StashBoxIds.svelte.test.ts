/*
 * Which entry in each stash-box a person, a Site or a tag is: the box by name, the id, the box's
 * own page, and Remove for an admin.
 *
 * The rules defended are the ones a screenshot cannot show: the page opened is the one the SERVER
 * named (never an address built here), an id with no page is text rather than a guessed link, a
 * guest is offered nothing to press but the page, and Remove asks first and then asks the page to
 * read the links again rather than editing a copy of its own.
 */
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { flushSync, mount, tick, unmount } from 'svelte';
import type { StashBoxLink } from '$lib/entity/enrich.svelte';
import { toasts } from '$lib/shell/toasts.svelte';

const mocks = vi.hoisted(() => ({ forgetLink: vi.fn() }));

vi.mock('$lib/entity/enrich.svelte', () => ({
	forgetLink: mocks.forgetLink,
	problemFrom: () => 'That stash-box link could not be forgotten.'
}));

vi.mock('$lib/entity/records.svelte', () => ({
	fields: {
		one: (_subject: string, key: string) =>
			({ height_cm: { label: 'Height' }, birth_date: { label: 'Birthdate' } })[key]
	}
}));

import StashBoxIds from './StashBoxIds.svelte';

function linked(over: Partial<StashBoxLink> = {}): StashBoxLink {
	return {
		box_id: 'box-1',
		box_name: 'StashDB',
		box_slug: 'stashdb',
		remote_id: '0b5d3c1e-7f2a-4c9e-9a41-2d6f8e1b3a70',
		page_url: 'https://stashdb.example/performers/0b5d3c1e-7f2a-4c9e-9a41-2d6f8e1b3a70',
		fetched_at: 1,
		record: {
			source_id: 'box-1',
			source_name: 'StashDB',
			remote_id: '0b5d3c1e-7f2a-4c9e-9a41-2d6f8e1b3a70',
			subject: 'person',
			name: 'Esme Wrenfield',
			disambiguation: null,
			image_url: null,
			icon_slug: null,
			file_count: null,
			every_word: true,
			fields: {},
			extra: {},
			confidence: 1
		},
		...over
	} as StashBoxLink;
}

let host: HTMLElement;
let drawn: Record<string, unknown> | null = null;

beforeEach(() => {
	mocks.forgetLink.mockReset();
	mocks.forgetLink.mockResolvedValue(undefined);
	toasts.clear();
});

afterEach(() => {
	if (drawn) unmount(drawn);
	drawn = null;
	host?.remove();
	document.body.innerHTML = '';
});

function draw(props: {
	links: StashBoxLink[];
	mayForget?: boolean;
	onforgot?: () => void;
}): HTMLElement {
	host = document.createElement('div');
	document.body.append(host);
	drawn = mount(StashBoxIds, {
		target: host,
		props: { subject: 'person', id: 'p1', name: 'Esme Wrenfield', ...props }
	}) as Record<string, unknown>;
	flushSync();
	return host;
}

it('draws nothing at all where no stash-box knows them', () => {
	const drawnHost = draw({ links: [] });

	expect(drawnHost.textContent?.trim()).toBe('');
	expect(drawnHost.querySelector('ul')).toBeNull();
});

it("names each box and opens the box's own page for the id, as the server named it", () => {
	const drawnHost = draw({
		links: [
			linked(),
			linked({
				box_id: 'box-2',
				box_name: 'FansDB',
				remote_id: 'f-77',
				page_url: 'https://fansdb.example/performers/f-77'
			})
		]
	});

	expect(drawnHost.textContent).toContain('Stash-boxes');
	const rows = [...drawnHost.querySelectorAll('li')].filter((one) => one.querySelector('a'));
	expect(
		rows.map((one) => [
			one.querySelector('.box')?.textContent?.trim(),
			one.querySelector('.id')?.textContent?.trim()
		])
	).toEqual([
		['StashDB', '0b5d3c1e-7f2a-4c9e-9a41-2d6f8e1b3a70'],
		['FansDB', 'f-77']
	]);
	const pages = [...drawnHost.querySelectorAll('a')];
	expect(pages.map((one) => one.getAttribute('href'))).toEqual([
		'https://stashdb.example/performers/0b5d3c1e-7f2a-4c9e-9a41-2d6f8e1b3a70',
		'https://fansdb.example/performers/f-77'
	]);
	// Somewhere else entirely, so never in place of the page the person was on.
	expect(pages.every((one) => one.getAttribute('target') === '_blank')).toBe(true);
	expect(pages.every((one) => one.getAttribute('rel')?.includes('noopener'))).toBe(true);
});

it('draws an id the server has no page for as text, never as a guessed link', () => {
	const drawnHost = draw({ links: [linked({ page_url: null, remote_id: 'r-9' })] });

	expect(drawnHost.textContent).toContain('r-9');
	expect(drawnHost.querySelector('a')).toBeNull();
});

it('offers a guest nothing to press but the page', () => {
	const drawnHost = draw({ links: [linked()] });

	expect(drawnHost.querySelector('button')).toBeNull();
});

it('forgets a link only once the question is answered, then asks the page to read again', async () => {
	const onforgot = vi.fn();
	const drawnHost = draw({ links: [linked()], mayForget: true, onforgot });

	const press = drawnHost.querySelector<HTMLButtonElement>(
		'button[aria-label="Remove the link to StashDB"]'
	);
	expect(press).not.toBeNull();
	press?.click();
	flushSync();

	expect(mocks.forgetLink).not.toHaveBeenCalled();
	const dialog = document.querySelector('[role="alertdialog"]');
	expect(dialog?.textContent).toContain('Remove the StashDB link from Esme Wrenfield?');
	expect(dialog?.textContent).toContain('Everything it filled in stays.');

	document.querySelector<HTMLButtonElement>('.confirm')?.click();
	flushSync();
	await tick();
	await vi.waitFor(() => expect(onforgot).toHaveBeenCalledOnce());

	expect(mocks.forgetLink).toHaveBeenCalledWith('person', 'p1', 'box-1');
});

it("says so when the link could not be forgotten, and leaves the page's list alone", async () => {
	mocks.forgetLink.mockRejectedValue(new Error('no'));
	const onforgot = vi.fn();
	const drawnHost = draw({ links: [linked()], mayForget: true, onforgot });

	drawnHost
		.querySelector<HTMLButtonElement>('button[aria-label="Remove the link to StashDB"]')
		?.click();
	flushSync();
	document.querySelector<HTMLButtonElement>('.confirm')?.click();
	flushSync();

	// The sentence the module words a failure in, as the toast shows it (its full stop is the
	// toast's to tidy).
	await vi.waitFor(() =>
		expect(toasts.items.map((one) => one.message).join(' ')).toContain(
			'That stash-box link could not be forgotten'
		)
	);
	expect(onforgot).not.toHaveBeenCalled();
});

it('says under each box what it filled in that the record still holds as it gave it', () => {
	/* The same `gave` the record's hover reads, so the band and the hover cannot disagree about
	   where a value came from. A key the record does not describe is left out rather than guessed. */
	const drawnHost = draw({
		links: [
			linked({ gave: ['birth_date', 'height_cm', 'no_such_field'] }),
			linked({ box_id: 'box-2', box_name: 'FansDB', remote_id: 'f-77', gave: ['height_cm'] }),
			linked({ box_id: 'box-3', box_name: 'PMVStash', remote_id: 'p-1', gave: [] })
		]
	});

	expect([...drawnHost.querySelectorAll('.gave')].map((one) => one.textContent?.trim())).toEqual([
		'Filled in birthdate and height',
		'Filled in height'
	]);
});
