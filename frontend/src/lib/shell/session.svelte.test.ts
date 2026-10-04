/* SPDX-License-Identifier: AGPL-3.0-or-later */
import { afterEach, expect, it } from 'vitest';
import { session } from './session.svelte';

afterEach(() => {
	session.viewer = undefined;
});

/** Who the server says this is, as `/auth/me` answers. */
function signedIn(role: 'admin' | 'guest', locked: boolean): void {
	session.viewer = { id: 'u1', username: 'ada', role, locked } as typeof session.viewer;
}

it("is an admin's reader's go-ahead only while the session is not locked", () => {
	signedIn('admin', false);
	expect(session.adminUnlocked).toBe(true);
	signedIn('admin', true);
	expect(session.adminUnlocked).toBe(false);
	expect(session.isAdmin).toBe(true);
	signedIn('guest', false);
	expect(session.adminUnlocked).toBe(false);
});
