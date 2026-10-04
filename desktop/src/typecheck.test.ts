/* The test config type-checks the tests.
 *
 * `tsconfig.test.json` extends the build config, and `extends` hands a config its parent's
 * `exclude` whenever it names none of its own, so without its own `exclude` the build's
 * exclusion of every test file comes along, an exclude beats an include, and `npm run typecheck`
 * checks no test at all while it says it passed.
 *
 * Asked of TypeScript's own reading of the config rather than of the JSON, because the fault is
 * in how the two files combine, which reading one of them cannot see.
 */

import * as path from 'node:path';

import ts from 'typescript';
import { describe, expect, it } from 'vitest';

const ROOT = path.join(__dirname, '..');

function filesOf(config: string): string[] {
	const file = path.join(ROOT, config);
	const read = ts.readConfigFile(file, (at) => ts.sys.readFile(at));
	if (read.error !== undefined) throw new Error(String(read.error.messageText));
	return ts.parseJsonConfigFileContent(read.config, ts.sys, ROOT, undefined, file).fileNames;
}

describe('what each typecheck reads', () => {
	it('the test config reads every test and every double', () => {
		const files = filesOf('tsconfig.test.json').map((one) =>
			path.relative(ROOT, one).split(path.sep).join('/')
		);
		expect(files).toContain('src/typecheck.test.ts');
		expect(files).toContain('src/verbs.test.ts');
		expect(files).toContain('test/electron-stub.ts');
	});

	it('the build config still leaves the tests out of what ships', () => {
		/* The other half, so the rule above cannot be satisfied by deleting the build's exclude:
		   a dist/ carrying *.test.js would be packaged into the installer. */
		const files = filesOf('tsconfig.json');
		expect(files.some((one) => one.endsWith('.test.ts'))).toBe(false);
		expect(files.some((one) => path.basename(one) === 'main.ts')).toBe(true);
	});
});
