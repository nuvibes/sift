/*
 * A pager published to the frame's foot has to be taken back down.
 *
 * Panels that page a long list do not draw their own pager: the frame does, in its foot, so every
 * paged screen has the band in the same place at the same height. A panel reaches it by calling
 * `onpaging` with what to draw, and must call `onpaging(null)` as it goes away. Forgotten, nothing
 * errors: the pager of a screen somebody has left stays in the foot of the one they are on,
 * counting a list that is not on the page.
 *
 * Components either publish and withdraw, or hand the prop straight to a child that does; this
 * holds all three shapes. A text scan, like `check_one_server_type.js`: what has to be true is a
 * fact about the source, and a rendered test would need every panel mounted against its own
 * fixture.
 */

import { readdirSync, readFileSync, statSync } from 'node:fs';
import { dirname, join, relative, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

import { describe, expect, it } from 'vitest';

/** `frontend/src`, resolved from this file: the runner is started from more than one place. */
const SOURCE = resolve(dirname(fileURLToPath(import.meta.url)), '..', '..', '..');

/** The component that DEFINES the prop. Its own declaration is the contract, not a use of it. */
const THE_PAGER = 'lib/components/common/Pager.svelte';

function everyComponent(dir: string): string[] {
	const found: string[] = [];
	for (const entry of readdirSync(dir)) {
		if (entry === 'node_modules' || entry === '.svelte-kit') continue;
		const path = join(dir, entry);
		if (statSync(path).isDirectory()) found.push(...everyComponent(path));
		else if (entry.endsWith('.svelte')) found.push(path);
	}
	return found;
}

/** The `<script>` block, so a mention of the prop inside markup is not read as a declaration. */
function scriptOf(source: string): string {
	const block = /<script[^>]*>([\s\S]*?)<\/script>/.exec(source);
	return block ? block[1] : '';
}

const files = everyComponent(SOURCE)
	.map((path) => ({
		where: relative(SOURCE, path).split('\\').join('/'),
		source: readFileSync(path, 'utf8')
	}))
	.filter((file) => file.where !== THE_PAGER);

/** Declared in a component's props, rather than merely mentioned in its prose. */
const TAKES_IT = /\bonpaging\??\s*:|,\s*onpaging\b|\{\s*onpaging\b/;

/** Every component that TAKES the prop. */
const takers = files.filter((file) => TAKES_IT.test(scriptOf(file.source)));

describe('a panel that publishes a pager takes it back down', () => {
	it('found the panels that take the prop', () => {
		/* The positive control. This is a regular expression over source, and the way it fails is by
		   matching nothing and passing for ever. Nine panels page a list today and three pass the
		   prop through; a number far below that means the scan has stopped seeing them. */
		expect(takers.length, 'no component takes onpaging, which cannot be right').toBeGreaterThan(8);
	});

	it.each(takers.map((file) => file.where))(
		'%s either withdraws its pager or hands the prop to a child',
		(where) => {
			const file = takers.find((one) => one.where === where)!;
			const script = scriptOf(file.source);
			// Passing it straight down: `<Child {onpaging} />`. The child is held to this rule too,
			// so the pass-through is covered by the row for whatever it hands it to.
			if (/\{onpaging\}/.test(file.source.replace(script, ''))) return;

			expect(
				/onpaging\?\.\(\s*null\s*\)/.test(script),
				`${where} publishes a pager to the frame's foot and never takes it back. The band ` +
					`stays under the NEXT screen, counting a list that is not on it, with arrows that ` +
					`turn its pages. Add \`onDestroy(() => onpaging?.(null))\`.`
			).toBe(true);
		}
	);

	it('holds the publish and the withdrawal together', () => {
		/* The other direction, and it is the one that makes the rule above worth having: a panel that
		   withdraws a pager it never published is dead code, and a scan that only looked for the
		   withdrawal would be satisfied by exactly that. Every panel that withdraws must publish. */
		const withdrawsWithoutPublishing = takers
			.filter((file) => /onpaging\?\.\(\s*null\s*\)/.test(scriptOf(file.source)))
			.filter((file) => !/onpaging\?\.\((?!\s*null)/.test(scriptOf(file.source)))
			.map((file) => file.where);

		expect(withdrawsWithoutPublishing).toEqual([]);
	});
});
