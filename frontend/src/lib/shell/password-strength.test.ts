import { describe, expect, it } from 'vitest';
import { assess, MIN_LENGTH } from './password-strength';

/* Live guidance while somebody types, and the reason it has to be honest.
 *
 * There is no reset email in Sift. A password chosen on the setup screen and mistyped is an install
 * nobody can open, and the recovery is a command run on the machine that destroys the saved site
 * logins on its way past. So the meter's job is to tell somebody they are not done yet BEFORE they
 * press the button, and never to tell them they are done when the server is about to disagree.
 */

const STRONG = 'Corr3ct-Horse!staple9';

describe('what it says about nothing', () => {
	it('says nothing at all', () => {
		// An empty box with a red bar under it is telling somebody off for not having started.
		expect(assess('').strength).toBe('empty');
	});

	it('and still lists what will be wanted', () => {
		expect(assess('').rules).toHaveLength(4);
		expect(assess('').rules.every((rule) => !rule.met)).toBe(true);
	});
});

describe('the rules it mirrors from the server', () => {
	it('holds the same length floor', () => {
		expect(assess('Ab1!' + 'x'.repeat(MIN_LENGTH - 5)).rules[0].met).toBe(false);
		expect(assess('Ab1!' + 'x'.repeat(MIN_LENGTH - 4)).rules[0].met).toBe(true);
	});

	it('wants both cases, a number and a symbol', () => {
		const rules = assess('alllowercase').rules;
		expect(rules[1].met).toBe(false);
		expect(rules[2].met).toBe(false);
		expect(rules[3].met).toBe(false);
	});

	it('marks each one off as it is met', () => {
		expect(assess(STRONG).rules.every((rule) => rule.met)).toBe(true);
	});
});

describe('the one word it puts on it', () => {
	it('calls anything short of the policy weak, whatever else it has', () => {
		/* A nine-character password with a symbol in it is not "good": the server is about to
		 * refuse it. Calling it anything but weak would be telling somebody they are finished a
		 * moment before they are told they are not. */
		const nearly = assess('Ab1!efghi');
		expect(nearly.acceptable).toBe(false);
		expect(nearly.strength).toBe('weak');
	});

	it('reserves the top word for length as well as variety', () => {
		expect(assess(STRONG).strength).toBe('strong');
		// The same variety, barely past the floor: acceptable, and not the top word.
		expect(assess('Ab1!efghij').strength).not.toBe('strong');
	});

	it('rises with length once the policy is met', () => {
		const scores = ['Ab1!efghij', 'Ab1!efghijkl', STRONG].map((one) => assess(one).score);
		expect(scores[0]).toBeLessThan(scores[1]);
		expect(scores[1]).toBeLessThan(scores[2]);
	});
});

describe('what it cannot know', () => {
	it('calls a breached password acceptable, because it cannot see the list', () => {
		/* The honest limit, asserted so nobody later reads a green meter as a promise. The list is a
		 * hundred thousand entries checked on the server; shipping it to the browser to save one
		 * round trip would be a megabyte on every page load. This is why nothing here disables a
		 * submit button. The server has the last word and says why. */
		const breached = assess('Password123!');
		expect(breached.acceptable).toBe(true);
	});
});
