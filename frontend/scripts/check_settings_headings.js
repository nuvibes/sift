// A heading on a settings screen is drawn by one of two components, never by hand.
//
// ## The failure this is for
//
// Panes that each draw their own headings drift: several sizes under one section's title, some of
// them the browser's own defaults because no rule reaches the element, sections with no title, and
// a section called one thing in the list and another above the pane. The eye reads the hierarchy of
// a long pane from its headings, and six sizes is no hierarchy.
//
// ## The rule
//
// Two mechanisms, and nothing else:
//
//   - a section's TITLE is drawn by the frame (`SettingsTitle`, reached from `SettingsPane` and the
//     sub-page), from the section's own entry in `sections.ts`: its name and its icon;
//   - a GROUP's heading is `SectionHeading` (or `SettingGroup`, which draws one), from
//     `$lib/components/common`.
//
// So this counts, in every component under `lib/settings-ui/` and `routes/settings/`:
//
//   - a heading ELEMENT written in the markup (`<h1>` to `<h6>`, or `role="heading"`);
//   - a style rule whose selector reaches a heading element (`h2 {`, `.block h3,`, `:global(h1)`).
//
// A pane that needs a heading passes its words to the component; it never writes the element, and
// it never writes the look.
//
// ## Why a ratchet that stands at zero rather than a ban
//
// A ratchet at zero refuses the first hand-written heading exactly as a ban would, and it keeps the
// one shape every other count in `gate-baselines.json` has.
//
// ## What is not counted
//
// `SettingsTitle.svelte`, which IS the title mechanism: its `<h1>` is the one every section gets.
// It is named below with that reason, and the check that every named file still exists and still
// holds a heading keeps the exemption from outliving what it excuses.
//
// What this does NOT see: a heading drawn by a component that lives OUTSIDE these two folders and
// is rendered into a settings pane (the Folders and Activity screens are two). Their headings are
// counted nowhere, and that is said here rather than silently assumed covered.
//
// Run: node scripts/check_settings_headings.js   (--record to lower the recorded number)

import { readFile } from 'node:fs/promises';
import { join } from 'node:path';

import { everySvelteFile, fromSource, ratchet, SOURCE, withoutComments } from './lib/tree.js';

const SCOPES = [
	join(SOURCE, 'lib', 'settings-ui'),
	join(SOURCE, 'routes', 'settings'),
	join(SOURCE, 'lib', 'library'),
	join(SOURCE, 'lib', 'jobs')
];

/** The mechanisms themselves. A file here is where the one heading is drawn, and why. */
const THE_MECHANISM = {
	'lib/settings-ui/SettingsTitle.svelte':
		"the section's title: the one h1 every section and sub-page gets, with its icon"
};

/** A heading element in the markup. The tag, or the role that makes anything one. */
const ELEMENT = /<h[1-6][\s>/]|\brole=["']heading["']/g;

/**
 * A selector that reaches a heading element. The element name must stand alone (`h2`, not `.h2x`
 * or `--h2`), so it is preceded by the start, a space, a combinator, a comma or an open bracket,
 * and followed by the end or by something that continues a compound.
 */
const HEADING_SELECTOR = /(^|[\s,>+~(])h[1-6](?=$|[\s,.:#[)>+~])/;

/** A style block's rules: each selector list, up to its open brace. */
const RULE = /([^{}]+)\{/g;

/** How many files must be read before a clean result means anything. */
const AT_LEAST = 40;

/** The heading elements a component writes. */
function elementsIn(code) {
	return code.match(ELEMENT)?.length ?? 0;
}

/** The style rules in a component whose selector reaches a heading element. */
function headingRulesIn(code) {
	let found = 0;
	for (const block of code.matchAll(/<style\b[^>]*>([\s\S]*?)<\/style>/g)) {
		for (const rule of block[1].matchAll(RULE)) {
			const selector = rule[1].trim();
			// An at-rule's prelude (`@media (max-width: 767px)`) is not a selector.
			if (selector.startsWith('@')) continue;
			if (HEADING_SELECTOR.test(selector)) found += 1;
		}
	}
	return found;
}

/*
 * A KNOWN POSITIVE, run every time. A pattern that stopped matching would report zero forever, and
 * zero is exactly what this gate is supposed to report. So silence has to be proved to be a
 * reading rather than a blindness.
 */
const PLANT =
	'<h2 id="x">Words</h2>\n<div role="heading">W</div>\n<style>\n\t.block h3,\n\t.other {\n\t\tfont: var(--text-h2);\n\t}\n\t:global(h1) {\n\t\tmargin: 0;\n\t}\n\t.h2x {\n\t\tmargin: 0;\n\t}\n</style>';
if (elementsIn(PLANT) !== 2 || headingRulesIn(PLANT) !== 2) {
	console.error(
		`settings-headings: the patterns no longer find a planted heading (elements ${elementsIn(PLANT)} of 2, ` +
			`rules ${headingRulesIn(PLANT)} of 2). The gate is blind; fix it before trusting a zero.`
	);
	process.exit(1);
}

const offenders = [];
const excusing = new Set();
let read = 0;

for (const scope of SCOPES) {
	for (const path of await everySvelteFile(scope)) {
		if (/\.test\.svelte$|(Harness|Probe)\.svelte$/.test(path)) continue;
		read += 1;
		const where = fromSource(path);
		const code = withoutComments(await readFile(path, 'utf8'));
		const count = elementsIn(code) + headingRulesIn(code);
		if (where in THE_MECHANISM) {
			if (count > 0) excusing.add(where);
			continue;
		}
		if (count > 0) offenders.push({ where, count });
	}
}

/* Every exemption must still be excusing something: a name that went, or a file that stopped
   drawing a heading, would go on vouching silently for whatever appears there next. */
const inert = Object.keys(THE_MECHANISM).filter((name) => !excusing.has(name));
if (inert.length > 0) {
	console.error(
		'settings-headings: these are named as THE_MECHANISM and draw no heading: the file has gone\n' +
			`or the title moved:\n  ${inert.join('\n  ')}\nMove the exemption with the title.`
	);
	process.exit(1);
}

if (read < AT_LEAST) {
	console.error(
		`settings-headings: only ${read} components read under settings-ui and routes/settings; there ` +
			`should be at least ${AT_LEAST}.\n  The walk found nothing to check, which is not the same as nothing being wrong.`
	);
	process.exit(1);
}

const total = offenders.reduce((sum, one) => sum + one.count, 0);

const complaint = await ratchet('settings-headings', 'handwritten', total, {
	what: 'heading element(s) or heading style rule(s) written by hand on a settings screen',
	instead:
		"A group's heading is `SectionHeading` from '$lib/components/common' (or a `SettingGroup`\n" +
		"    with a `heading`, which draws one). A section's title is the frame's: it is drawn from the\n" +
		"    section's entry in `settings-ui/sections.ts`, so a pane writes no title at all.",
	offenders: offenders
		.sort((a, b) => b.count - a.count)
		.slice(0, 12)
		.map((one) => `${one.count}x  ${one.where}`)
});

if (complaint) {
	console.error(`\n${complaint}\n`);
	process.exit(1);
}

console.log(
	`settings-headings: ${total} hand-written heading(s) across ${read} settings components (at the recorded number)`
);
