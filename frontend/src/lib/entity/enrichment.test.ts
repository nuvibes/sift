/* The line under the menu row, which is the whole of what somebody reads before deciding. */
import { describe, expect, it } from 'vitest';
import { KEPT_LOCAL_SAID, lastEnriched, refusedOutside } from '$lib/entity/enrichment.svelte';
import type { EnrichmentState } from '$lib/entity/enrichment.svelte';

function state(runs: EnrichmentState['runs']): EnrichmentState {
	return { subject: 'asset', id: 'a-1', kept_local: false, refused: false, why: '', runs };
}

describe('the last-enrichment line', () => {
	it('says nothing at all about something that has never been enriched', () => {
		expect(lastEnriched(state([]))).toBeNull();
		expect(lastEnriched(null)).toBeNull();
	});

	it('names the box beside the time', () => {
		const said = lastEnriched(
			state([{ box_id: 'b-1', box_name: 'StashDB', box_slug: 'stashdb', at: 0, automatic: true }])
		);
		expect(said).toContain('Last: ');
		expect(said).toContain('StashDB');
	});

	it('gives the time alone where the box has since been removed', () => {
		/* An id nobody recognises is worse than no name. The row still says a box did this. */
		const said = lastEnriched(
			state([{ box_id: 'b-1', box_name: null, box_slug: null, at: 0, automatic: false }])
		);
		expect(said).toContain('Last: ');
		expect(said).not.toContain('b-1');
	});

	it('reads the NEWEST run, which the server answers first', () => {
		const said = lastEnriched(
			state([
				{ box_id: 'b-2', box_name: 'FansDB', box_slug: 'fansdb', at: 200, automatic: false },
				{ box_id: 'b-1', box_name: 'StashDB', box_slug: 'stashdb', at: 100, automatic: true }
			])
		);
		expect(said).toContain('FansDB');
		expect(said).not.toContain('StashDB');
	});
});

/* WHETHER THE ENRICH ROWS ARE DRAWN REFUSED, which is a different question from the switch
 * itself. */
describe('what refuses a subject', () => {
	it('refuses nothing where nothing has been asked yet', () => {
		expect(refusedOutside(null)).toEqual({ refused: false, why: undefined });
	});

	it('carries the reason the server gave, so two surfaces cannot word it differently', () => {
		const said = refusedOutside({ ...state([]), refused: true, why: 'Kept local' });
		expect(said).toEqual({ refused: true, why: 'Kept local' });
	});

	it('draws no reason where the server sent none, rather than an empty line', () => {
		expect(refusedOutside({ ...state([]), refused: true, why: '' }).why).toBeUndefined();
	});

	it('is true of a file its own switch says nothing about', () => {
		/* The inheritance, from the menu's side: `kept_local` is off and it is refused anyway. */
		const inherited = { ...state([]), kept_local: false, refused: true, why: 'Kept local by ...' };
		expect(inherited.kept_local).toBe(false);
		expect(refusedOutside(inherited).refused).toBe(true);
	});

	it('says what to do about it, not only what happened', () => {
		/* A refusal that stops at "kept local" leaves somebody pressing the same button again,
		   so the message says plainly what to do. */
		expect(KEPT_LOCAL_SAID).toContain('not sent outside this device');
		expect(KEPT_LOCAL_SAID).toContain('Allow enrichment on it first');
	});
});
