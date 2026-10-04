/*
 * The bulk actions live behind one door at the end of the pile strip, which never grows a row of
 * buttons.
 *
 * ## The fault
 *
 * Every bulk action names its pile and its number ("Clear the 120,000 canceled"), which is
 * deliberate: a button reading "Clear them" clears a pile the reader may not be able to see. The
 * cost is that the labels grow with the queue, and laid across a header row they run past the edge
 * of the page on a large library, the last one cut off: a control that exists and cannot be
 * pressed.
 *
 * A menu is a column, so a long label is only long. Every one of them is in `MenuButton` (the
 * same door a file's own screen and every entity page wears), and this holds them there.
 *
 * ## Why the source and not the rendered screen
 *
 * Because what is asserted is where an action is DECLARED, and a rendered test can only see what
 * today's counts happen to draw: on an idle library none of these rows exists at all, so a screen
 * test would pass against a header that had quietly grown a button for a case it did not set up.
 * The same reasoning, and the same idiom, as the guest rows in Settings.
 */
import { describe, expect, it } from 'vitest';
import { readFileSync } from 'node:fs';

// Read from the project root, which is where vitest runs, the same way the guest rows are read.
// `import.meta.url` is not a file URL under the transform, so it cannot be resolved from here.
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
		/* The door is always drawn, because it always holds the density row: a menu button
		   that opens on nothing reads as a screen refusing its own actions, with no way to tell
		   that from a menu that failed to open. So the guard is that the density row is not
		   inside any `{#if}`: it is the one row that must always be there, and it is what makes
		   the door safe to draw unconditionally. */
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
