/* Two rules the downloads screen holds that are about where somebody is sent, not about what is
 * drawn: being pointed AT one row, and the door on the message that says a download finished.
 *
 * ## Why the source and not the rendered screen
 *
 * The same reason the Activity screen's own tests give. Mounting this page is mounting the frame,
 * the rail's neighbours, the settings reader, the cookies sheet and four requests, and what is
 * asserted here is not what any of that draws. It is which door the screen offers and which it
 * refuses to offer. A rendered test would need a queue, a landed download and a clipboard to see
 * the same two lines, and would still be reading them through a mock of everything around them.
 */
import { describe, expect, it } from 'vitest';
import { readFileSync } from 'node:fs';

// Read from the project root, which is where vitest runs.
const source = readFileSync('src/routes/downloads/+page.svelte', 'utf8');

/** The script, with its comments taken out: a rule that matched its own explanation would pass for
 *  as long as the explanation mentioned the thing it forbids. */
const script = source
	.slice(0, source.indexOf('</script>'))
	.replace(/\/\*[\s\S]*?\*\//g, '')
	.replace(/\/\/[^\n]*/g, '');

describe('being sent here to look at one row', () => {
	it('honours the address that points at a row', () => {
		expect(script).toContain("page.url.searchParams.get('row')");
	});

	/* "This one", not "only this one". The chip goes back to All and the search is cleared, or the
	   row somebody was sent to look at is filtered out of the list they were sent to and the screen
	   appears to have ignored the link. */
	it('puts back anything that would hide the row', () => {
		expect(script).toContain("queue.filter = 'all'");
		expect(script).toContain("queue.search = ''");
	});

	it('picks it, so the verbs for it are already in the bar', () => {
		expect(script).toContain('selection.toggle(id)');
	});

	/* Read again when the address changes, not once when the screen opens: a second press on a
	   second pointer would otherwise do nothing at all, which reads as a broken link. */
	it('watches the address rather than reading it once', () => {
		const at = script.indexOf("searchParams.get('row')");
		const effect = script.lastIndexOf('$effect', at);
		expect(effect, 'the address is read outside an effect').toBeGreaterThan(-1);
		expect(script.slice(effect, at)).not.toContain('onMount');
	});
});

/* What a finished download SAYS is the toasts' own module rather than this screen's (a download
   finishing is said wherever somebody is, not only on Downloads), so these
   read that file: reading the page for them would pass on nothing. */
const said = readFileSync('src/lib/shell/toasts-downloads.svelte.ts', 'utf8')
	.replace(/\/\*[\s\S]*?\*\//g, '')
	.replace(/^\s*\/\/.*$/gm, '');

describe('what a finished download says', () => {
	it('offers the file it made, as the file piece a toast names a thing by', () => {
		expect(said).toContain("thing('asset'");
	});

	/* THE FILE OPENS OVER THE QUEUE, NOT BY A LINK, and that is the modal-only rule rather than a
	   preference: an anchor to /asset/<id> would tear down the queue behind it and take the pick,
	   the chip and the search with it. The toaster draws a file piece the way a History line does,
	   and that drawing is what opens a file over the page. */
	it('opens it over the queue rather than navigating away from it', () => {
		expect(said).not.toContain('/asset/');
		const toaster = readFileSync('src/lib/components/common/Toaster.svelte', 'utf8');
		expect(toaster).toContain('<HistorySentence pieces={toast.pieces} />');
	});

	/* Several together have no single thing to open: `toasts-downloads.svelte.test.ts` holds that
	   by what is said ("4 downloads finished", and no file to open), which a search of the source
	   could not. */
});
