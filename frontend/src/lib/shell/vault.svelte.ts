/* The vault, as the screens see it.
 *
 * ## It is called "Hidden" on screen, and "vault" everywhere in here
 *
 * Two names for one feature, on purpose, and the split is worth knowing before reading further or
 * writing anything new.
 *
 * **"Hidden"** is the name a person sees: the rail slot, the top-bar control, every button, every
 * sentence. It says what the feature does. "Vault" would say what it is like, and invite the two
 * things it is not: a safe, and encryption. It hides rows from the screen; it does not encrypt a
 * single byte, and the files sit on disk exactly where they always were.
 *
 * **"vault"** stays in the code, the API paths (`/api/vault`, `/api/assets/{id}/vault`), the
 * database columns and these comments. Not laziness: those are addresses and a schema, renaming an
 * address breaks every link and bookmark that points at it, and a column rename is a migration
 * bought for nothing. The interface is what somebody reads; the wire is not.
 *
 * So: if you are writing something a person will read, write Hidden. If you are naming a field, a
 * route or a variable, it is the vault, and it stays the vault.
 *
 * ## What this file is
 *
 * Two facts and the handful of writes that change them: whether this browser has the vault open,
 * and whether there is a PIN to open it with. Both come from the server on every load rather than
 * being remembered here, because both are the server's answer and a client that kept its own copy
 * would eventually draw an unlocked vault over a locked one.
 *
 * Nothing here hides anything. That is worth saying plainly, because it is the mistake this file is
 * most likely to invite: a concealed thing is absent from what the server sends, so there is no
 * filtering to do and any that appeared here would be decoration over data the browser had already
 * been given. Unlocking reloads; it does not reveal something that was already in hand.
 */

import { api, ApiError } from '$lib/api/client';
import type { components } from '$lib/api/schema';
import { PIN_DIGITS } from '$lib/components/common/PinBox.svelte';
import { libraryChanges } from '$lib/library/changes.svelte';

export type VaultState = components['schemas']['VaultState'];

/* The tabs and windows of this browser share one session, so Hidden shut in one is shut in all. */
const SHUT = 'shut';
const tabs = typeof BroadcastChannel === 'undefined' ? null : new BroadcastChannel('sift.vault');

/** What went wrong, in words a person can act on. Anything else is rethrown. */
export type UnlockFailure = 'wrong-pin' | 'too-many-attempts';

/** Why a new PIN was not saved. Anything else is rethrown. */
type SetPinFailure = 'wrong-password' | 'not-a-pin';

export class Vault {
	unlocked = $state(false);
	/** Whether the account holds a PIN. Without one there is nothing to open the vault with, so no
	 * screen may offer to put anything in it. */
	pinSet = $state(false);
	loaded = $state(false);

	/** Bumped every time the vault opens or shuts, so a screen can re-fetch by watching one number
	 * instead of every screen subscribing to the same event. What is on the grid changes when this
	 * changes, and only the server knows what it changed to. */
	generation = $state(0);

	async load(): Promise<void> {
		const state = await api.get<VaultState>('/vault');
		// Answered, so the session is open again: a locked one is refused here.
		this.#sessionLocked = false;
		this.#adopt(state);
		this.loaded = true;
	}

	/* Open it. Always takes the PIN, including when it is already open.
	 *
	 * That re-prompt is the point of the whole feature rather than an oversight: showing hidden
	 * things is meant to be a deliberate act, and one inherited from something done earlier is not
	 * deliberate. The server has no route that opens the vault without a PIN, so there is nothing
	 * here that could be relaxed into skipping it even if somebody tried.
	 */
	async unlock(pin: string): Promise<UnlockFailure | null> {
		try {
			this.#adopt(await api.post<VaultState>('/vault/unlock', { body: { pin } }));
			/* A PIN that opened the vault IS the stored PIN, so its length is the stored one's: the
			   only moment a PIN kept from before the six-digit rule can be recognised. It keeps
			   working; its owner is asked for a six-digit one. */
			if (pin.length < PIN_DIGITS) vaultPrompt.creating = 'longer';
			return null;
		} catch (error) {
			if (error instanceof ApiError && error.status === 401) return 'wrong-pin';
			if (error instanceof ApiError && error.status === 429) return 'too-many-attempts';
			throw error;
		}
	}

	/**
	 * Set or replace the PIN. The server asks for the account's password as well, so a screen left
	 * signed in cannot be used to plant one.
	 *
	 * The one write every screen that sets a PIN goes through, so each says the same thing about a
	 * refusal. Setting a PIN opens nothing: showing hidden things is still its own act.
	 */
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

