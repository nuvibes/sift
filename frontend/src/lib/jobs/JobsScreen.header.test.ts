/* The bulk actions live behind one door at the end of the pile strip, which never grows a row of
 * buttons. */
import { describe, expect, it } from 'vitest';
import { readFileSync } from 'node:fs';

// Read from the project root, which is where vitest runs, the same way the guest rows are read.
const source = readFileSync('src/lib/jobs/JobsScreen.svelte', 'utf8');

/** The pile strip (the tabs and the door at its end), with its comments taken out: a rule that matched its own explanation would pass
 *  for as long as the explanation mentioned the thing it forbids. */
const header = source
	.slice(source.indexOf('<div class="filters">'), source.indexOf('<div id={listId}'))
	.replace(/<!--[\s\S]*?-->/g, '');

describe('the Jobs header', () => {
	it('offers its bulk actions through the shared menu', () => {
		expect(header, 'the door is not drawn').toContain('<MenuButton');
		// The known positive: without this the rule below is satisfied by a header that lost the
		// actions altogether, which is the other way to have no buttons in it.
		expect(header).toContain('<ContextMenuItem');
	});

	it('lays no action out as a button beside the heading', () => {
		expect(header, 'a bulk action went back to being a button in the header').not.toMatch(
			/<Button\b/
		);
	});

	it('always has something behind it', () => {
		/* The door is always drawn, because it always holds the density row: a menu button that
		   opens on nothing reads as a screen refusing its own actions, with no way to tell that
		   from a menu that failed to open. */
		const rows = header.slice(header.indexOf('<MenuButton'));
		const density = rows.indexOf('label="Compact rows"');
		expect(density, 'the row that is always offered went missing').toBeGreaterThan(-1);
		const conditional = rows.lastIndexOf('{#if', density);
		const closed = rows.lastIndexOf('{/if}', density);
		expect(closed, 'the always-offered row was put inside a condition').toBeGreaterThan(
			conditional
		);
	});
});
