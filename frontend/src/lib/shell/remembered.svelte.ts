/* What this browser remembers: an arrangement of one screen, not a preference. */

/** Null if nothing is stored or storage cannot say. */
export function readStored(key: string): string | null {
	try {
		return localStorage.getItem(key);
	} catch {
		return null;
	}
}

export function writeStored(key: string, value: string): void {
	try {
		localStorage.setItem(key, value);
	} catch {
		// Storage refused: it will not be remembered.
	}
}

export function clearStored(key: string): void {
	try {
		localStorage.removeItem(key);
	} catch {
		// Storage refused: it will not be remembered.
	}
}

export function rememberedFlag(key: string, fallback: boolean) {
	const stored = readStored(key);
	let value = $state(stored === null ? fallback : stored === 'yes');

	return {
		get on(): boolean {
			return value;
		},
		set on(next: boolean) {
			value = next;
			writeStored(key, next ? 'yes' : 'no');
		}
	};
}

/*
 * Whether this browser skips the SECOND, permanent-delete question; the first dialog always opens.
 * Per browser: somebody who deletes all day at their desk has not agreed to skip it everywhere.
 */
const DELETE_NO_CONFIRM = 'sift.delete.noConfirm';

const deleteSecondQuestion = rememberedFlag(DELETE_NO_CONFIRM, false);

export function deleteConfirmationSkipped(): boolean {
	return deleteSecondQuestion.on;
}

export function rememberDeleteConfirmation(): void {
	deleteSecondQuestion.on = true;
}

/** The way back: a guard that can be switched off must come back on. */
export function forgetDeleteConfirmation(): void {
	deleteSecondQuestion.on = false;
	// Both: clearing the key puts the browser back to "never said".
	clearStored(DELETE_NO_CONFIRM);
}