	/* Shut it. Never fails, never asks, and is safe to call when nothing was open.
	 *
	 * Every lock trigger and the panic control come through here, so it is deliberately the one
	 * write in the app that swallows its errors. A locked screen that also has to report why it
	 * could not lock is a screen that stays unlocked while somebody reads the message.
	 */
	async lock(options: { keepalive?: boolean } = {}): Promise<boolean> {
		// The session lock shut Hidden on the server along with it, and the server refuses every
		// vault request from a locked session: asking would only be refused.
		if (this.#sessionLocked) return true;
		let reached = true;
		try {
			// `keepalive` is for the lock fired by a tab that is closing. Without it the browser
			// cancels the request along with the page, and the vault the person thought they had
			// shut on the way out is still open for whoever opens Sift next.
			await api.post<void>('/vault/lock', { keepalive: options.keepalive });
		} catch {
			reached = false;
		}
		// Locked locally either way, because that is what takes the content off this screen and
		// there is no version of this where showing it longer is the better answer.
		this.#adopt({ unlocked: false, pin_set: this.pinSet });
		tabs?.postMessage(SHUT);
		// But the caller is told, because the two states differ where it matters: a lock the server
		// never heard leaves the vault open there, and a reload would reveal everything again.
		return reached;
	}

	/** Another tab of this browser shut Hidden, and so shut it here. */
	shutElsewhere(): void {
		this.#adopt({ unlocked: false, pin_set: this.pinSet });
	}

	/* Whether this tab locked its session. Cleared by a load the server answers. */
	#sessionLocked = false;

	/** This tab's session was just locked, which shut Hidden on the server too. Nothing is
	 *  adopted: moving the generation would send every list to a session that refuses them. */
	sessionLocked(): void {
		this.#sessionLocked = true;
	}

	#adopt(state: VaultState): void {
		const changed = state.unlocked !== this.unlocked;
		this.unlocked = state.unlocked;
		this.pinSet = state.pin_set;
		if (!changed) return;
		this.generation += 1;
		/*
		 * And the bell every list in the application already listens to.
		 *
		 * `generation` above has a handful of readers, and the face piles, the entity walls and the
		 * duplicate groups are not among them. Without this, unlocking would put the vault's
		 * files back on the grid and leave every other wall drawn from an answer taken while the
		 * vault was shut: thumbnails and details not appearing until the page was reloaded.
		 *
		 * Ringing `libraryChanges` rather than adding more readers of `generation` is the durable
		 * half: WHAT CHANGED IS WHICH FILES THIS SESSION MAY SEE, which is the question every one
		 * of those lists is asking the server. A screen written next year gets it for nothing, and
		 * nobody has to remember this store exists.
		 */
		libraryChanges.changed();
	}
}

export const vault = new Vault();

tabs?.addEventListener('message', (event) => {
	if (event.data === SHUT) vault.shutElsewhere();
});

/**
 * Asking for the PIN, from anywhere.
 *
 * The prompt is a component, and a component mounted by each control that needs it cannot work for
 * anything that is not a control: a toast saying a file could not be saved because the vault is
 * locked has an action that runs a function, and a function cannot mount a dialog.
 *
 * So the flag lives here and ONE prompt is mounted in the shell. The top bar and the Unhide button
 * drive this rather than holding their own, which also settles in one place what to do for somebody
 * who has no PIN at all. The enter-PIN prompt only ever VERIFIES a PIN, so showing it to them asks
 * for something that cannot exist and refuses whatever they type; they are asked to create one
 * instead, in a dialog over wherever they are, rather than being sent to another screen to find the
 * form.
 */
class VaultPrompt {
	#asking = $state(false);

	/** Who is waiting to hear how the prompt on screen ended. See `opened`. */
	#waiting: ((opened: boolean) => void)[] = [];

	/** Whether the shell's prompt is on screen. Bound by the one instance in the layout.
	 *
	 * An accessor rather than a plain field so that the prompt CLOSING is an event somebody can wait
	 * on. The prompt closes itself after a PIN opened Hidden, and closes on Cancel or Escape without
	 * one, and both land here; what opened is read off the vault itself, never off how it closed. */
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

	/**
	 * Whether the create-a-PIN dialog is on screen, and why.
	 *
	 * `first` for somebody with no PIN, who pressed something that needs one. `longer` for somebody
	 * whose PIN is shorter than the rule and who has just opened Hidden with it: that PIN goes on
	 * working, and the dialog can be put off.
	 */
	creating = $state<'first' | 'longer' | null>(null);

	/**
	 * What the PIN is being asked FOR, when it is not showing hidden items: the words of the act
	 * waiting on it ("Put it back"), which the prompt says as its title. Null for the prompt's own
	 * reason. Set by `opened` and cleared when the prompt closes, so a later plain ask never wears an
	 * earlier act's words.
	 */
	reason = $state<string | null>(null);

	/**
	 * Ask for the PIN, or ask for a new one if they have none.
	 *
	 * `loaded` is checked as well as `pinSet`, and the uncertain case falls through to the prompt.
	 * `pinSet` starts false and is filled by the load on mount, so acting on it before the answer
	 * lands would ask somebody who HAS a PIN to create one they already have. The prompt is the safe
	 * fallback, and the server has the final say either way.
	 */
	ask(): void {
		if (vault.loaded && !vault.pinSet) {
			this.creating = 'first';
			return;
		}
		this.asking = true;
	}

	/**
	 * Ask for the PIN and answer whether Hidden is open once the prompt has closed.
	 *
	 * For an act that needs Hidden open and was refused because it was shut: the Undo of a hide is
	 * the one today. The person proves the PIN in the one shared prompt and the act is tried again,
	 * rather than being told to go and unlock and then do it again by hand. Answers false
	 * immediately for somebody with no PIN (they are asked to make one, which opens nothing), and
	 * false when the prompt is cancelled.
	 *
	 * `reason` is the act's own words, which the prompt says instead of "Show hidden items": asked
	 * so an Undo can put a file back, the prompt reads "Put it back", which is what pressing it does.
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
