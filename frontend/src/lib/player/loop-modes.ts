/* What happens when a file reaches its end. Three answers, named once.
 *
 * The player asks it of a clip and a Theater cell of its run: the same three answers under the
 * same names. Play through is the default in both, because the two that stop leave somebody
 * pressing every few seconds through a library of short clips; the others are one press away.
 */

export type LoopMode = 'once' | 'loop_one' | 'loop_all';

/*
 * The order the one control cycles through, and the order any menu offers: off, the whole run,
 * this one file, as every repeat button does, so one press from the default never lands on the
 * rarest. See `loopModeIcon`.
 */
export const LOOP_MODES: readonly LoopMode[] = ['once', 'loop_all', 'loop_one'];

/** Play through: the answer before the account's arrives, and the server's own default. */
export const DEFAULT_LOOP_MODE: LoopMode = 'loop_all';

/*
 * The phone names an answer by its place in this order, since a Remote command carries a number.
 * A place outside the order is nobody's answer.
 */
export function loopModeAt(place: number | null): LoopMode | null {
	return place === null ? null : (LOOP_MODES[place] ?? null);
}

/** Whether a stored value is one of ours. A setting read back from an older version may not be. */
export function isLoopMode(value: unknown): value is LoopMode {
	return typeof value === 'string' && (LOOP_MODES as readonly string[]).includes(value);
}

/** The next answer the one control moves to. */
export function nextLoopMode(mode: LoopMode): LoopMode {
	return LOOP_MODES[(LOOP_MODES.indexOf(mode) + 1) % LOOP_MODES.length];
}

/** What each answer is called, wherever it is shown. */
export function loopModeLabel(mode: LoopMode): string {
	if (mode === 'once') return 'Stop at the end';
	return mode === 'loop_one' ? 'Repeat this' : 'Play through';
}

/*
 * The glyph for each, and there are only TWO of them, for three answers: "Stop at the end" is the
 * repeat arrows unlit (`loopRepeats`), since a stop glyph would read as a button that stops now.
 */
export function loopModeIcon(mode: LoopMode): 'repeat_one' | 'repeat' {
	return mode === 'loop_one' ? 'repeat_one' : 'repeat';
}

/** Whether the control is lit: whether anything repeats at all. One fact, asked once. */
export function loopRepeats(mode: LoopMode): boolean {
	return mode !== 'once';
}

/**
 * Why a picture's Play is dimmed in a run: it plays through on one answer of what happens at the
 * end, and rests on the others.
 */
export const PLAYS_THROUGH_ONLY = 'A picture plays through only on Play through';

/** Why it is dimmed on Play through itself: the account leaves pictures out of a run. */
export const PICTURES_LEFT_OUT =
	'A picture plays through only with Include photos on a playthrough';
