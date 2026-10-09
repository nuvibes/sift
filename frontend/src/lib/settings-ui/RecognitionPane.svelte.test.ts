/* The one Recognition layout: Faces, Smart Search, Watermarks and Stash-boxes read the same way. */

import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { createRawSnippet, flushSync, mount, tick, unmount } from 'svelte';

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

import RecognitionPane, { deviceWords } from './RecognitionPane.svelte';
import { drilldown } from './drilldown.svelte';

function said(html: string) {
	return createRawSnippet(() => ({ render: () => html }));
}

let host: HTMLElement;
let drawn: Record<string, unknown> | null = null;

beforeEach(() => {
	host = document.createElement('div');
	document.body.append(host);
});

afterEach(() => {
	if (drawn) unmount(drawn);
	drawn = null;
	host.remove();
	drilldown.close();
});

async function draw(props: Record<string, unknown>): Promise<void> {
	drawn = mount(RecognitionPane, {
		target: host,
		props: {
			id: 'faces.scan',
			on: true,
			task: 'faces',
			consent: said('<p data-part="consent">switch</p>'),
			status: 'Ready.',
			...props
		}
	}) as Record<string, unknown>;
	flushSync();
	await tick();
	flushSync();
}

/** The parts of the page, in the order they are drawn. */
function order(): string[] {
	return [...host.querySelectorAll('[data-part], .status, [data-task]')].map(
		(one) =>
			one.getAttribute('data-part') ??
			(one.hasAttribute('data-task') ? 'when' : one.classList.contains('status') ? 'status' : '')
	);
}

it('reads switch, status, how thorough, when it runs, the pages, the list, and the way out last', async () => {
	await draw({
		on: true,
		work: said('<p data-part="work">downloading</p>'),
		setup: said('<p data-part="setup">the models</p>'),
		thorough: said('<p data-part="thorough">dial</p>'),
		rows: said('<p data-part="rows">more settings</p>'),
		always: said('<p data-part="always">list</p>'),
		danger: said('<p data-part="danger">delete</p>')
	});

	expect(order()).toEqual([
		'consent',
		'status',
		'work',
		'setup',
		'thorough',
		'when',
		'rows',
		'always',
		'danger'
	]);
});

it("draws the task's When row, by the task's own address, only while the feature is on", async () => {
	await draw({ on: true, task: 'smart-search' });
	expect(host.querySelector('[id="tasks.smart-search.when"]')).not.toBeNull();
	expect(host.textContent).toContain('When it runs');

	if (drawn) unmount(drawn);
	host.replaceChildren();
	// With a row of its own still offered, so the group is drawn and only the When row is withheld.
	await draw({
		on: false,
		task: 'smart-search',
		thorough: said('<p data-part="thorough">x</p>'),
		rows: said('<p data-part="rows">import people</p>')
	});
	expect(host.querySelector('[id="tasks.smart-search.when"]')).toBeNull();
	// How thorough is a question about a feature that is running; off, it is not asked.
	expect(order()).not.toContain('thorough');
});

it('keeps the way out last even with the feature off and nothing else to draw', async () => {
	await draw({
		on: false,
		rows: said('<p data-part="rows">import people</p>'),
		danger: said('<p data-part="danger">delete</p>')
	});

	expect(order()).toEqual(['consent', 'status', 'rows', 'danger']);
});

it('says nothing about where it stands before the route has answered', async () => {
	await draw({ on: true, status: null });

	expect(host.querySelector('.status')).toBeNull();
});

it('opens a page one level in when a deep link names a row on it', async () => {
	await draw({
		on: true,
		pages: [{ title: 'More settings', keys: ['faces.device'], body: said('<p>rows</p>') }]
	});

	expect(drilldown.reveal('faces.device')).toBe(true);
	expect(drilldown.title).toBe('More settings');
});

it('lets the claim go when the page is no longer offered', async () => {
	await draw({
		on: true,
		pages: [{ title: 'More settings', keys: ['faces.device'], body: said('<p>rows</p>') }]
	});
	if (drawn) unmount(drawn);
	drawn = null;

	expect(drilldown.reveal('faces.device')).toBe(false);
});

it("names the device by the menu's own label, so the line and the menu agree", () => {
	const entry = { choices: ['cpu', 'nvidia'], choice_labels: ['CPU', 'GPU'] };

	expect(deviceWords(entry, 'nvidia')).toBe('GPU');
	expect(deviceWords(entry, 'cpu')).toBe('CPU');
	// A device the declaration has never heard of is still named, never dropped.
	expect(deviceWords(entry, 'npu')).toBe('NPU');
});

it('puts the status and what is in flight in one shaded box under the switch', async () => {
	await draw({
		on: true,
		status: "On, but the models aren't downloaded yet.",
		statusCaution: true,
		work: said('<p data-part="work">a run stopped</p>'),
		setup: said('<p data-part="setup">the models</p>')
	});

	const note = host.querySelector('.recognition-note');
	expect(note?.querySelector('.status')?.textContent).toBe(
		"On, but the models aren't downloaded yet."
	);
	// One box: the warning is part of it, and the rows that set it up are not.
	expect(note?.querySelector('[data-part="work"]')).not.toBeNull();
	expect(note?.querySelector('[data-part="setup"]')).toBeNull();
	expect(note?.querySelector('.note')?.classList.contains('caution')).toBe(true);

	// Shaded, and in the rows' measure: the ground is what makes it read as the switch's answer.
	const { applyStyles, removeStyles } = await import('$lib/design/testing-styles');
	const { default: source } = await import('./RecognitionNote.svelte?raw');
	applyStyles(source, note);
	try {
		// The declarations that land on the box, read off the rules that match it: the unit
		// environment resolves no custom property, so the token is read as it is written.
		const landed = [...document.styleSheets]
			.flatMap((sheet) => [...sheet.cssRules])
			.filter((rule): rule is CSSStyleRule => rule instanceof CSSStyleRule)
			.filter((rule) => (note as HTMLElement).matches(rule.selectorText))
			.map((rule) => rule.style.getPropertyValue('background'))
			.join(' ');
		expect(landed).toContain('var(--sift-surface-2)');
		expect(getComputedStyle(note as HTMLElement).maxWidth).toBe('var(--reading-measure)');
	} finally {
		removeStyles();
	}
});
