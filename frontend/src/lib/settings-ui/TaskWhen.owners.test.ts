/* A task's When has two doors: the list on Tasks, and the pane that owns the thing the task does.
 *
 * Tasks draws every row by itself, from the list the server sends. The owning panes do not: each
 * writes its `<TaskWhen>` by hand, in the block about the thing the task works on, and a pane that
 * leaves it out simply has no When: nothing on the screen says a row is missing. So the four
 * owners of the timed clean-ups are held here: each draws its task's row inside the block a search
 * result names, and each declares that block in its copy module so the search can find it there.
 *
 * ## Why the source and not the rendered pane
 *
 * Maintenance and Privacy draw their blocks behind loads and an admin check, so a rendered test
 * sees only what today's mocks let through. What is asserted is what the pane DECLARES, which is
 * the same reason Activity's pointer test gives. Updates also has a rendered test of its own.
 */
import { describe, expect, it } from 'vitest';
import { readdirSync, readFileSync } from 'node:fs';

interface Owner {
	pane: string;
	task: string;
	/** The id of the block holding the row, which the search entry names. */
	block: string;
	section: string;
}

const OWNERS: Owner[] = [
	// Backup is not here: the backup task's row lives on Tasks and nowhere else, and the pane's own
	// line points at it, since two copies of one control are how two screens come to disagree.
	// Privacy and Updates are not here: the search-history clean-up and the update check run in the
	// background, and a task nobody needs to set is drawn on no pane.
	// Music: the fingerprint task's row is on the section that owns the feature.
	{ pane: 'Music', task: 'music', block: 'music.fingerprints', section: 'music' },
	// And the lookup task's row, beside the switch that says whether anything may be sent.
	{ pane: 'Music', task: 'music-lookup', block: 'music.acoustid-when', section: 'music' }
];

/** The pane's markup, comments out: a rule that matched its own explanation would always pass. */
function markupOf(pane: string): string {
	const source = readFileSync(`src/lib/settings-ui/${pane}.svelte`, 'utf8');
	return source.slice(source.indexOf('</script>')).replace(/<!--[\s\S]*?-->/g, '');
}

describe.each(OWNERS)('$pane', ({ pane, task, block, section }) => {
	it(`draws the ${task} task's row inside the block a search result names`, () => {
		const markup = markupOf(pane);
		const at = markup.indexOf(`id="${block}"`);
		expect(at, `no block with id "${block}"`).toBeGreaterThan(-1);
		const inside = markup.slice(at, markup.indexOf('</SettingGroup>', at));
		expect(inside, 'the row is not in the block').toContain(`<TaskWhen task="${task}"`);
		// Its words come from the pane's copy module, like every word the pane adds.
		expect(inside).toMatch(new RegExp(`<TaskWhen task="${task}" label=\\{COPY\\.`));
	});

	it('declares that block in its copy module, on its own section', () => {
		const declared = readFileSync(`src/lib/settings-ui/${pane}.search.ts`, 'utf8');
		const at = declared.indexOf(`key: '${block}',`);
		expect(at, `no search entry names "${block}"`).toBeGreaterThan(-1);
		const entry = declared.slice(at, declared.indexOf('}', at));
		expect(entry).toContain(`section: '${section}',`);
	});
});

/* Upkeep nobody times (the two prunes and the update check) runs on its default When and shows in
 * Activity; the server leaves it out of the task list, and no pane may draw a row for it. Read from
 * the source for the reason above: a row for a task the list leaves out draws as an empty label. */
const UNSHOWN = ['quarantine-prune', 'search-records-prune', 'update-check'];

it('draws no row for upkeep nobody times, on any pane', () => {
	const panes = readdirSync('src/lib/settings-ui').filter((name) => name.endsWith('.svelte'));
	const drawn = panes.flatMap((pane) =>
		UNSHOWN.filter((task) =>
			markupOf(pane.replace(/\.svelte$/, '')).includes(`task="${task}"`)
		).map((task) => `${pane}: ${task}`)
	);
	expect(drawn).toEqual([]);
});

/* Presses live on Tasks. Every other pane that draws a task's row draws it without the press, so a
 * task is started from one place and the owning pane keeps the choice and the facts. Read from the
 * source, every `<TaskWhen>` a pane writes, because a press drawn on a pane nobody opened in a test
 * is a press all the same. Import tasks (`Importing`) is part of Tasks, mounted by it. */
const ON_TASKS = ['ScheduledTasks.svelte', 'Importing.svelte'];

it('draws a press on no pane but Tasks', () => {
	expect(markupOf('ScheduledTasks')).toMatch(/<Importing\b/);
	const panes = readdirSync('src/lib/settings-ui').filter(
		(name) => name.endsWith('.svelte') && !ON_TASKS.includes(name)
	);
	const rows = panes.flatMap((pane) =>
		(markupOf(pane.replace(/\.svelte$/, '')).match(/<TaskWhen\b[\s\S]*?\/>/g) ?? []).map((tag) => ({
			pane,
			tag
		}))
	);
	// The guard reads something: the owning panes draw rows today (Music's two, at least).
	expect(rows.length).toBeGreaterThan(1);
	expect(rows.filter(({ tag }) => !/\bpress=\{false\}/.test(tag)).map(({ pane }) => pane)).toEqual(
		[]
	);
});
