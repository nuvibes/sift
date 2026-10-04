/* The head of the downloads screen, and what is not under it.
 *
 * No summary strip sits between the paste box and the list: every figure it would carry is said
 * somewhere closer: the counts by the state tabs and the rail, the speed by the running row,
 * the tunnel by that row's detail. Two things such a strip would carry that are said nowhere else
 * are held here: the queue's Pause, in the head beside the page's Options, and the one live region
 * that speaks the queue's milestones aloud. The doors and the paste's choices are the Options
 * menu's rows (`DownloadOptions.svelte.test.ts`).
 *
 * Read from the source for the reason `pointing.test.ts` gives: mounting this page is mounting
 * the frame, the cookies sheet and four requests to see a handful of lines.
 */
import { describe, expect, it } from 'vitest';
import { readFileSync } from 'node:fs';

// Read from the project root, which is where vitest runs.
const source = readFileSync('src/routes/downloads/+page.svelte', 'utf8');

/** The markup, with its comments and the style block taken out: a rule that matched its own
 *  explanation would pass for as long as the explanation mentioned the thing it forbids. */
const markup = source
	.slice(source.indexOf('</script>'), source.lastIndexOf('<style>'))
	.replace(/<!--[\s\S]*?-->/g, '');

/** The head's controls: what sits beside the title, and nothing else. */
const controls = (() => {
	const start = markup.indexOf('{#snippet controls()}');
	return start < 0 ? '' : markup.slice(start, markup.indexOf('</PageHeader>', start));
})();

describe('the head', () => {
	it('carries the queue pause beside the Options', () => {
		expect(controls).toContain("'Pause the queue'");
		expect(controls).toContain("'Resume the queue'");
		expect(controls).toContain('setPaused(!paused)');
		// Beside, not instead of: the Options holds the doors, the cookies one opening the sheet.
		expect(controls).toContain('<DownloadOptions');
		expect(controls).toContain('oncookies={() => openCookies(null)}');
	});

	/* The three doors and the two choices are rows behind one press, as on an entity page: none
	   of them is a control of its own on the page. */
	it('draws none of the five as a control of its own', () => {
		for (const said of ['Edit cookies', 'Open settings', 'Start or join a swap']) {
			expect(markup).not.toContain(said);
		}
		expect(markup).toContain('bind:open={optionsOpen}');
		expect(markup).toContain('onask={() => (optionsOpen = true)}');
		expect(markup).not.toMatch(/bind:remember\s+busy=/);
	});

	it('offers pause nowhere else on the screen', () => {
		expect(markup.split('Pause the queue').length - 1).toBe(1);
	});
});

describe('there is no summary strip', () => {
	it('draws no figures of the queue as a whole', () => {
		expect(markup).not.toMatch(/needs you/);
		expect(markup).not.toMatch(/MB\/s/);
		expect(markup).not.toMatch(/Tunnel:/);
		expect(markup).not.toMatch(/QueueStrip/);
	});

	/* With no strip, the milestones still need a voice. A progress bar is not announced as it
	   moves, so without this somebody listening hears nothing about the queue at all. */
	it('still says the milestones aloud, in exactly one live region', () => {
		expect(markup.split('aria-live=').length - 1).toBe(1);
		expect(markup).toMatch(/aria-live="polite">\{announced\}/);
		expect(source).toContain('downloading, ${queued} waiting');
	});
});

describe('the page actions', () => {
	/* One header-action look: the queue's verb is the Options door's kind of button (the shared
	   button at its own tone, as an entity page's Edit beside its Options), so neither is louder. */
	it('are all one kind of button', () => {
		const buttons = [...controls.matchAll(/<Button\b[^>]*>/g)].map((one) => one[0]);
		expect(buttons.length).toBe(1);
		for (const button of buttons) expect(button).not.toMatch(/tone=/);
		expect(controls).not.toMatch(/<Chip\b/);
		expect(controls).not.toMatch(/size="small"/);
	});
});

describe('the head on a phone', () => {
	/* Two presses, so one line under the title from its start: a two-column grid is for four. */
	const style = source.slice(source.lastIndexOf('<style>'));

	it('lays the Options and the queue verb on one line under the title', () => {
		expect(style).toMatch(/\.head-acts\.phone \{\s*display: flex;/);
		expect(style).not.toMatch(/grid-column/);
	});
});
