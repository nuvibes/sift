/* Two things the Activity screen does not do for itself.
 *
 * **Its state filter is the row every entity page draws**, the row of words Downloads draws too,
 * here in its value mode: the pane is inside the Settings sheet, where a link would navigate and
 * tear the page behind it down. One strip in the app filters a list by its states.
 *
 * **A download's row is a pointer.** Everything a download needs (its Site, its address, the
 * cookies it is waiting for, the sentence saying why it stopped) is on the Downloads screen, and
 * none of it can be on a row in a queue of every kind of work. So the name is the way there.
 *
 * ## Why the source and not the rendered screen
 *
 * The same reason the header's own test gives: what is asserted is what the screen DECLARES, and a
 * rendered test can only see what today's counts happen to draw: on an idle library there are no
 * state chips and no download row at all, so it would pass against a screen that had quietly gone
 * back to drawing its own.
 */
import { describe, expect, it } from 'vitest';
import { readdirSync, readFileSync } from 'node:fs';
import { join } from 'node:path';

// Read from the project root, which is where vitest runs, the same way the header's test reads it.
const source = readFileSync('src/lib/jobs/JobsScreen.svelte', 'utf8');

/** The markup, with its comments taken out: a rule that matched its own explanation would pass for
 *  as long as the explanation mentioned the thing it forbids. */
const markup = source.slice(source.indexOf('</script>')).replace(/<!--[\s\S]*?-->/g, '');

describe('the state filter', () => {
	it('is the row every entity page draws, narrowing in place', () => {
		expect(markup, 'the row is not drawn').toMatch(/<Tabs\b/);
		// Filtering in place: told which was pressed, never sent to an address. A link here would
		// leave the Settings sheet and tear down the page it was opened over.
		const at = markup.search(/<Tabs\b/);
		const row = markup.slice(at, markup.indexOf('/>', at));
		expect(row).toContain('onselect=');
		expect(row).not.toMatch(/\bhref\b/);
	});

	/* The tab being looked at stays in the row once it empties. Left out, it would vanish the moment
	   its last task finished and the row would light nothing while the list stayed filtered to it. */
	it('keeps the tab being looked at even when nothing is left in it', () => {
		expect(source).toContain('(shownCounts[state] ?? 0) > 0 || state === queue.filter');
	});

	it('draws no chips of its own beside it', () => {
		expect(markup, 'a hand-rolled state chip came back').not.toMatch(/<Chip\b/);
	});

	/* The words are the BADGE's, imported rather than retyped. The chips filter the list to a state
	   and the badges in the rows say which state each row is in, so a chip with a word of its own
	   would be one state called two things on one screen. */
	it('takes its words from the badge that says the same states', () => {
		expect(source).toMatch(/import \{ LABELS.*from '\$lib\/components\/common\/Badge\.svelte'/);
		expect(markup).toContain('LABELS[state as BadgeState]');
	});

	/* Blocked work is waiting for a person rather than for a worker, and nothing else on this screen
	   says so. */
	it('marks the pile that is waiting for somebody', () => {
		expect(markup).toContain("attention: state === 'blocked' ? BLOCKED_WAITS : undefined");
	});

	/* One strip for filtering a list by its states, and this is the guard that there is only one:
	   no component, import or gallery section may carry the name of a separate strip of state
	   chips. The name is spelt in two halves so this file
	   is not itself a mention of it. */
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
		// The download case is asked FIRST, before the finished-job case that links to the file:
		// a download that failed or is waiting for cookies is exactly the one somebody wants to
		// follow, and it has no file to open.
		const download = markup.indexOf("job.type === 'download'");
		const finished = markup.indexOf("state === 'done' && fileOf(job)");
		expect(download, 'the download row is not a pointer at all').toBeGreaterThan(-1);
		expect(download).toBeLessThan(finished);
	});

	/* The two verbs every row here offers are unchanged, and the pointer must not have quietly
	   taken them away: Cancel and Run again are what this screen is for. */
	it('keeps the two verbs it already had', () => {
		// Run again reads the row's own state; Cancel reads what the row shows: a folded
		// download that is done with a step running is worth cancelling, and the cancel walks the
		// family on the server.
		expect(markup).toContain('canRetry(job.state)');
		expect(markup).toContain('canCancel(state)');
		expect(markup).toContain('{@const state = shownState(job)}');
	});
});

/* Every piece of work has ONE row on Tasks, where when it runs is chosen and Run now lives. A
   bar here that started the work itself would be one more door to the same thing. */
describe("a pass's row", () => {
	/** The one list over the passes and the housekeeping. */
	const summary = markup.slice(
		markup.indexOf('items={lines}'),
		markup.indexOf('</DataRows>', markup.indexOf('items={lines}'))
	);
	// The row's own snippet: Run in Tasks, at the row's end on a desktop and the card's end on a phone.
	const acts = summary.slice(
		summary.indexOf('{#snippet runInTasks()}'),
		summary.indexOf('{/snippet}', summary.indexOf('{#snippet runInTasks()}'))
	);

	it("links to its task's row on Tasks instead of starting the work", () => {
		expect(summary, 'the list was not found').toContain('<DataRow');
		expect(acts, 'a row lost its way to Tasks').toContain(
			'<SettingLink section="tasks" setting={taskRow(one)}'
		);
		// The known positive's other half: nothing in the rows presses a route of its own.
		expect(summary).not.toMatch(/api\.post|runPass|runChore/);
	});

	/* A pass that is more than one task (Identify, Fingerprint) has no row of its own and still has
	   the link, to the top of Tasks. A chore has one only when it is a task. */
	it('draws every pass its link, and a chore its link when the chore is a task', () => {
		expect(acts).toContain("{#if line.kind === 'pass' || one.task}");
		expect(source, 'a start address came back').not.toMatch(/runNow|runsFromSettings|run_now/);
	});
});

/* ONE COLUMN DECLARATION FOR THE TAB. Two hand-built grids sizing their own columns would put
   the status column at a different x in Passes and in Housekeeping. */
describe("the Now tab's columns", () => {
	const style = source.slice(source.indexOf('<style>'));

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
