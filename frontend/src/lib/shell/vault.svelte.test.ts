/* The vault store: the reasoning a screen depends on.
 *
 * The API is stubbed, so what is under test is this file's own behaviour, not concealment, which
 * is the server's and is tested there. Three things here are worth guarding because getting any of
 * them wrong produces something that still looks like a working privacy control:
 *
 *   - opening never happens without the server saying so, so a failed unlock cannot leave the app
 *     drawing an open vault;
 *   - shutting always happens, even when the request fails, because the direction that fails safe
 *     must not be blocked by a network;
 *   - the generation counter moves when what is on screen has changed, because that is the signal
 *     the screens re-fetch on, and a stale grid after an unlock is hidden data still hidden or
 *     (far worse) revealed data still showing after a lock.
 */

import { beforeEach, describe, expect, it, vi } from 'vitest';

import { api } from '$lib/api/client';
import { libraryChanges } from '$lib/library/changes.svelte';
import { Vault, vault as shellVault, vaultPrompt } from '$lib/shell/vault.svelte';

vi.mock('$lib/api/client', () => ({
	api: { get: vi.fn(), post: vi.fn(), put: vi.fn(), del: vi.fn() },
	ApiError: class extends Error {
		status: number;
		constructor(status: number, message = 'failed') {
			super(message);
			this.status = status;
		}
	}
}));

const mocked = vi.mocked(api);

// The stub above is the module under mock, so the real class is not in scope. This is the one the
// store will compare against.
async function apiError(status: number): Promise<Error> {
	const { ApiError } = await import('$lib/api/client');
	return new ApiError(status, 'refused');
}

beforeEach(() => {
	vi.resetAllMocks();
});

describe('reading the state', () => {
	it('takes both facts from the server rather than assuming either', async () => {
		mocked.get.mockResolvedValue({ unlocked: true, pin_set: true });
		const vault = new Vault();

		await vault.load();

		expect(vault.unlocked).toBe(true);
		expect(vault.pinSet).toBe(true);
		expect(vault.loaded).toBe(true);
	});

	it('starts shut, so a screen drawn before the answer arrives conceals', () => {
		const vault = new Vault();

		expect(vault.unlocked).toBe(false);
		expect(vault.pinSet).toBe(false);
	});
});

describe('opening it', () => {
	it('sends the PIN and adopts what the server answered', async () => {
		mocked.post.mockResolvedValue({ unlocked: true, pin_set: true });
		const vault = new Vault();

		expect(await vault.unlock('2468')).toBeNull();

		expect(mocked.post).toHaveBeenCalledWith('/vault/unlock', { body: { pin: '2468' } });
		expect(vault.unlocked).toBe(true);
	});

	it('stays shut on a wrong PIN', async () => {
		mocked.post.mockRejectedValue(await apiError(401));
		const vault = new Vault();

		expect(await vault.unlock('0000')).toBe('wrong-pin');
		expect(vault.unlocked).toBe(false);
	});

	it('says when the attempts have run out, which is not the same as being wrong', async () => {
		mocked.post.mockRejectedValue(await apiError(429));
		const vault = new Vault();

		expect(await vault.unlock('0000')).toBe('too-many-attempts');
		expect(vault.unlocked).toBe(false);
	});

	it('rethrows anything it does not have words for', async () => {
		mocked.post.mockRejectedValue(new Error('the network is gone'));
		const vault = new Vault();

		await expect(vault.unlock('2468')).rejects.toThrow('the network is gone');
		expect(vault.unlocked).toBe(false);
	});
});

