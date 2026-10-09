/* Two things the Activity screen does not do for itself. */
import { describe, expect, it } from 'vitest';
import { readdirSync, readFileSync } from 'node:fs';
import { join } from 'node:path';

// Read from the project root, which is where vitest runs, the same way the header's test reads it.
const files = ['JobsScreen.svelte', 'ActivitySummary.svelte'].map((name) =>
	readFileSync(`src/lib/jobs/${name}`, 'utf8')
);
const source = files.join('\n');

/** The markup, with its comments taken out: a rule that matched its own explanation would pass for
 *  as long as the explanation mentioned the thing it forbids. */
const markup = files
	.map((one) => one.slice(one.indexOf('</script>')))
	.join('\n')
	.replace(/<!--[\s\S]*?-->/g, '');

describe('the state filter', () => {
	it('is the row every entity page draws, narrowing in place', () => {
		expect(markup, 'the row is not drawn').toMatch(/<Tabs\b/);
		// Filtering in place: told which was pressed, never sent to an address.
		const at = markup.search(/<Tabs\b/);
		const row = markup.slice(at, markup.indexOf('/>', at));
		expect(row).toContain('onselect=');
		expect(row).not.toMatch(/\bhref\b/);
	});

	/* The tab being looked at stays in the row once it empties. */
	it('keeps the tab being looked at even when nothing is left in it', () => {
		expect(source).toContain('(shownCounts[state] ?? 0) > 0 || state === queue.filter');
	});

	it('draws no chips of its own beside it', () => {
		expect(markup, 'a hand-rolled state chip came back').not.toMatch(/<Chip\b/);
	});

	/* The words are the BADGE's, imported rather than retyped. */
	it('takes its words from the badge that says the same states', () => {
		expect(source).toMatch(/import \{ LABELS.*from '\$lib\/components\/common\/Badge\.svelte'/);
		expect(markup).toContain('LABELS[state as BadgeState]');
	});

	/* Blocked work is waiting for a person rather than for a worker, and nothing else on this
	   screen says so. */
	it('marks the pile that is waiting for somebody', () => {
		expect(markup).toContain("attention: state === 'blocked' ? BLOCKED_WAITS : undefined");
	});

	/* One strip for filtering a list by its states, and this is the guard that there is only
	   one: no component, import or gallery section may carry the name of a separate strip of
	   state chips. */
	it('has no second strip beside it anywhere in the client', () => {
		const retired = 'State' + 'Chips';
		const mentions: string[] = [];
		let read = 0;
		const walk = (dir: string) => {
			for (const entry of readdirSync(dir, { withFileTypes: true })) {
				const full = join(dir, entry.name);
				if (entry.isDirectory()) walk(full);
				else if (/\.(svelte|ts|js)$/.test(entry.name)) {
					read += 1;
					if (readFileSync(full, 'utf8').includes(retired)) mentions.push(full);
				}
			}
		};
		walk('src');
		walk('e2e');
		// The known positive: the walk read the tree at all, or silence would pass for an empty one.
		expect(read).toBeGreaterThan(500);
		expect(mentions).toEqual([]);
	});
});

describe("a download's row", () => {
	it('leads to the screen that knows about downloads', () => {
		// The row's own way in: the job's id, which the Downloads screen resolves to its download.
		expect(markup).toContain('href="/downloads?job={job.id}"');
	});

	it('does so for a download in any state, not only a finished one', () => {
		// The download case is asked FIRST, before the finished-job case that links to the file: a
		// download that failed or is waiting for cookies is exactly the one somebody wants to
		// follow, and it has no file to open.
		const download = markup.indexOf("job.type === 'download'");
		const finished = markup.indexOf("state === 'done' && fileOf(job)");
		expect(download, 'the download row is not a pointer at all').toBeGreaterThan(-1);
		expect(download).toBeLessThan(finished);
	});

	/* The two verbs every row here offers are unchanged, and the pointer must not have quietly
	   taken them away: Cancel and Run again are what this screen is for. */
	it('keeps the two verbs it already had', () => {
		// Run again reads the row's own state; Cancel reads what the row shows: a folded download
		// that is done with a step running is worth cancelling, and the cancel walks the family on
		// the server.
		expect(markup).toContain('canRetry(job.state)');
		expect(markup).toContain('canCancel(state)');
		expect(markup).toContain('{@const state = shownState(job)}');
	});
});

/* Run now on a pass is the task's OWN Run now (`pressTask`): one press and one sentence after it,
   wherever it is drawn, so a bar here and the task's row on Tasks cannot come to do two things. */
describe("a pass's row", () => {
	/** The one list over the passes and the housekeeping. */
	const summary = markup.slice(
		markup.indexOf('items={lines}'),
		markup.indexOf('</DataRows>', markup.indexOf('items={lines}'))
	);
	// The row's own snippet: at the row's end on a desktop and the card's end on a phone.
	const acts = summary.slice(
		summary.indexOf('{#snippet lineActions()}'),
		summary.indexOf('{/snippet}', summary.indexOf('{#snippet lineActions()}'))
	);

	it("presses its task's own Run now, and pauses and cancels the pass", () => {
		expect(summary, 'the list was not found').toContain('<DataRow');
		expect(acts).toContain('{COPY.runNow}');
		expect(acts).toContain('runNow(one)');
		expect(acts).toContain('holdAndCancel(');
		expect(source).toContain("await pressTasks(pressesOf(one), 'now')");
		// The known positive's other half: nothing in the rows presses a route of its own.
		expect(summary).not.toMatch(/api\.post/);
	});

	it('offers Run now only where there is a task to press, and a sub-task its own pause', () => {
		expect(acts).toContain('{#if pressesOf(one).length > 0}');
		expect(summary).toContain('holdAndCancel({ type: line.part.type }');
	});
});

/* ONE COLUMN DECLARATION FOR THE TAB. Two hand-built grids sizing their own columns would put
   the status column at a different x in Passes and in Housekeeping. */
describe("the Now tab's columns", () => {
	const style = files.map((one) => one.slice(one.indexOf('<style>'))).join('\n');

	it('are declared once and read by every list on the tab', () => {
		const declared = [...markup.matchAll(/columns=\{([^}]+)\}/g)].map((one) => one[1]);
		expect(declared, 'the lists were not found').toHaveLength(2);
		// One choice, read by both lists: the desktop's columns, or the phone's one card column.
		expect(new Set(declared)).toEqual(
			new Set(['phoneWidth.yes ? ACTIVITY_CARD : ACTIVITY_COLUMNS'])
		);
		const family = readFileSync('src/lib/jobs/family.ts', 'utf8');
		expect(family.match(/: readonly Column\[\] =/g)).toHaveLength(2);
		expect(family).toContain('export const ACTIVITY_COLUMNS: readonly Column[] =');
		expect(family).toContain('export const ACTIVITY_CARD: readonly Column[] =');
	});

	it("are never a grid of the screen's own", () => {
		expect(style, 'a hand-built grid came back').not.toMatch(/grid-template-columns/);
		expect(style).not.toMatch(/display:\s*grid/);
	});

	it('open a family onto the same tracks, declaring none of its own', () => {
		// The steps under an opened row inherit the list's tracks, so a step's badge stands under
		// the family's badge.
		const label = markup.indexOf('label="Steps of');
		const tag = markup.slice(markup.lastIndexOf('<DataRows', label), markup.indexOf('}">', label));
		expect(tag, 'the steps list was not found').toContain('<DataRows');
		expect(tag).not.toContain('columns=');
	});
});
