/*
 * Where one group's control sits.
 *
 * The control whose menu reads "All N on this page", "Nothing is picked", "All N in this group"
 * belongs on the group's own row, beside the count and directly above the faces it acts on, not at
 * the far end of the tabs' row a window away from them.
 *
 * Read from the source: what is pinned is which band a control is drawn in, and jsdom lays nothing
 * out, so a rendered test would find the same button in both arrangements. The header is a shared
 * component (`OrganizeHeader`) whose `controls` snippet is the one place this could go back to, so
 * naming it names the way back.
 */
import { readFileSync } from 'node:fs';

import { describe, expect, it } from 'vitest';

const source = readFileSync('src/lib/components/faces/PileDetail.svelte', 'utf8');

describe("the group's one control sits with the faces", () => {
	it('is on the row that carries the count', () => {
		const bar = source.slice(
			source.indexOf('<div class="bar">'),
			source.indexOf('<ul class="faces"')
		);
		expect(bar, 'the control is not on the group\u2019s own row').toContain('<SplitButton');
		expect(bar).toContain('Add as person');
	});

	it('is not in the band the tabs are in', () => {
		// `OrganizeHeader` draws the trail and the tabs; anything in its `controls` lands at the
		// far end of that row. This screen hands it nothing.
		//
		// The queue is `opened` rather than the literal `faces-to-name`: one screen draws a group
		// whichever tab it was reached through, so the lit tab and the trail follow the tab it was
		// opened from.
		expect(source).toContain('<OrganizeHeader queue={opened} here="A group of faces">');
		expect(source, 'a control is back in the header band').not.toContain('{#snippet controls()}');
	});
});

describe('the way back is the tab it was opened from', () => {
	/*
	 * A group pressed on Ignored must be able to get back to Ignored. The origin travels in the
	 * address, because it has to survive a refresh, a shared link and the browser's own Back.
	 */
	it('reads the origin off the address and hands it to the trail', () => {
		expect(source).toContain("address.url.searchParams.get('via')");
		expect(source).toContain('tabOpenedFrom(');
	});
});

describe('a discarded group has its own way back out', () => {
	/* With only the count on the row, the only way to bring a discarded group back from its own
	   page would be to pick or right-click a face, while the wall's card has the button. */
	it('draws Restore on the group row when the group is discarded', () => {
		const bar = source.slice(
			source.indexOf('<div class="bar">'),
			source.indexOf('<ul class="faces"')
		);
		const discarded = bar.slice(bar.indexOf("{:else if status === 'ignored'}"));
		expect(bar, 'no branch for a discarded group').toContain("{:else if status === 'ignored'}");
		expect(discarded).toContain('onclick={() => void bringBack()}');
		expect(discarded).toContain('>Restore</Button>');
	});
});
