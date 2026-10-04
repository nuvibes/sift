/* The one filter in `test-setup.ts`, put to a stack of each kind.
 *
 * A console filter is the classic silent gate: broken open it hides every warning in the suite and
 * reports nothing, and the run still passes. So the predicate is asked about a warning it must drop
 * and about three it must not, and the file it lives in is the file the whole suite loads.
 */
import { describe, expect, it } from 'vitest';

import {
	NETWORK_REFUSED,
	isLibraryTeardownDerivedRead,
	noServerAt,
	refuseStrays,
	refuseTheNetwork,
	requestWords
} from './test-setup';

/** What the framework actually passes to `console.warn` in a development build. */
const DERIVED_INERT =
	'%c[svelte] derived_inert\n%cReading a derived belonging to a now-destroyed effect may result in stale values';

/** A real stack, shortened: a library timer landing after the component was unmounted. */
const LIBRARY_TIMER = [
	'Error',
	'    at Module.derived_inert (/work/sift/frontend/node_modules/svelte/src/internal/client/warnings.js:82:16)',
	'    at Object.get current [as current] (/work/sift/frontend/node_modules/svelte-toolbelt/dist/box/box-extras.svelte.js:11:23)',
	'    at Timeout._onTimeout (/work/sift/frontend/node_modules/bits-ui/dist/bits/utilities/dismissible-layer/use-dismissable-layer.svelte.js:46:36)',
	'    at listOnTimeout (node:internal/timers:605:17)'
].join('\n');

/** The same warning, raised while one of our own components is on the stack. */
const OUR_COMPONENT = [
	'Error',
	'    at Module.derived_inert (/work/sift/frontend/node_modules/svelte/src/internal/client/warnings.js:82:16)',
	'    at Object.get current [as current] (/work/sift/frontend/node_modules/svelte-toolbelt/dist/box/box-extras.svelte.js:11:23)',
	'    at closed (/work/sift/frontend/src/lib/components/organize/DuplicatesPanel.svelte:71:9)'
].join('\n');

describe('the library teardown filter', () => {
	it('drops the warning a library timer raises after we have unmounted the component', () => {
		expect(isLibraryTeardownDerivedRead(DERIVED_INERT, LIBRARY_TIMER)).toBe(true);
	});

	it('keeps the same warning when our own code is the one reading it', () => {
		expect(isLibraryTeardownDerivedRead(DERIVED_INERT, OUR_COMPONENT)).toBe(false);
	});

	it('keeps every other warning the library raises', () => {
		const other = '%c[svelte] state_referenced_locally\n%cThis reference only captures...';
		expect(isLibraryTeardownDerivedRead(other, LIBRARY_TIMER)).toBe(false);
	});

	it('is not defeated by its own frame, which is in every stack it reads', () => {
		/* The regression this file exists for as much as the filter does. The stack is taken inside
		   the replacement, so this file is always the innermost frame, and it sits under
		   `frontend/src` like any component. Counted as our code, the filter suppresses nothing and
		   the suite goes on printing every warning, which looks exactly like a filter that has not
		   been written yet. */
		const withOurOwnFrame = [
			'Error',
			'    at console.warn (/work/sift/frontend/src/test-setup.ts:171:38)',
			...LIBRARY_TIMER.split('\n').slice(1)
		].join('\n');
		expect(isLibraryTeardownDerivedRead(DERIVED_INERT, withOurOwnFrame)).toBe(true);
	});

	it('keeps a warning that is not a string, rather than guessing at it', () => {
		expect(isLibraryTeardownDerivedRead({ message: 'derived_inert' }, LIBRARY_TIMER)).toBe(false);
	});
});

describe('the network guard', () => {
	it('is the fetch every test starts with', () => {
		expect(globalThis.fetch).toBe(refuseTheNetwork);
	});

	it('refuses a request, naming it, and the test that made it is told afterwards', async () => {
		await expect(fetch('http://localhost/api/loops')).rejects.toThrow(
			`${NETWORK_REFUSED}: GET http://localhost/api/loops`
		);
		// What `afterEach` does: the refusal was kept, so the client swallowing it changes nothing.
		expect(() => refuseStrays()).toThrow(/GET http:\/\/localhost\/api\/loops/);
		// And it forgets once said, so the next test starts clean.
		expect(() => refuseStrays()).not.toThrow();
	});

	it('answers a path a file named as a server that is not there, and nothing else', async () => {
		noServerAt('/api/insights/path');
		await expect(fetch('http://localhost:3000/api/insights/path?x=1')).rejects.toThrow(
			'Nothing is listening in this test'
		);
		await expect(fetch('/api/insights/path')).rejects.toBeInstanceOf(TypeError);
		// Not kept: the file said it leaves this one unanswered.
		expect(() => refuseStrays()).not.toThrow();
		// A neighbouring address is still refused and still told.
		await expect(fetch('http://localhost:3000/api/insights')).rejects.toThrow(NETWORK_REFUSED);
		expect(() => refuseStrays()).toThrow(/GET http:\/\/localhost:3000\/api\/insights$/m);
	});

	it('says nothing when nothing went out', () => {
		expect(() => refuseStrays()).not.toThrow();
	});

	it('names the method and the address, whichever shape the request came in', () => {
		expect(requestWords('/health')).toBe('GET /health');
		expect(requestWords(new URL('http://localhost/api/tags'), { method: 'put' })).toBe(
			'PUT http://localhost/api/tags'
		);
		expect(requestWords(new Request('http://localhost/api/sites', { method: 'POST' }))).toBe(
			'POST http://localhost/api/sites'
		);
	});
});
