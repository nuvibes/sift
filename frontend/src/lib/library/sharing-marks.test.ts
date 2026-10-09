/* What a badge says, which is the one piece of judgement in the marks. */

import { describe, expect, it } from 'vitest';
import { CLICK_THROUGH, markFor } from './sharing-marks';

describe('the mark for one thing', () => {
	it('is nothing at all when nothing has been said', () => {
		// Most of a library, and it must draw no badge rather than a neutral one.
		expect(markFor({})).toBeNull();
		expect(markFor({ shared: false, restricted: false })).toBeNull();
	});

	it('is shared when somebody can reach it', () => {
		const mark = markFor({ shared: true, shared_here: true });
		expect(mark).toMatchObject({ kind: 'shared', icon: 'group', here: true });
		expect(mark?.words).toBe('Shared with guests you chose, set here');
		expect(mark?.rest).toContain('guests you chose can see this');
	});

	it('is solid where the switch is, and hollow where it is not', () => {
		/* The actionable reading: solid says the decision is right here, hollow says go up. */
		const onAFile = { file: true };
		expect(markFor({ shared: true, shared_here: true }, onAFile)?.filled).toBe(true);
		expect(markFor({ shared: true }, onAFile)?.filled).toBe(false);
		expect(markFor({ restricted: true, restricted_here: true }, onAFile)?.filled).toBe(true);
		expect(markFor({ restricted: true }, onAFile)?.filled).toBe(false);
		// Amber included. A convention that holds everywhere except one mark is not a convention.
		expect(markFor({ shared: true, restricted: true, shared_here: true }, onAFile)?.filled).toBe(
			true
		);
		expect(markFor({ shared: true, restricted: true }, onAFile)?.filled).toBe(false);
	});

	it('draws a folder, a tag or a person solid on its own row', () => {
		/* There is nowhere above one of those for the switch to be: the decision on it is its own. */
		expect(markFor({ shared: true, shared_here: true })?.filled).toBe(true);
		expect(markFor({ restricted: true, restricted_here: true })?.filled).toBe(true);
	});

	it('draws a label under a shared network hollow, and the network solid', () => {
		/* A Site under a network is shared by the network's grant, and its own row holds no
		   switch. */
		const label = markFor({ shared: true, shared_here: false, restricted_here: false });
		expect(label?.filled).toBe(false);
		expect(label?.words).toContain('set on something it is in');
		expect(markFor({ shared: true, shared_here: true, restricted_here: false })?.filled).toBe(true);
	});

	it('still draws a tag or a person solid when it is handed no where-facts at all', () => {
		// Those inherit nothing, so the only place a decision on one can be is the thing itself.
		expect(markFor({ shared: true })?.filled).toBe(true);
		expect(markFor({ restricted: true })?.filled).toBe(true);
	});

	it('says which of the two it is, in words as well as in the glyph', () => {
		/* Filled-versus-hollow is only legible to somebody who already knows the rule. */
		expect(markFor({ shared: true }, { file: true })?.words).toContain('set on something it is in');
		expect(markFor({ shared: true, shared_here: true }, { file: true })?.words).toContain(
			'set here'
		);
	});

	it('is restricted when somebody is kept from it, and says so as a refusal', () => {
		/* Not "not shared". The whole reason the model has three states is that saying nothing and
		 * saying never behave differently, and the words have to carry that. */
		const mark = markFor({ restricted: true, restricted_here: true });
		expect(mark).toMatchObject({ kind: 'restricted', icon: 'group_off', here: true });
		expect(mark?.words).toBe('Restricted from guests you chose, set here');
		expect(mark?.rest).toContain('can never see this');
	});

	it('is neither when it is both, because that is two accounts and two answers', () => {
		/* Red would read as "nobody" and the plain ink as "everybody". */
		const mark = markFor({ shared: true, restricted: true, shared_here: true });
		expect(mark?.kind).toBe('both');
		expect(mark?.words).toBe('Shared with some guests and restricted from others, set here');
	});

	it('is hollow when the switch on the file is the one that lost', () => {
		/* A file shared on its own inside a restricted folder: the restrict above is absolute,
		 * so the answer is Restricted and the file's own share decides nothing. */
		const mark = markFor(
			{ shared: false, restricted: true, shared_here: true, restricted_here: false },
			{ file: true }
		);
		expect(mark?.kind).toBe('restricted');
		expect(mark?.here).toBe(false);
		expect(mark?.filled).toBe(false);
		expect(mark?.words).toContain('set on something it is in');
		// The same file with the restrict set on it is the one doing the controlling.
		expect(
			markFor({ restricted: true, restricted_here: true, shared_here: true }, { file: true })?.here
		).toBe(true);
	});

	it('knows when the decision was made further up, and says so by being hollow', () => {
		const mark = markFor({ shared: true }, { file: true });
		expect(mark?.here).toBe(false);
		expect(mark?.filled).toBe(false);
	});

	it('every tooltip says the mark can be pressed, because every mark is a button', () => {
		/* A glyph nobody can name is decoration, and one that turns out to be pressable only if
		 * you try it is worse. */
		for (const facts of [
			{ shared: true },
			{ restricted: true },
			{ shared: true, restricted: true }
		]) {
			expect(markFor(facts)?.rest.endsWith(CLICK_THROUGH)).toBe(true);
		}
	});

	it('splits the status word off, so it can carry its own colour', () => {
		const mark = markFor({ restricted: true });
		expect(mark?.verb).toBe('Restricted');
		expect(mark?.rest).not.toContain('Restricted');
	});

	it('names the mark in the words a person uses, and leaves the press to the button', () => {
		/* The accessible name is read out on every mark a screen reader passes, so it is the
		 * answer and where it is set, and nothing about how the rule is built. */
		for (const facts of [
			{ shared: true },
			{ restricted: true },
			{ shared: true, restricted: true },
			{ shared: true, shared_here: false, restricted_here: false }
		]) {
			const words = markFor(facts)?.words ?? '';
			expect(words).toMatch(/^(Shared|Restricted) /);
			expect(words).toMatch(/, set (here|on something it is in)$/);
			expect(words).not.toMatch(/controll|click|\(/i);
		}
	});
});
