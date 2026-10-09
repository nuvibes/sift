/* The test config type-checks the tests. */

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
