/*
 * A style rule that says what something IS lives in one place.
 *
 * Three rules would otherwise be retyped on nearly every screen: the sentence under a heading, the
 * small upright label over a group, and the row a dialog's two buttons sit in. Every copy can read
 * the right tokens and still drift: a rule retyped twenty times is how an error line comes to reach
 * for the fill red where the readable one was meant.
 *
 * What is shared is only the part that is genuinely the same. Spacing and measure stay with the
 * screen, because those really do differ: a sentence under a settings heading wants different room
 * from one under a page title, and a screen that says so outranks the shared rule.
 *
 * Anything with MEANING attached is a component instead, never a class. The error line carries a
 * screen reader role, and a role that travels separately from its styling is a role that gets
 * chosen differently on the next screen. See `Problem`.
 */

import { readFileSync, readdirSync, statSync } from 'node:fs';
import { dirname, join, relative, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

import { describe, expect, it } from 'vitest';

/** The whole of `src`, resolved from this file rather than from the working directory: the runner
 *  is started from more than one place and a relative root silently surveys nothing. */
const SOURCE = resolve(dirname(fileURLToPath(import.meta.url)), '..', '..');

/**
 * The declarations each shared rule owns, and how a copy of it is recognised.
 *
 * A component may still use any one of these on its own: plenty of things are `--text-label`,
 * and a right-aligned row of anything is `flex-end`. What it may not do is write the whole rule
 * again.
 *
 * Two ways of recognising one, and the difference matters. The upright label is known by its
 * DECLARATIONS: four together, one of them a specific letter-spacing, is not a coincidence, and a
 * copy under some other class name is still a copy. The dialog button row is known by its
 * SELECTOR, because its three declarations are the most ordinary in CSS: a settings row that
 * right-aligns its control writes the same three and is not a copy of anything.
 */
const SHARED: {
	rule: string;
	declarations: string[];
	/** When set, only a rule written under this name counts as a copy. */
	selector?: RegExp;
}[] = [
	{
		rule: '.section-label',
		declarations: [
			'font: var(--text-label);',
			'text-transform: uppercase;',
			'letter-spacing: 0.06em;',
			'color: var(--sift-ink-3);'
		]
	},
	{
		rule: '.sheet .buttons',
		declarations: ['display: flex;', 'justify-content: flex-end;', 'gap: var(--space-2);'],
		selector: /(^|\s|\.)buttons$/
	}
];

function everyMarkupFile(dir: string): string[] {
	const found: string[] = [];
	for (const entry of readdirSync(dir)) {
		if (entry === 'node_modules') continue;
		const path = join(dir, entry);
		if (statSync(path).isDirectory()) found.push(...everyMarkupFile(path));
		else if (entry.endsWith('.svelte')) found.push(path);
	}
	return found;
}

/** The declarations inside one style rule, once per selector it is written under.
 *
 * A selector list is read a name at a time, and it may run over several lines. Both matter: read
 * one line only and split nothing, `.lede,\n.empty { ... }` would come back as a rule about
 * `.empty`, and a component retyping a shared rule under the first name of a list would be
 * invisible to every check below. Comments come out first, or a comment sitting above a rule is read as part
 * of its selector.
 */
function ruleBodies(source: string): { selector: string; body: string }[] {
	const styles = source.slice(source.indexOf('<style>')).replace(/\/\*[\s\S]*?\*\//g, '');
	return [...styles.matchAll(/(?:^|\n)\t([^{};]+)\{([^}]*)\}/g)].flatMap((match) =>
		match[1].split(',').map((selector) => ({ selector: selector.trim(), body: match[2] }))
	);
}

const APP_CSS = readFileSync(join(SOURCE, 'app.css'), 'utf8');
/** The question, which no stylesheet may ask, and the answer, which every one of them reads. */
const MEDIA_QUERY = 'prefers-reduced-motion';
const MOTION_GUARD = "[data-motion='reduce']";

/**
 * Rules only. A gate that reads its own explanation is a gate that reports on prose: a note in
 * `app.css` naming the media query it replaced would be flagged.
 */
function rulesOnly(source: string): string {
	return source.replace(/\/\*[\s\S]*?\*\//g, '').replace(/<!--[\s\S]*?-->/g, '');
}

const components = everyMarkupFile(SOURCE).map((path) => ({
	where: relative(SOURCE, path).split('\\').join('/'),
	source: readFileSync(path, 'utf8')
}));

describe('who decides whether the app moves', () => {
	/*
	 * NOTHING IN THE STYLESHEETS ASKS THE OPERATING SYSTEM DIRECTLY.
	 *
	 * Sift has its own motion switch, and `@media (prefers-reduced-motion: reduce)` asks Windows
	 * and nothing else, and a media query cannot be un-applied by any later rule, at any
	 * specificity, by any means. A rule written that way would leave "Full motion" beaten by a
	 * blanket rule killing every transition with `!important`, while the screen that offered the
	 * choice reported that it was animating.
	 *
	 * The resolved answer (theirs where they gave one, the system's where they asked to follow it)
	 * is stamped on the root as `data-motion`, and that is what every rule reads.
	 *
	 * This is the guard, and it has to read the STYLESHEETS rather than the module. The unit tests
	 * next door prove the attribute is written; not one of them notices if a rule goes back to
	 * asking Windows, because the attribute is still perfectly correct while the rule ignores it.
	 */
	it('has no rule anywhere that asks Windows instead of asking Sift', () => {
		const asking = [
			...components.filter((component) => rulesOnly(component.source).includes(MEDIA_QUERY)),
			...(rulesOnly(APP_CSS).includes(MEDIA_QUERY) ? [{ where: 'app.css' }] : [])
		].map((file) => file.where);

		expect(
			asking,
			"These ask the operating system directly, so Sift's own motion setting cannot overrule " +
				`them. Write the rule under ${MOTION_GUARD} instead.`
		).toEqual([]);
	});

	/* The other half: the guard has to be REACHED. A stylesheet with no reduced-motion rules at all
	 * would pass the test above and mean the mode does not exist. */
	it('still has rules for the still mode, under the answer rather than the question', () => {
		const guarded = components.filter((component) =>
			rulesOnly(component.source).includes(MOTION_GUARD)
		);
		expect(guarded.length).toBeGreaterThan(15);
		expect(rulesOnly(APP_CSS)).toContain(MOTION_GUARD);
	});
});

describe('the shared rules are declared once', () => {
	it('surveys the interface rather than an empty tree', () => {
		expect(components.length).toBeGreaterThan(100);
		expect(APP_CSS.length).toBeGreaterThan(1000);
	});

	for (const shared of SHARED) {
		it(`${shared.rule} is written in app.css`, () => {
			// The liveness half. Without it, deleting the rule from `app.css` would leave every
			// component correctly not-retyping a rule that no longer dresses anything.
			const declared = APP_CSS.slice(APP_CSS.indexOf(`${shared.rule} {`));
			for (const declaration of shared.declarations) {
				expect(declared.slice(0, declared.indexOf('}'))).toContain(declaration);
			}
		});

		it(`${shared.rule} is not retyped in a component`, () => {
			const retyping = components
				.filter((component) =>
					ruleBodies(component.source).some(
						(rule) =>
							(!shared.selector || shared.selector.test(rule.selector)) &&
							shared.declarations.every((declaration) => rule.body.includes(declaration))
					)
				)
				.map((component) => component.where);

			expect(retyping).toEqual([]);
		});
	}

	it('the settings sentence is not retyped in a component', () => {
		/* Written out rather than in the list above, because this one is keyed on its selector: a
		   component is free to set body-sm ink-3 on something of its own, and what it may not do is
		   write those two declarations under `.lede` again. The screens that deliberately want the
		   larger, lighter sentence still say so in their own stylesheet, and that is the point of
		   sharing only the part that was identical. */
		const retyping = components
			.filter((component) =>
				ruleBodies(component.source).some(
					(rule) =>
						rule.selector.startsWith('.lede') &&
						rule.body.includes('font: var(--text-body-sm);') &&
						rule.body.includes('color: var(--sift-ink-3);')
				)
			)
			.map((component) => component.where);

		expect(retyping).toEqual([]);
	});

	it('.lede is written in app.css', () => {
		const declared = APP_CSS.slice(APP_CSS.indexOf('.lede {'));
		expect(declared.slice(0, declared.indexOf('}'))).toContain('font: var(--text-body-sm);');
		expect(declared.slice(0, declared.indexOf('}'))).toContain('color: var(--sift-ink-3);');
	});
});