describe('shutting it', () => {
	it('shuts locally even when the request fails, and says it did not land', async () => {
		// Both halves matter. The screen stops showing what it was showing either way (there is no
		// version of this where drawing it longer is better), but a lock the server never heard
		// leaves the vault open there, and a reload would reveal everything again. The caller is
		// told, so it can say so rather than report a success it did not get.
		mocked.post.mockResolvedValue({ unlocked: true, pin_set: true });
		const vault = new Vault();
		await vault.unlock('2468');

		mocked.post.mockRejectedValue(new Error('the network is gone'));
		expect(await vault.lock()).toBe(false);

		expect(vault.unlocked).toBe(false);
	});

	it('is not an error when nothing was open', async () => {
		mocked.post.mockResolvedValue(undefined);
		const vault = new Vault();

		await expect(vault.lock()).resolves.toBe(true);
		expect(vault.unlocked).toBe(false);
	});

	it('asks the browser to let the request outlive a closing tab when told to', async () => {
		mocked.post.mockResolvedValue(undefined);
		const vault = new Vault();

		await vault.lock({ keepalive: true });

		expect(mocked.post).toHaveBeenCalledWith('/vault/lock', { keepalive: true });
	});
});

describe('the generation counter', () => {
	it('moves when the vault opens and when it shuts', async () => {
		mocked.post.mockResolvedValue({ unlocked: true, pin_set: true });
		const vault = new Vault();
		const start = vault.generation;

		await vault.unlock('2468');
		const afterUnlock = vault.generation;
		await vault.lock();

		expect(afterUnlock).toBeGreaterThan(start);
		expect(vault.generation).toBeGreaterThan(afterUnlock);
	});

	it('rings the library bell, so every wall re-reads and not just the five that watch it', async () => {
		/* Unlocking changes WHICH FILES THIS SESSION MAY SEE, which is the question every list
		   in the app asks the server (the face piles, the entity walls, the duplicate groups),
		   not only the grid. So it rings the bell they all already listen to, rather than
		   relying on the few readers of `generation`. */
		mocked.post.mockResolvedValue({ unlocked: true, pin_set: true });
		const vault = new Vault();
		const before = libraryChanges.generation;

		await vault.unlock('2468');

		expect(libraryChanges.generation).toBeGreaterThan(before);

		const afterUnlock = libraryChanges.generation;
		await vault.lock();
		expect(libraryChanges.generation).toBeGreaterThan(afterUnlock);

		// And a state that did not change rings nothing, or every poll of the vault would re-read
		// every wall in the application.
		const quiet = libraryChanges.generation;
		await vault.lock();
		expect(libraryChanges.generation).toBe(quiet);
	});

	it('moves the counter and rings the bell together, so a screen needs only one of them', async () => {
		/* The counter and the bell move in the same statement, and the statement is guarded, so
		   neither can move without the other. This is what makes watching either one watching
		   both, which is what the recently-viewed wall and Theater rely on: each watches one,
		   not a second mechanism for the same event. */
		mocked.post.mockResolvedValue({ unlocked: true, pin_set: true });
		mocked.get.mockResolvedValue({ unlocked: true, pin_set: true });
		const vault = new Vault();

		const acts = [
			() => vault.unlock('2468'),
			// A read that finds it already open: neither may move, or every poll re-reads every wall.
			() => vault.load(),
			() => vault.lock()
		];
		for (const act of acts) {
			const counter = vault.generation;
			const bell = libraryChanges.generation;
			await act();
			expect(
				vault.generation - counter,
				'the counter and the bell came apart, so one of the two is a reader that can miss'
			).toBe(libraryChanges.generation - bell);
		}
		expect(vault.generation, 'the three acts above moved nothing at all').toBeGreaterThan(0);
	});

	it('stays put when a read finds nothing has changed', async () => {
		mocked.get.mockResolvedValue({ unlocked: false, pin_set: true });
		const vault = new Vault();

		await vault.load();
		const after = vault.generation;
		await vault.load();

		expect(vault.generation).toBe(after);
	});
});

/*
 * A PIN is six digits. Somebody with none is asked to make one where they pressed; somebody whose
 * PIN is shorter, kept from before that rule, is asked for a six-digit one the moment theirs opens
 * Hidden, which is the only moment its length can be known.
 */
