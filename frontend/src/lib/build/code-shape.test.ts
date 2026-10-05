/**
 * What `scripts/check_code_shape.js` refuses and records, driven with files made for it.
 *
 * The gate over the real tree runs in `gates.js`; these hold its rules: a module, a test file and
 * the loose files of `src/lib` may not grow, a function may not gain a branch past its line, a new
 * entry over its line is refused, and comments grow only when their share and their count rise
 * together.
 */

import { describe, expect, it } from 'vitest';

import {
	addFile,
	branchesIn,
	compareShape,
	emptyShape,
	looseImports,
	measure
} from '../../../scripts/lib/code-shape.js';

const code = (lines: number) => 'const x = 1;\n'.repeat(lines);
const commented = (comments: number, lines: number) => '// why\n'.repeat(comments) + code(lines);
const branched = (name: string, ifs: number) =>
	`function ${name}(x: number) {\n${'\tif (x) x -= 1;\n'.repeat(ifs)}}\n`;

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

	it('counts a branch, a loop, a catch, a case and a short-circuit, and nothing else', () => {
		const text = [
			'function run(x: number, list: number[], held?: { a?: number }) {',
			'\tif (x) x -= 1;',
			'\telse if (x > 1) x += 1;',
			'\tfor (const y of list) x += y;',
			'\tfor (const k in list) x += 1;',
			'\tfor (let i = 0; i < 1; i += 1) x += 1;',
			'\twhile (x > 9) x -= 1;',
			'\tdo x -= 1; while (x > 5);',
			'\ttry { x += 1; } catch { x = 0; } finally { x += 1; }',
			'\tswitch (x) { case 1: break; case 2: break; default: break; }',
			'\tconst a = x ? 1 : 2;',
			'\tlet b = (a && x) || held?.a;',
			'\tb ??= a ?? 3;',
			'\treturn b;',
			'}'
		].join('\n');
		// two ifs, five loops, one catch, two cases, one conditional, two short-circuits, `??=`
		// and `??`; an optional chain, a `default` and a `finally` add none.
		expect(branchesIn('a.ts', text)).toEqual({ run: 1 + 15 });
	});

	it('counts a function written inside another by itself, and names each where it is written', () => {
		const text = [
			'class Store {',
			'\tconstructor(x: number) { if (x) this.put(x); }',
			'\tput(x: number) { return [x].map((y) => (y ? 1 : 2)); }',
			'\tget size() { return 1; }',
			'\tkept = () => 2;',
			'}',
			'const read = function () { return 1; };',
			'const table = { row: () => 1 };',
			'const rows = derive(() => (read() ? 1 : 2));',
			'function read() { return 2; }',
			'declare function typed(x: number): void;'
		].join('\n');
		expect(branchesIn('a.ts', text)).toEqual({
			'Store.constructor': 2,
			'Store.put': 1,
			'Store.put.(anonymous)': 2,
			'Store.size': 1,
			'Store.kept': 1,
			read: 1,
			row: 1,
			rows: 2,
			'read#2': 1
		});
	});

	it('reads the functions of both scripts of a component and none of its markup', () => {
		const text = [
			'<script lang="ts" module>',
			'\texport function shared(x: number) { return x ? 1 : 2; }',
			'</script>',
			'<script lang="ts">',
			'\tfunction press(x: number) { if (x && x > 1) return; }',
			'</script>',
			'{#if a}<button onclick={() => (a ? b : c)}>x</button>{/if}'
		].join('\n');
		expect(branchesIn('A.svelte', text)).toEqual({ shared: 2, press: 3 });
	});

	it('refuses a function over its branches that gains one, and a new one over the line', () => {
		const before = { 'frontend/src/lib/a.ts': branched('run', 21) };
		expect(shapeOf(before).function_branches).toEqual({ 'frontend/src/lib/a.ts::run': 22 });
		expect(shapeOf({ 'frontend/src/lib/a.ts': branched('run', 19) }).function_branches).toEqual({});
		expect(verdict(before, { 'frontend/src/lib/a.ts': branched('run', 22) }).rose).toEqual([
			'function_branches frontend/src/lib/a.ts::run: 23, recorded 22'
		]);
		expect(verdict({}, before).added).toEqual([
			'function_branches frontend/src/lib/a.ts::run: 22, over the line and not recorded'
		]);
		expect(
			shapeOf({ 'frontend/src/lib/a.test.ts': branched('run', 30) }).function_branches
		).toEqual({});
	});

	it('lets a function move to another module with its number, and no higher', () => {
		const before = { 'frontend/src/lib/a.ts': branched('run', 21), 'frontend/src/lib/b.ts': '' };
		const moved = { 'frontend/src/lib/a.ts': '', 'frontend/src/lib/b.ts': branched('run', 21) };
		const found = verdict(before, moved);
		expect([found.rose, found.added]).toEqual([[], []]);
		expect(found.fell).toEqual([
			'function_branches frontend/src/lib/b.ts::run: moved from frontend/src/lib/a.ts::run'
		]);
		const grown = { 'frontend/src/lib/a.ts': '', 'frontend/src/lib/b.ts': branched('run', 22) };
		expect(verdict(before, grown).added).toHaveLength(1);
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
