/*
 * The labelled facts on an entity's record, beside the name: two columns declared once, so every
 * label starts one column and every value the next, whatever length each is.
 *
 * And only the labelled ones. Other names and links are drawn under the name by their shape; one
 * appearing here as well would be the same fact twice on one header.
 */
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { flushSync, mount, unmount } from 'svelte';
import type { FieldDescription } from '$lib/entity/records.svelte';

const mocks = vi.hoisted(() => ({ all: [] as FieldDescription[] }));

/* A real registry with its one request replaced, as the summary's own test does. */
vi.mock('$lib/entity/records.svelte', async (importOriginal) => {
	const real = await importOriginal<typeof import('$lib/entity/records.svelte')>();
	class Standing extends real.Fields {
		override of(): FieldDescription[] {
			return mocks.all;
		}
	}
	return { ...real, fields: new Standing() };
});

import RecordFacts from './RecordFacts.svelte';

function field(over: Partial<FieldDescription>): FieldDescription {
	return {
		key: 'aliases',
		subject: 'person',
		label: 'Aliases',
		kind: 'names',
		shown: 'record',
		group: 'record',
		editable: true,
		links_to: null,
		help: null,
		imported: false,
		suggests: null,
		entry: null,
		ordered: false,
		...over
	};
}

let host: HTMLElement;
let drawn: Record<string, unknown> | null = null;

beforeEach(() => {
	mocks.all = [
		field({ key: 'aliases', label: 'Aliases', kind: 'names' }),
		field({ key: 'links', label: 'Links', kind: 'links' }),
		field({ key: 'nationality', label: 'Nationality', kind: 'text' }),
		field({ key: 'hair_color', label: 'Hair color', kind: 'text' }),
		field({ key: 'career', label: 'Career', kind: 'text' }),
		field({ key: 'notes', label: 'Notes', kind: 'text', shown: 'more' })
	];
});

afterEach(() => {
	if (drawn) unmount(drawn);
	drawn = null;
	host?.remove();
});

function draw(values: Record<string, unknown>): HTMLElement {
	host = document.createElement('div');
	document.body.append(host);
	drawn = mount(RecordFacts, {
		target: host,
		props: { subject: 'person', values, label: 'Facts about Ada Byron' }
	}) as Record<string, unknown>;
	flushSync();
	return host;
}

it('lays the labelled facts on two declared columns, a label and a value to each row', () => {
	draw({
		aliases: ['Ada'],
		links: ['https://example.com/ada'],
		nationality: 'Wales',
		hair_color: 'Brunette',
		career: '',
		notes: 'Kept behind More'
	});
	const list = host.querySelector('ul.rows');
	expect(list?.classList.contains('columned')).toBe(true);
	expect(list?.getAttribute('aria-label')).toBe('Facts about Ada Byron');
	const rows = [...host.querySelectorAll('li')].filter((one) => !one.classList.contains('heads'));
	// Only the labelled facts with something in them: no names, no links, no empty career, and
	// nothing the panel keeps behind More.
	expect(rows.map((one) => one.querySelector('.label')?.textContent)).toEqual([
		'Nationality',
		'Hair color'
	]);
	expect(rows[0].textContent).toContain('Wales');
});

it('draws nothing at all where no labelled fact has a value', () => {
	draw({ aliases: ['Ada'] });
	expect(host.querySelector('ul')).toBeNull();
});
