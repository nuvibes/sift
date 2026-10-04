/* What the shell's own tests run against.
 *
 * `all: true` is the whole mechanism, exactly as it is on the client: without it only the files a
 * test happens to import are measured, so an untested file is absent from the report rather than
 * sitting in it at zero. `scripts/check_desktop_coverage.mjs` counts the zeroes.
 */

import { fileURLToPath } from 'node:url';
import { defineConfig } from 'vitest/config';

export default defineConfig({
	resolve: {
		// `electron` is a binary, not a package a test can import. See test/electron-stub.ts.
		alias: {
			electron: fileURLToPath(new URL('./test/electron-stub.ts', import.meta.url))
		}
	},
	test: {
		environment: 'node',
		include: ['src/**/*.test.ts'],
		coverage: {
			provider: 'v8',
			all: true,
			include: ['src/**/*.ts'],
			exclude: ['src/**/*.test.ts'],
			reporter: ['text-summary', 'json-summary'],
			reportsDirectory: './coverage'
		}
	}
});
