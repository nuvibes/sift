/* Where the downloads screen sends somebody: to one row, and from a finished download's message. */
import { describe, expect, it } from 'vitest';
import { readFileSync } from 'node:fs';

// Read from the project root, which is where vitest runs.
const source = readFileSync('src/routes/downloads/+page.svelte', 'utf8');

/** The script without its comments, so a rule cannot pass on its own explanation. */
const script = source
	.slice(0, source.indexOf('</script>'))
	.replace(/\/\*[\s\S]*?\*\//g, '')
	.replace(/\/\/[^\n]*/g, '');

describe('being sent here to look at one row', () => {
	it('honours the address that points at a row', () => {
		expect(script).toContain("page.url.searchParams.get('row')");
	});

	/* "This one", not "only this one". */
	it('puts back anything that would hide the row', () => {
		expect(script).toContain("queue.filter = 'all'");
		expect(script).toContain("queue.search = ''");
	});

	it('picks it, so the verbs for it are already in the bar', () => {
		expect(script).toContain('selection.toggle(id)');
	});

	/* Read again when the address changes, or a second press on a second pointer does nothing. */
	it('watches the address rather than reading it once', () => {
		const at = script.indexOf("searchParams.get('row')");
		const effect = script.lastIndexOf('$effect', at);
		expect(effect, 'the address is read outside an effect').toBeGreaterThan(-1);
		expect(script.slice(effect, at)).not.toContain('onMount');
	});
});

/* What a finished download SAYS is the toasts' module, said wherever somebody is. */
const said = readFileSync('src/lib/shell/toasts-downloads.svelte.ts', 'utf8')
	.replace(/\/\*[\s\S]*?\*\//g, '')
	.replace(/^\s*\/\/.*$/gm, '');

describe('what a finished download says', () => {
	it('offers the file it made, as the file piece a toast names a thing by', () => {
		expect(said).toContain("thing('asset'");
	});

	/* THE FILE OPENS OVER THE QUEUE, NOT BY A LINK: an anchor would tear down the queue behind
	   it. */
	it('opens it over the queue rather than navigating away from it', () => {
		expect(said).not.toContain('/asset/');
		const toaster = readFileSync('src/lib/components/common/Toaster.svelte', 'utf8');
		expect(toaster).toContain('<HistorySentence pieces={toast.pieces} />');
	});

	/* Several together have no single thing to open (`toasts-downloads.svelte.test.ts`). */
});
