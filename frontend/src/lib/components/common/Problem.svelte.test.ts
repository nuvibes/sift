import { readFileSync, readdirSync, statSync } from 'node:fs';
import { dirname, join, relative, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

import { afterEach, describe, expect, it } from 'vitest';
import { flushSync, mount } from 'svelte';
import { reactiveProps } from '$lib/design/testing.svelte';
import Problem from './Problem.svelte';

/*
 * The line a screen shows when something failed, and the rule that keeps there being one of it.
 *
 * The announcement is the part worth a test. Written out per screen it would come out as an
 * interrupting alert on some screens and a silent status on others, so whether somebody using a
 * screen reader was told about a failure at all would depend on which screen they were on. Nothing
 * on either screen would look wrong.
 */

let host: HTMLElement;

afterEach(() => {
	host?.remove();
});

function render(message: string | null | undefined) {
	host = document.createElement('div');
	document.body.append(host);
	mount(Problem, { target: host, props: reactiveProps({ message }) as never });
	flushSync();
	return host.querySelector('p');
}

describe('what it says and how it says it', () => {
	it('shows the sentence it was given', () => {
		expect(render('The folders could not be loaded.')?.textContent).toBe(
			'The folders could not be loaded.'
		);
	});

	it('interrupts, every time', () => {
		expect(render('That could not be saved.')?.getAttribute('role')).toBe('alert');
	});

	it('draws nothing when there is nothing wrong', () => {
		// So a caller hands it whatever its state holds. A guard written at every call site is a
		// guard somebody eventually writes differently.
		expect(render(null)).toBeNull();
		expect(render(undefined)).toBeNull();
		expect(render('')).toBeNull();
	});
});

/** The whole of `src`, resolved from this file rather than from the working directory. */
const SOURCE = resolve(dirname(fileURLToPath(import.meta.url)), '..', '..', '..');
const THE_ONE = 'lib/components/common/Problem.svelte';

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

describe('one word for it, and one way of announcing it', () => {
	const files = everyMarkupFile(SOURCE).map((path) => ({
		where: relative(SOURCE, path).split('\\').join('/'),
		source: readFileSync(path, 'utf8')
	}));

	it('surveys the interface rather than an empty tree', () => {
		expect(files.length).toBeGreaterThan(100);
		expect(files.some((file) => file.where === THE_ONE)).toBe(true);
	});

	it('is written in one file', () => {
		// The class, wherever it is written into markup. A second one is a second look and, sooner
		// or later, a second answer to whether it is announced.
		const writing = files
			.filter((file) => file.where !== THE_ONE)
			.filter((file) => /class="[^"]*\bproblem\b/.test(file.source))
			.map((file) => file.where);

		expect(writing).toEqual([]);
	});

	it('is written in that file, so the rule above is not vacuous', () => {
		const one = files.find((file) => file.where === THE_ONE);
		expect(/class="problem"/.test(one?.source ?? '')).toBe(true);
		expect(/role="alert"/.test(one?.source ?? '')).toBe(true);
	});

	it('leaves no failure announced the quiet way', () => {
		/*
		 * The other half, and why this check is worth more than the class name. A screen can stop
		 * using the shared component and still look right, with its own paragraph in the same red;
		 * what it gets wrong is `role="status"`, which does not interrupt, so the fault reaches
		 * somebody looking and is withheld from somebody who is not. No failure-shaped state may be
		 * announced that way.
		 */
		const quiet = files
			.filter((file) => file.where !== THE_ONE)
			.flatMap((file) =>
				[...file.source.matchAll(/<[^>]*role="status"[^>]*>\s*\{([^}]*)\}/g)]
					.filter((match) => /\b(problem|error|failure|refusal)\b/i.test(match[1]))
					.map((match) => `${file.where}: ${match[1].trim()}`)
			);

		expect(quiet).toEqual([]);
	});
});
