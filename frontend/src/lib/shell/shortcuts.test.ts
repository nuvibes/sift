/*
 * The one declaration of every shortcut, and the matching that reads it.
 *
 * What is worth proving here is not that a comparison works. It is the three properties the app
 * depends on: that a press reaches exactly one shortcut and not its neighbour, that the modifier
 * rules are the ones each call site needs, and that anything showing a LIST is showing the same
 * declarations the keys are read from, which is the whole reason this exists.
 */

import { describe, expect, it } from 'vitest';

import { MOST_CELLS } from '../theater/layouts';

import {
	AREAS,
	REMOTE_ACTIONS,
	SHORTCUTS,
	commanded,
	matches,
	offerable,
	pressed,
	shortcut,
	shortcutsByArea,
	shortcutsIn,
	typingInto,
	type Actions,
	type PlayerAction,
	type ShortcutId
} from './shortcuts';

function press(key: string, held: Partial<KeyboardEventInit> = {}, target?: EventTarget) {
	const event = new KeyboardEvent('keydown', { key, ...held });
	if (target) Object.defineProperty(event, 'target', { value: target });
	return event;
}

function box(tag: string): HTMLElement {
	return document.createElement(tag);
}

describe('the declarations themselves', () => {
	it('gives every shortcut a name of its own', () => {
		const ids = SHORTCUTS.map((one) => one.id);

		expect(new Set(ids).size).toBe(ids.length);
	});

	it('says what every one of them does, and where', () => {
		for (const one of SHORTCUTS) {
			expect(one.does.length, one.id).toBeGreaterThan(0);
			expect(one.shown.length, one.id).toBeGreaterThan(0);
			expect(one.keys.length, one.id).toBeGreaterThan(0);
			expect(AREAS).toContain(one.area);
		}
	});

	it('refuses a name nothing is declared under', () => {
		expect(() => shortcut('nonsense' as ShortcutId)).toThrow('no shortcut is declared');
	});
});

describe('which press is which shortcut', () => {
	it('does not fire a bare key while a modifier is held', () => {
		expect(matches(press('m'), 'player.mute')).toBe(true);
		expect(matches(press('m', { ctrlKey: true }), 'player.mute')).toBe(false);
		expect(matches(press('m', { altKey: true }), 'player.mute')).toBe(false);
	});

	it('does not fire a Ctrl shortcut without Ctrl', () => {
		expect(matches(press('l', { ctrlKey: true }), 'app.lock')).toBe(true);
		expect(matches(press('l'), 'app.lock')).toBe(false);
	});

	it('moves the search switch with Ctrl and an arrow, from inside the field, never with Shift', () => {
		const field = box('input');

		expect(matches(press('ArrowLeft', { ctrlKey: true }, field), 'search.byMeaning')).toBe(true);
		expect(matches(press('ArrowRight', { ctrlKey: true }, field), 'search.forWords')).toBe(true);
		expect(matches(press('ArrowLeft', { ctrlKey: true }, field), 'search.forWords')).toBe(false);
		expect(
			matches(press('ArrowLeft', { ctrlKey: true, shiftKey: true }, field), 'search.byMeaning'),
			"Ctrl + Shift + an arrow selects a word at a time, and stays the browser's."
		).toBe(false);
		expect(matches(press('ArrowLeft', {}, field), 'search.byMeaning')).toBe(false);
		expect(
			matches(press('ArrowLeft', { ctrlKey: true }), 'view.previous'),
			'The file before this one is a bare arrow, so the two never meet.'
		).toBe(false);
	});

	it('takes Cmd for Ctrl, because a Mac keyboard has no other answer', () => {
		expect(matches(press('l', { metaKey: true }), 'app.lock')).toBe(true);
	});

	it('tells undo from redo, which differ only by Shift', () => {
		expect(matches(press('z', { ctrlKey: true }), 'select.undo')).toBe(true);
		expect(matches(press('z', { ctrlKey: true }), 'select.redo')).toBe(false);
		expect(matches(press('Z', { ctrlKey: true, shiftKey: true }), 'select.redo')).toBe(true);
		expect(matches(press('Z', { ctrlKey: true, shiftKey: true }), 'select.undo')).toBe(false);
	});

	/*
	 * The two mutes are told apart by CTRL, not by the letter.
	 *
	 * `m` and `Shift + M` is the shape every "one and all of them" pair in this app takes, except
	 * that Theater's `m` is also the modifier for the volume keys, and a shortcut whose plain form
	 * is a HELD key cannot also have a shifted form somebody is meant to reach while holding it.
	 * Ctrl is what the rest of Theater uses for "all of them", beside Ctrl + Space.
	 */
	it("tells Theater's two mutes apart by whether Ctrl is held", () => {
		expect(matches(press('m'), 'theater.mute')).toBe(true);
		expect(matches(press('m', { ctrlKey: true }), 'theater.mute')).toBe(false);
		expect(matches(press('m', { ctrlKey: true }), 'theater.muteAll')).toBe(true);
		expect(matches(press('M'), 'theater.mute'), 'the shifted letter is the same key').toBe(true);
	});

	/*
	 * The arrows mean one thing on their own and another with Ctrl, and the pair must never both
	 * answer: five seconds and a whole file are not a difference somebody can undo by pressing again.
	 */
	it("tells Theater's steps apart by whether Ctrl is held", () => {
		expect(matches(press('ArrowRight'), 'theater.forward')).toBe(true);
		expect(matches(press('ArrowRight'), 'theater.next')).toBe(false);
		expect(matches(press('ArrowRight', { ctrlKey: true }), 'theater.next')).toBe(true);
		expect(matches(press('ArrowRight', { ctrlKey: true }), 'theater.forward')).toBe(false);
	});

	it('tells the two holds apart the same way', () => {
		expect(matches(press(' '), 'theater.pause')).toBe(true);
		expect(matches(press(' '), 'theater.pauseAll')).toBe(false);
		expect(matches(press(' ', { ctrlKey: true }), 'theater.pauseAll')).toBe(true);
	});

	it('takes both spellings of the space bar', () => {
		expect(matches(press(' '), 'player.playPause')).toBe(true);
		expect(matches(press('Spacebar'), 'player.playPause')).toBe(true);
	});

	it('answers to any of the nine cell keys, and to nothing else', () => {
		expect(matches(press('1'), 'theater.cell')).toBe(true);
		expect(matches(press('9'), 'theater.cell')).toBe(true);
		expect(matches(press('0'), 'theater.cell')).toBe(false);
	});
});