describe('the PIN is six digits', () => {
	beforeEach(() => {
		vaultPrompt.creating = null;
		vaultPrompt.asking = false;
	});

	it('asks somebody with no PIN to create one, rather than for one they cannot have', () => {
		shellVault.loaded = true;
		shellVault.pinSet = false;

		vaultPrompt.ask();

		expect(vaultPrompt.creating).toBe('first');
		expect(vaultPrompt.asking).toBe(false);
	});

	it('asks for the PIN when there is one', () => {
		shellVault.loaded = true;
		shellVault.pinSet = true;

		vaultPrompt.ask();

		expect(vaultPrompt.asking).toBe(true);
		expect(vaultPrompt.creating).toBeNull();
	});

	/* An act refused because Hidden was locked (the Undo of a hide) asks in the one shared prompt
	 * and waits for it to close. What it hears is whether Hidden is open, read off the vault, so a
	 * Cancel and a PIN that opened it are told apart by the only fact that matters. */
	it('answers whether Hidden opened once the prompt closes', async () => {
		shellVault.loaded = true;
		shellVault.pinSet = true;
		shellVault.unlocked = false;

		const cancelled = vaultPrompt.opened();
		expect(vaultPrompt.asking).toBe(true);
		vaultPrompt.asking = false;
		expect(await cancelled).toBe(false);

		const proved = vaultPrompt.opened();
		shellVault.unlocked = true;
		vaultPrompt.asking = false;
		expect(await proved).toBe(true);
		shellVault.unlocked = false;
	});

	it('answers no at once for somebody with no PIN, who is asked to make one', async () => {
		shellVault.loaded = true;
		shellVault.pinSet = false;

		expect(await vaultPrompt.opened()).toBe(false);
		expect(vaultPrompt.creating).toBe('first');
	});

	it('asks for a six-digit PIN when a shorter one opens Hidden, and opens it anyway', async () => {
		mocked.post.mockResolvedValue({ unlocked: true, pin_set: true });
		const vault = new Vault();

		expect(await vault.unlock('2468')).toBeNull();

		expect(vault.unlocked).toBe(true);
		expect(vaultPrompt.creating).toBe('longer');
	});

	it('asks nothing more of a six-digit PIN', async () => {
		mocked.post.mockResolvedValue({ unlocked: true, pin_set: true });
		const vault = new Vault();

		await vault.unlock('246810');

		expect(vaultPrompt.creating).toBeNull();
	});

	it('sets a PIN with the password, and says which half was refused', async () => {
		mocked.put.mockResolvedValueOnce(undefined);
		mocked.get.mockResolvedValue({ unlocked: false, pin_set: true });
		const vault = new Vault();

		expect(await vault.setPin('246810', 'the password')).toBeNull();
		expect(mocked.put).toHaveBeenCalledWith('/auth/pin', {
			body: { pin: '246810', current_password: 'the password' }
		});
		expect(vault.pinSet).toBe(true);

		mocked.put.mockRejectedValueOnce(await apiError(401));
		expect(await vault.setPin('246810', 'wrong')).toBe('wrong-password');
		mocked.put.mockRejectedValueOnce(await apiError(422));
		expect(await vault.setPin('2468', 'the password')).toBe('not-a-pin');
	});
});

describe('another tab of this browser', () => {
	/* A channel of the test's own: the store's never hears what it says itself. */
	const heard = (channel: BroadcastChannel) =>
		new Promise<unknown>((done) =>
			channel.addEventListener('message', (e) => done(e.data), { once: true })
		);

	it('is told when Hidden shuts here', async () => {
		const other = new BroadcastChannel('sift.vault');
		mocked.post.mockResolvedValue(undefined);
		const told = heard(other);

		await new Vault().lock();

		expect(await told).toBe('shut');
		other.close();
	});

	it('shuts it here when it shuts there, since the session is the same', async () => {
		mocked.get.mockResolvedValue({ unlocked: true, pin_set: true });
		await shellVault.load();
		const before = shellVault.generation;
		const other = new BroadcastChannel('sift.vault');

		other.postMessage('shut');
		await vi.waitFor(() => expect(shellVault.unlocked).toBe(false));

		expect(shellVault.generation).toBe(before + 1);
		other.close();
	});
});
