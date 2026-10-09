/* A press on a settings pane sits in a row's control column, on the right, with the row's name
 * and its sentence on the left: never a button at the pane's left edge under a paragraph. */
import { readFileSync } from 'node:fs';
import { describe, expect, it } from 'vitest';

/** The pane's markup, comments out, so a note about the rule is not read as the rule. */
function markupOf(pane: string): string {
	const source = readFileSync(`src/lib/settings-ui/${pane}.svelte`, 'utf8');
	return source.slice(source.lastIndexOf('</script>')).replace(/<!--[\s\S]*?-->/g, '');
}

/** Whether the text at `at` is inside a row: an `ActionRow` tag not yet closed, or a
 *  `LabelledRow` whose control column it is in. */
function insideARow(markup: string, at: number): boolean {
	const action = markup.lastIndexOf('<ActionRow', at);
	if (action > -1 && !markup.slice(action, at).includes('/>')) return true;
	const labelled = markup.lastIndexOf('<LabelledRow', at);
	return labelled > -1 && !markup.slice(labelled, at).includes('</LabelledRow>');
}

const PRESSES: { pane: string; press: string }[] = [
	{ pane: 'Sites', press: 'action={COPY.cookies.edit}' },
	{ pane: 'Profile', press: 'action={SIGN_OUT.action}' },
	{ pane: 'Appearance', press: 'action="Reset"' },
	{ pane: 'Performance', press: '? COPY.measure.again : COPY.measure.run}' },
	{ pane: 'DatabaseSwitcher', press: '>{COPY.create}</Button' },
	{ pane: 'DatabaseSwitcher', press: 'action={DUPLICATE.begin}' },
	{ pane: 'DatabaseSwitcher', press: 'action={COPY.chooseFile}' },
	{ pane: 'DatabaseSwitcher', press: '{COPY.importChoose}' }
];

describe.each(PRESSES)('$pane', ({ pane, press }) => {
	it(`draws ${press} in a row, with its press on the right`, () => {
		const markup = markupOf(pane);
		const at = markup.indexOf(press);
		expect(at, `${press} is not on ${pane}`).toBeGreaterThan(-1);
		expect(insideARow(markup, at)).toBe(true);
	});
});

it("draws a stash-box card's verbs through the shared row, never in a head of its own", () => {
	/* The card is a `DataRow`: the verbs come from one declared list, opened from the three-dot
	   menu and from a right-click, with the switch at the row's end. */
	const markup = markupOf('StashBoxes');
	const row = markup.indexOf('<DataRow verbs={boxVerbs(box)}');
	const marks = markup.indexOf('<div class="marks">');
	expect(row).toBeGreaterThan(-1);
	expect(row).toBeLessThan(marks);
	expect(markup.includes('<div class="head">')).toBe(false);
	expect(markup.includes('<div class="verbs">')).toBe(false);
});

/* ONE PRESS HEIGHT: a press inside a settings row or a pane's form names no size; the row
 * decides it (see `press-size`). */
const PANE_FILES = import.meta.glob(['./*.svelte', '!./*.test.svelte'], {
	query: '?raw',
	import: 'default',
	eager: true
});

/** Every `LabelledRow` and `FormCard` element in a pane's markup, whole, with its opening tag. */
function holders(markup: string): { open: string; body: string }[] {
	const found: { open: string; body: string }[] = [];
	const tag = /<(\/?)(LabelledRow|FormCard)\b/g;
	const stack: number[] = [];
	for (let match = tag.exec(markup); match; match = tag.exec(markup)) {
		if (match[1] === '') {
			const end = markup.indexOf('>', match.index);
			if (markup[end - 1] === '/') continue;
			stack.push(match.index);
		} else {
			const start = stack.pop();
			if (start === undefined) continue;
			const body = markup.slice(start, match.index);
			found.push({ open: body.slice(0, body.search(/[^=]>/) + 2), body });
		}
	}
	return found;
}

/** A press's opening tag; an arrow function inside an attribute does not end it. */
const PRESS = /<(Button|ChooseFile|SplitButton)\b((?:=>|[^>])*?)\/?>/g;

it('leaves the size of every press in a row or a form to the row', () => {
	const named: string[] = [];
	for (const [file, source] of Object.entries(PANE_FILES)) {
		const markup = (source as string)
			.slice((source as string).lastIndexOf('</script>'))
			.replace(/<!--[\s\S]*?-->/g, '');
		for (const { body } of holders(markup)) {
			for (const press of body.matchAll(PRESS)) {
				if (/\ssize=/.test(press[2])) named.push(`${file}: ${press[0].slice(0, 60)}`);
			}
		}
	}
	expect(named).toEqual([]);
});

it('marks every row holding a field beside a press, so the press takes the field height', () => {
	const unmarked: string[] = [];
	for (const [file, source] of Object.entries(PANE_FILES)) {
		const markup = (source as string)
			.slice((source as string).lastIndexOf('</script>'))
			.replace(/<!--[\s\S]*?-->/g, '');
		for (const { open, body } of holders(markup)) {
			if (!open.startsWith('<LabelledRow')) continue;
			const field = /<(Select|TextInput|NumberInput)\b/.test(body);
			const press = [...body.matchAll(PRESS)].some(
				(one) => !/type="submit"|tone="(link|quiet)"/.test(one[2])
			);
			if (field && press && !/\bbesideField\b/.test(open)) unmarked.push(`${file}: ${open}`);
		}
	}
	expect(unmarked).toEqual([]);
});

it("gives a stage's Edit beside its When the choice's height", () => {
	const markup = markupOf('TaskWhen');
	expect(markup).toMatch(/<LabelledRow[^>]*\bbesideField\b/);
	expect(markup).toContain('{@render beside()}');
});