describe('somebody typing is typing, not pressing a shortcut', () => {
	it.each(['INPUT', 'TEXTAREA', 'SELECT'])('holds off inside a %s', (tag) => {
		expect(matches(press('m', {}, box(tag)), 'player.mute')).toBe(false);
	});

	it('holds off inside anything editable, which no tag name says', () => {
		const editable = box('DIV');
		editable.contentEditable = 'true';
		Object.defineProperty(editable, 'isContentEditable', { value: true });

		expect(typingInto(editable)).toBe(true);
		expect(matches(press('m', {}, editable), 'player.mute')).toBe(false);
	});

	it('fires the locks anyway, because a lock has to work from inside a search box', () => {
		const typing = box('INPUT');

		expect(matches(press('l', { ctrlKey: true }, typing), 'app.lock')).toBe(true);
		expect(matches(press('h', { ctrlKey: true }, typing), 'app.shutHidden')).toBe(true);
		expect(matches(press('L', { ctrlKey: true, shiftKey: true }, typing), 'vault.panicLock')).toBe(
			true
		);
	});

	it('is not confused by a target that is not an element at all', () => {
		expect(typingInto(null)).toBe(false);
		expect(matches(press('m'), 'player.mute')).toBe(true);
	});
});

describe('the numbers a declaration writes out', () => {
	it('offers exactly as many cell keys as a wall can hold', () => {
		/*
		 * The one number in these declarations that is a copy of something else, and it is written
		 * out in three places: the ceiling itself, the pattern the keys are tested against, and the
		 * "1 to 9" a list prints. That is the shape this whole module exists to stop, and the gate
		 * cannot see it: a key nobody can reach is not an UNDECLARED shortcut, it is a declared
		 * one that has quietly stopped being true. A hand-written "1 to 4" beside code taking 1 to
		 * 9 is the same fault the other way round.
		 */
		const cells = shortcut('theater.cell');

		expect(cells.keys).toHaveLength(MOST_CELLS);
		expect(cells.keys[cells.keys.length - 1]).toBe(String(MOST_CELLS));
		expect(cells.shown).toContain(String(MOST_CELLS));
	});
});

