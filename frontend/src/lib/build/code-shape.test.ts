/**
 * What `scripts/check_code_shape.js` refuses and records, driven with files made for it.
 *
 * The gate over the real tree runs in `gates.js`; these hold its rules: a module, a test file and
 * the loose files of `src/lib` may not grow, a new entry over its line is refused, and comments
 * grow only when their share and their count rise together.
 */

import { describe, expect, it } from 'vitest';

import {
	addFile,
	compareShape,
	emptyShape,
	looseImports,
	measure
} from '../../../scripts/lib/code-shape.js';

const code = (lines: number) => 'const x = 1;\n'.repeat(lines);
const commented = (comments: number, lines: number) => '// why\n'.repeat(comments) + code(lines);

function shapeOf(files: Record<string, string>) {
	const shape = emptyShape();
	for (const [path, text] of Object.entries(files))
		addFile(shape, path, text, /\.(test|spec)\.ts$/.test(path));
	return shape;
}

const verdict = (before: Record<string, string>, after: Record<string, string>, top = [10, 10]) =>
	compareShape({ lib_top_level: top[0], ...shapeOf(before) }, shapeOf(after), top[1]);

describe('the code-shape gate', () => {
	it('reads comments in markup, script and style as the other gates do', () => {
		const text = '<script>\n\t// why\n\tlet a = 1;\n</script>\n<!-- why -->\n<p>{a}</p>\n';
		expect(measure(text)).toEqual({ lines: 6, nonblank: 6, comments: 2, share: 33.3 });
	});

	it('refuses a module over its line that grows', () => {
		const before = { 'frontend/src/lib/a.ts': code(1200) };
		expect(shapeOf(before).module_lines).toEqual({ 'frontend/src/lib/a.ts': 1200 });
		expect(verdict(before, { 'frontend/src/lib/a.ts': code(1201) }).rose).toEqual([
			'module_lines frontend/src/lib/a.ts: 1201, recorded 1200'
		]);
	});

	it('holds a test file to its own line', () => {
		const before = { 'frontend/src/lib/a.test.ts': code(2100) };
		const found = shapeOf(before);
		expect([found.test_file_lines, found.module_lines]).toEqual([
			{ 'frontend/src/lib/a.test.ts': 2100 },
			{}
		]);
		expect(verdict(before, { 'frontend/src/lib/a.test.ts': code(2101) }).rose).toHaveLength(1);
	});

	it('holds a test file to the comment line as a module is', () => {
		const before = { 'frontend/src/lib/a.test.ts': commented(40, 60) };
		expect(shapeOf(before).comment_lines).toEqual({ 'frontend/src/lib/a.test.ts': 40 });
		const after = { 'frontend/src/lib/a.test.ts': commented(45, 60) };
		expect(verdict(before, after).rose).toHaveLength(1);
		expect(verdict({}, before).added).toHaveLength(1);
	});

	it('refuses a new entry over the line', () => {
		expect(verdict({}, { 'desktop/src/new.ts': code(1001) }).added).toEqual([
			'module_lines desktop/src/new.ts: 1001, over the line and not recorded'
		]);
	});

	it('refuses comments that grow in share and in lines together', () => {
		const before = { 'frontend/src/lib/a.ts': commented(40, 60) };
		expect(verdict(before, { 'frontend/src/lib/a.ts': commented(45, 60) }).rose).toHaveLength(1);
	});

	it('records, rather than refuses, comments that rise in only one of the two', () => {
		const before = { 'frontend/src/lib/a.ts': commented(40, 60) };
		for (const after of [commented(40, 50), commented(42, 90)]) {
			const found = verdict(before, { 'frontend/src/lib/a.ts': after });
			expect([found.rose, found.added, found.fell.length]).toEqual([[], [], 1]);
		}
	});

	it('leaves a short file out of the share', () => {
		expect(shapeOf({ 'frontend/src/lib/a.ts': commented(30, 5) }).comment_share).toEqual({});
	});

	it('refuses a loose file more at the top of src/lib and records one fewer', () => {
		expect(verdict({}, {}, [224, 225]).rose).toEqual(['lib_top_level: 225 files, recorded 224']);
		expect(verdict({}, {}, [224, 223]).fell).toEqual(['lib_top_level: 223 files, recorded 224']);
	});

	it('asks for a fall to be recorded', () => {
		const found = verdict(
			{ 'frontend/src/lib/a.ts': code(1500) },
			{ 'frontend/src/lib/a.ts': code(900) }
		);
		expect(found.fell).toEqual([
			'module_lines frontend/src/lib/a.ts: under the line, recorded 1500'
		]);
	});

	it('refuses a component that imports a file at the top of src/lib', () => {
		const text = [
			"import { a } from '$lib/loose';",
			"import b from '../../loose.svelte';",
			"const c = await import('$lib');",
			"import type { D } from '$lib/shell/when';",
			"import e from '../common/E.svelte';",
			"import f from 'svelte';",
			"import { g } from '$lib/bridge';"
		].join('\n');
		expect(looseImports('components/common/X.svelte', text, new Set(['bridge']))).toEqual([
			'$lib/loose',
			'../../loose.svelte',
			'$lib'
		]);
	});
});
