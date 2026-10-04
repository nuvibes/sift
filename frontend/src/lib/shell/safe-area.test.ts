/** The safe areas are read through the four tokens and nowhere else, and the top bar reads its own. */

import { readdirSync, readFileSync } from 'node:fs';
import { join, relative, resolve } from 'node:path';

import { describe, expect, it } from 'vitest';

import { directInsets, TOKEN_FILE } from './safe-area';
import topBar from '$lib/components/shell/TopBar.svelte?raw';

/** Every stylesheet under `dir`, read off the disk: one asked for through `?raw` arrives empty. */
function sheets(dir = resolve('src')): { file: string; text: string }[] {
	return readdirSync(dir, { withFileTypes: true }).flatMap((entry) => {
		const path = join(dir, entry.name);
		if (entry.isDirectory()) return sheets(path);
		if (!entry.name.endsWith('.css')) return [];
		const file = relative(resolve('.'), path).split('\\').join('/');
		return [{ file, text: readFileSync(path, 'utf8') }];
	});
}

/* Every stylesheet, component and module in the client, as text. The test files are left out: a
   test that plants a read to prove the rule refuses it would otherwise be refused itself. */
const TREE = [
	...Object.entries(
		import.meta.glob(['/src/**/*.{svelte,ts}', '!/src/**/*.test.ts'], {
			query: '?raw',
			import: 'default',
			eager: true
		}) as Record<string, string>
	).map(([file, text]) => ({ file: file.replace(/^\//, ''), text })),
	...sheets()
];

/* Written as a join so this file does not hold the spelling it is looking for. */
const INSET = ['env(safe', 'area', 'inset'].join('-');

describe('the safe areas', () => {
	it('are read nowhere but the four tokens', () => {
		expect(TREE.find((one) => one.file === TOKEN_FILE)?.text).toContain('--safe-top');
		expect(TREE.filter((one) => !one.text.trim()).map((one) => one.file)).toEqual([]);
		expect(directInsets(TREE)).toEqual([]);
	});

	it('refuses a component that reads an inset itself', () => {
		const planted = {
			file: 'src/lib/components/Planted.svelte',
			text: `<div></div>\n<style>\n\tdiv {\n\t\tpadding-top: ${INSET}-top, 0px);\n\t}\n</style>\n`
		};
		expect(directInsets([...TREE, planted])).toEqual([
			{ file: 'src/lib/components/Planted.svelte', lines: [4] }
		]);
	});

	it('refuses a second spelling in the token file itself', () => {
		const tokens = TREE.find((one) => one.file === TOKEN_FILE);
		expect(tokens).toBeDefined();
		const spelled = { file: TOKEN_FILE, text: `${tokens?.text}\n.x { top: ${INSET}-top); }\n` };
		expect(directInsets([spelled])).toHaveLength(1);
	});

	it('is read by the top bar, which stands at the top edge', () => {
		const rule = topBar.slice(topBar.indexOf('.topbar {'));
		expect(rule.slice(0, rule.indexOf('}'))).toContain('padding-block-start: var(--safe-top)');
	});
});
