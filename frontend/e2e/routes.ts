import type { Route } from '@playwright/test';

/**
 * Answer an intercepted request with the server's own JSON, changed.
 *
 * Reading the real answer can lose a race, and losing it must not fail a test. `route.fetch()`
 * hands back a response tied to the request that made it. A page that navigates, or a test that
 * ends, retires that request while the answer is still on its way, and the read then rejects
 * ("Response has been disposed", "Test ended"). Nothing is waiting for that rejection, so it is
 * reported against whichever test the worker is running by then, which is a different test in a
 * different file, and it happens only under load.
 *
 * A request this could not stand in for is passed through, which is what the page would have got
 * without the interception. `change` runs outside the guard: a fault in it is the test's own and
 * is thrown.
 */
export async function rewrite<T>(route: Route, change: (body: T) => void): Promise<void> {
	let answer;
	let body: T;
	try {
		answer = await route.fetch();
		body = (await answer.json()) as T;
	} catch {
		await route.fallback().catch(() => undefined);
		return;
	}
	change(body);
	await route.fulfill({ response: answer, json: body }).catch(() => undefined);
}
