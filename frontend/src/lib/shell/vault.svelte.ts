/*
 * The vault: "Hidden" on screen, "vault" in code, paths and columns. Its state is the server's,
 * read on every load; nothing here hides anything, since the server never sends what is concealed.
 */

import { api, ApiError } from '$lib/api/client';
import type { components } from '$lib/api/schema';
import { PIN_DIGITS } from '$lib/components/common/PinBox.svelte';
import { libraryChanges } from '$lib/library/changes.svelte';

export type VaultState = components['schemas']['VaultState'];

/* The tabs of this browser share one session, so Hidden shut in one is shut in all. */
const SHUT = 'shut';
const tabs = typeof BroadcastChannel === 'undefined' ? null : new BroadcastChannel('sift.vault');

export type UnlockFailure = 'wrong-pin' | 'too-many-attempts';

type SetPinFailure = 'wrong-password' | 'not-a-pin';

export class Vault {
	unlocked = $state(false);
	pinSet = $state(false);
	loaded = $state(false);

	/** Bumped on every open or shut, so a screen re-fetches by watching one number. */
	generation = $state(0);

	async load(): Promise<void> {
		const state = await api.get<VaultState>('/vault');
		this.#sessionLocked = false;
		this.#adopt(state);
		this.loaded = true;
	}

	/* Always takes the PIN, even when open: showing hidden things is a deliberate act. */
	async unlock(pin: string): Promise<UnlockFailure | null> {
		try {
			this.#adopt(await api.post<VaultState>('/vault/unlock', { body: { pin } }));
			/* The only moment a PIN from before the six-digit rule can be recognised. */
			if (pin.length < PIN_DIGITS) vaultPrompt.creating = 'longer';
			return null;
		} catch (error) {
			if (error instanceof ApiError && error.status === 401) return 'wrong-pin';
			if (error instanceof ApiError && error.status === 429) return 'too-many-attempts';
			throw error;
		}
	}

	/** Asks for the password too, so a screen left signed in cannot plant a PIN. */
	async setPin(pin: string, password: string): Promise<SetPinFailure | null> {
		try {
			await api.put<void>('/auth/pin', { body: { pin, current_password: password } });
		} catch (error) {
			if (error instanceof ApiError && error.status === 401) return 'wrong-password';
			if (error instanceof ApiError && error.status === 422) return 'not-a-pin';
			throw error;
		}
		await this.load();
		return null;
	}

	/* Never fails and never asks: the lock triggers must not leave it unlocked. */
	async lock(options: { keepalive?: boolean } = {}): Promise<boolean> {
		if (this.#sessionLocked) return true;
		let reached = true;
		try {
			// `keepalive`, or a closing tab cancels the lock.
			await api.post<void>('/vault/lock', { keepalive: options.keepalive });
		} catch {
			reached = false;
		}
		this.#adopt({ unlocked: false, pin_set: this.pinSet });
		tabs?.postMessage(SHUT);
		// A lock the server never heard leaves the vault open there.
		return reached;
	}

	shutElsewhere(): void {
		this.#adopt({ unlocked: false, pin_set: this.pinSet });
	}

	#sessionLocked = false;

	/** Nothing adopted: the session refuses every list now. */
	sessionLocked(): void {
		this.#sessionLocked = true;
	}

	#adopt(state: VaultState): void {
		const changed = state.unlocked !== this.unlocked;
		this.unlocked = state.unlocked;
		this.pinSet = state.pin_set;
		if (!changed) return;
		this.generation += 1;
		libraryChanges.changed();
	}
}

export const vault = new Vault();

tabs?.addEventListener('message', (event) => {
	if (event.data === SHUT) vault.shutElsewhere();
});

/** ONE prompt in the shell, so even a toast's action can ask; no PIN means create one. */
class VaultPrompt {
	#asking = $state(false);

	#waiting: ((opened: boolean) => void)[] = [];

	/** An accessor, so the prompt CLOSING can be waited on. */
	get asking(): boolean {
		return this.#asking;
	}

	set asking(next: boolean) {
		this.#asking = next;
		if (next) return;
		this.reason = null;
		const waiting = this.#waiting.splice(0);
		for (const resolve of waiting) resolve(vault.unlocked);
	}

	/** `first`: no PIN; `longer`: a PIN shorter than the rule. */
	creating = $state<'first' | 'longer' | null>(null);

	reason = $state<string | null>(null);

	/** `loaded` too: `pinSet` starts false. */
	ask(): void {
		if (vault.loaded && !vault.pinSet) {
			this.creating = 'first';
			return;
		}
		this.asking = true;
	}

	/**
	 * Ask, and answer whether Hidden is open once the prompt closes; false with no PIN or on
	 * Cancel.
	 */
	opened(reason?: string): Promise<boolean> {
		this.reason = reason ?? null;
		this.ask();
		if (!this.#asking) this.reason = null;
		if (!this.#asking) return Promise.resolve(false);
		return new Promise((resolve) => this.#waiting.push(resolve));
	}
}

export const vaultPrompt = new VaultPrompt();
