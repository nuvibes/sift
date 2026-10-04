import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import { flushSync, mount, unmount } from 'svelte';
import { session } from '$lib/shell/session.svelte';
import { vault } from '$lib/shell/vault.svelte';
import layoutSource from '../../routes/+layout.svelte?raw';
import SwapModeKeeper from './SwapModeKeeper.svelte';
import { KEPT_AS, swapMode } from './mode.svelte';

/*
 * Who swap mode belongs to, and when it ends. Locking Hidden away leaves it: a pick may name a
 * Hidden thing, and the picks are kept in this tab's session storage so a full page load keeps the
 * mode. Signing out throws it away, and the rules sit outside the signed-in shell so the moment
 * the shell comes down is still seen.
 */

let host: HTMLDivElement;
let drawn: ReturnType<typeof mount> | undefined;

beforeEach(() => {
	session.viewer = { id: 'admin-1', role: 'admin' } as typeof session.viewer;
	vault.unlocked = true;
	host = document.createElement('div');
	document.body.append(host);
	drawn = mount(SwapModeKeeper, { target: host });
	flushSync();
});

afterEach(() => {
	if (drawn) unmount(drawn);
	drawn = undefined;
	host.remove();
	swapMode.leave();
	vault.unlocked = false;
	session.viewer = undefined;
});

describe('swap mode and the vault', () => {
	it('leaves the mode when Hidden is locked away', () => {
		swapMode.enter();
		flushSync();
		expect(swapMode.on).toBe(true);

		vault.unlocked = false;
		flushSync();

		expect(swapMode.on).toBe(false);
	});

	it('stays in the mode while the vault is merely still locked', () => {
		vault.unlocked = false;
		flushSync();
		swapMode.enter();
		flushSync();

		expect(swapMode.on).toBe(true);
	});
});

describe('swap mode and signing out', () => {
	it('throws the picks away when nobody is signed in any more', () => {
		swapMode.enter();
		swapMode.toggle({ kind: 'person', id: 'p1', name: 'Anyone' });
		flushSync();
		expect(sessionStorage.getItem(KEPT_AS)).not.toBeNull();

		session.viewer = null;
		flushSync();

		expect(swapMode.on).toBe(false);
		expect(sessionStorage.getItem(KEPT_AS)).toBeNull();
	});

	it('is mounted outside the signed-in shell, where a sign-out still reaches it', () => {
		const keeper = layoutSource.indexOf('<SwapModeKeeper />');
		const firstBranch = layoutSource.indexOf('{#if onShellScreen}');
		expect(keeper).toBeGreaterThan(-1);
		expect(firstBranch).toBeGreaterThan(-1);
		expect(keeper).toBeLessThan(firstBranch);
	});
});
