/* A pager published to the frame's foot is taken back down (`onpaging(null)`), or passed to a
 * child that does; a text scan over the source. */

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

const takers = files.filter((file) => TAKES_IT.test(scriptOf(file.source)));

describe('a panel that publishes a pager takes it back down', () => {
	it('found the panels that take the prop', () => {
		/* The positive control: a scan that matches nothing passes for ever. */
		expect(takers.length, 'no component takes onpaging, which cannot be right').toBeGreaterThan(8);
	});

	it.each(takers.map((file) => file.where))(
		'%s either withdraws its pager or hands the prop to a child',
		(where) => {
			const file = takers.find((one) => one.where === where)!;
			const script = scriptOf(file.source);
			// Passed straight down: the child is held to the rule too.
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
		/* And every panel that withdraws must publish. */
		const withdrawsWithoutPublishing = takers
			.filter((file) => /onpaging\?\.\(\s*null\s*\)/.test(scriptOf(file.source)))
			.filter((file) => !/onpaging\?\.\((?!\s*null)/.test(scriptOf(file.source)))
			.map((file) => file.where);

		expect(withdrawsWithoutPublishing).toEqual([]);
	});
});
