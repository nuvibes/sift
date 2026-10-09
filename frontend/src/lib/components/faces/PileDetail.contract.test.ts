/*
 * The group's control belongs on the group's row, above its faces, not at the end of the tabs' row.
 * Read from the source: jsdom lays nothing out.
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
		// `OrganizeHeader` is handed nothing; the queue is `opened`, the tab it was reached
		// through.
		expect(source).toContain('<OrganizeHeader queue={opened} here="A group of faces">');
		expect(source, 'a control is back in the header band').not.toContain('{#snippet controls()}');
	});
});

describe('the way back is the tab it was opened from', () => {
	/* The origin travels in the address, to survive a refresh and Back. */
	it('reads the origin off the address and hands it to the trail', () => {
		expect(source).toContain("address.url.searchParams.get('via')");
		expect(source).toContain('tabOpenedFrom(');
	});
});

describe('a discarded group has its own way back out', () => {
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
