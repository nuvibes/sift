/*
 * Trim and Create GIF at a phone's width: not offered. Both are the editor's timeline, a strip of
 * frames with two handles dragged to the frame, and a finger over a strip that narrow covers the
 * very frame it is choosing. A picture's Modify and every file's Compress are another question and
 * stay; the phone's width is the one thing that differs between the two halves of each case.
 */
import { afterEach, describe, expect, it } from 'vitest';
import { phoneWidth } from '$lib/components/common/phone-width.svelte';
import { clipEditsOffered, fileVerbs } from './verbs';

const NOTHING = () => undefined;
const A_CLIP = { media_type: 'video', favorite: false, concealed: false };
const A_PHOTOGRAPH = { media_type: 'image', favorite: false, concealed: false };

/* Every handler present, so a row is left out only by the rule under test. */
const HANDLERS = new Proxy({}, { get: () => NOTHING }) as Parameters<
	typeof fileVerbs
>[0]['handlers'];
const SAVING = {
	label: (_type: string, count: number) => (count === 1 ? 'Save' : 'Save all'),
	icon: () => 'download' as const
};

function ids(subject: typeof A_CLIP): string[] {
	return fileVerbs(
		{
			isAdmin: true,
			canSave: true,
			showingHidden: false,
			count: 1,
			canMove: true,
			canCompress: true,
			subject,
			handlers: HANDLERS
		},
		SAVING
	).map((verb) => verb.id);
}

afterEach(() => {
	phoneWidth.yes = false;
});

describe('cutting a clip on a phone', () => {
	it('is offered on a desktop window, Trim and Create GIF both', () => {
		expect(clipEditsOffered()).toBe(true);
		expect(ids(A_CLIP)).toEqual(expect.arrayContaining(['edit', 'gif']));
	});

	it('is not offered at a phone width: the clip gets Compress instead of Trim, and no GIF', () => {
		phoneWidth.yes = true;
		expect(clipEditsOffered()).toBe(false);
		const offered = ids(A_CLIP);
		expect(offered).not.toContain('edit');
		expect(offered).not.toContain('gif');
		expect(offered).toContain('compress');
	});

	it("leaves a picture's Modify alone at a phone width", () => {
		phoneWidth.yes = true;
		expect(ids(A_PHOTOGRAPH)).toContain('edit');
	});
});
