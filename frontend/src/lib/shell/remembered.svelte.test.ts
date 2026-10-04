/* The one thing in here that is a GUARD rather than an arrangement.
 *
 * Most of what this module remembers is a band being open or a list being sorted: forgetting it
 * costs somebody a click. The delete confirmation is not that: switching it off removes a question
 * asked before files leave a disk, so the three calls have to behave exactly as they say. In
 * particular the way BACK has to work, because a guard somebody can switch off and cannot switch on
 * is a guard they lose by accident.
 */

import { beforeEach, describe, expect, it } from 'vitest';

import {
	deleteConfirmationSkipped,
	forgetDeleteConfirmation,
	rememberDeleteConfirmation
} from './remembered.svelte';

beforeEach(() => {
	forgetDeleteConfirmation();
});

describe('the second delete question', () => {
	it('is asked until somebody says otherwise', () => {
		expect(deleteConfirmationSkipped()).toBe(false);
	});

	it('is skipped once, and only once, it has been agreed to', () => {
		rememberDeleteConfirmation();
		expect(deleteConfirmationSkipped()).toBe(true);
	});

	it('comes back, and leaves nothing behind in this browser', () => {
		rememberDeleteConfirmation();

		forgetDeleteConfirmation();

		expect(deleteConfirmationSkipped()).toBe(false);
		// Cleared rather than written as a "no". The difference is whether this browser has an
		// opinion at all: a stored "no" would outrank a later change to what Sift asks by default,
		// which is the one thing a default is for.
		expect(localStorage.getItem('sift.delete.noConfirm')).toBeNull();
	});
});
