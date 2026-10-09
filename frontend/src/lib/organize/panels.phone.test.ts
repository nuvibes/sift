/* The contact sheets, which a phone's width does not draw: the queues whose work is a whole
 * group laid side by side. */
import { describe, expect, it } from 'vitest';
import { readFileSync } from 'node:fs';
import { needsWiderWindow } from './panels';

describe('the contact sheets', () => {
	it('are Duplicates, Exact duplicates, Unnamed faces and Discarded, and their older names', () => {
		for (const queue of [
			'duplicates',
			'copies',
			'faces-to-name',
			'discarded-faces',
			'unidentified',
			'ignored',
			'to-check'
		]) {
			expect(needsWiderWindow(queue), queue).toBe(true);
		}
	});

	it('are not the queues that ask one question at a time', () => {
		for (const queue of [
			'faces',
			'disagreements',
			'known-people',
			'folders',
			'usernames',
			'tagger'
		]) {
			expect(needsWiderWindow(queue), queue).toBe(false);
		}
	});

	it('are refused on a phone by both the queue and one item of it', () => {
		for (const route of [
			'src/routes/organize/[queue]/+page.svelte',
			'src/routes/organize/[queue]/[id]/+page.svelte'
		]) {
			const source = readFileSync(route, 'utf8');
			expect(source, route).toMatch(/phoneWidth\.yes && needsWiderWindow\(name\)/);
			expect(source, route).toMatch(/title=\{WIDER_WINDOW\.title\}/);
		}
	});
});