describe('what a list of them is built from', () => {
	it('is the same declarations the keys are read from', () => {
		/*
		 * The load-bearing one: what a list shows has to come out of the same place `matches`
		 * reads. A hand-written list beside the code that reads the keys drifts ("1 to 4" while
		 * the code takes 1 to 9).
		 */
		for (const one of shortcutsIn('Theater')) {
			expect(SHORTCUTS).toContain(one);
			/* Every modifier the declaration names, not only shift. Pressing shift alone would
			   press a shortcut declaring `ctrl` WITHOUT it, and the check that a declaration
			   answers to its own key would silently stop covering those. */
			const held = { shiftKey: one.shift === true, ctrlKey: one.ctrl === true };
			expect(matches(press(one.keys[0], held), one.id), one.id).toBe(true);
		}
	});

	it('groups every declared shortcut into exactly one area', () => {
		const grouped = shortcutsByArea().flatMap((group) => group.shortcuts);

		expect(grouped).toHaveLength(SHORTCUTS.length);
	});

	it('returns the declared areas that have keys, in the declared order', () => {
		const areas = shortcutsByArea().map((group) => group.area);

		expect(areas).toEqual(AREAS.filter((area) => shortcutsIn(area).length > 0));
		expect(areas.every((area) => shortcutsIn(area).length > 0)).toBe(true);
	});

	it('leaves out an area with nothing in it, rather than drawing a heading over nothing', () => {
		/*
		 * THE GUARD AGAINST AN EMPTY AREA, checked directly.
		 *
		 * All five declared areas have keys, so against the real list removing the `.filter`
		 * changes nothing, and the moment the filter matters is the moment an area is declared
		 * before its keys are: a heading over an empty list.
		 *
		 * `shortcutsByArea` takes the list it groups, defaulting to the real one. Handed a list
		 * missing an area entirely, the answer must be missing that area too.
		 */
		const withoutTheater = SHORTCUTS.filter((one) => one.area !== 'Theater');
		const areas = shortcutsByArea(withoutTheater).map((group) => group.area);

		expect(AREAS, 'Theater is not a declared area, so this proves nothing').toContain('Theater');
		expect(areas).not.toContain('Theater');
		expect(areas).toEqual(AREAS.filter((area) => area !== 'Theater'));
	});
});

describe('one table of actions, answering the keyboard and the phone', () => {
	function aTable(heard: string[], holding: { m: boolean }): Actions<PlayerAction> {
		return {
			'player.louder': ({ key }) => {
				if (key === null || !holding.m) return false;
				heard.push('louder');
				return true;
			},
			'player.back': ({ key, value }) => {
				heard.push(key === null ? `back ${value}` : 'back');
				return true;
			},
			'player.volumeTo': ({ value }) => {
				heard.push(`volume ${value}`);
				return true;
			}
		};
	}

	it('asks the rows in the order written, so a shared key goes to the first that acts', () => {
		const heard: string[] = [];
		const holding = { m: true };
		// Cancelable, as a real key press is: an event that is not ignores `preventDefault`.
		const up = press('ArrowUp', { cancelable: true });
		expect(pressed(up, aTable(heard, holding))).toBe(true);
		expect(up.defaultPrevented).toBe(true);

		holding.m = false;
		const upAgain = press('ArrowUp', { cancelable: true });
		expect(pressed(upAgain, aTable(heard, holding))).toBe(false);
		expect(upAgain.defaultPrevented).toBe(false);
		expect(heard).toEqual(['louder']);
	});

	it('never asks a row that has no key for a key press', () => {
		const heard: string[] = [];
		expect(pressed(press('ArrowLeft'), aTable(heard, { m: false }))).toBe(true);
		expect(heard).toEqual(['back']);
	});

	it('answers a command by its row, with the value the phone sent', () => {
		const heard: string[] = [];
		expect(commanded(aTable(heard, { m: false }), 'player.volumeTo', 40)).toBe(true);
		expect(commanded(aTable(heard, { m: false }), 'player.back', 10)).toBe(true);
		expect(heard).toEqual(['volume 40', 'back 10']);
	});

	it('answers a verb it has never heard of with false rather than a guess', () => {
		expect(commanded(aTable([], { m: false }), 'player.somethingNew', null)).toBe(false);
	});

	it('offers the phone only the rows the phone has a name for', () => {
		expect(offerable(aTable([], { m: false }))).toEqual(['player.back', 'player.volumeTo']);
	});

	it('names every remote verb as a surface verb, shortcut or not', () => {
		const declared = new Set<string>(SHORTCUTS.map((one) => one.id));
		const withoutKeys = REMOTE_ACTIONS.filter((action) => !declared.has(action));
		// The phone's own presses: a slider's value, the two the still viewer offers that the
		// keyboard answers through the record instead, and the drawers' presses that have no letter
		// and the wall's two lists.
		expect(withoutKeys).toEqual([
			'player.seekTo',
			'player.volumeTo',
			'player.favorite',
			'player.count',
			'player.saveLoop',
			'player.clip',
			'player.random',
			'player.quality',
			'theater.seekTo',
			'theater.volumeTo',
			'theater.shuffle',
			'theater.saveLoop',
			'theater.random',
			'theater.quality',
			'theater.solo',
			'theater.timer',
			'theater.layout',
			'theater.preset'
		]);
	});
});
