/* What happens when a file reaches its end, named once for the player and a Theater cell. */

export type LoopMode = 'once' | 'loop_one' | 'loop_all';

/* Off, the whole run, this one file, as every repeat button cycles. */
export const LOOP_MODES: readonly LoopMode[] = ['once', 'loop_all', 'loop_one'];

export const DEFAULT_LOOP_MODE: LoopMode = 'loop_all';

/* A Remote command names an answer by its place in this order. */
export function loopModeAt(place: number | null): LoopMode | null {
	return place === null ? null : (LOOP_MODES[place] ?? null);
}

/** A setting read back from an older version may not be. */
export function isLoopMode(value: unknown): value is LoopMode {
	return typeof value === 'string' && (LOOP_MODES as readonly string[]).includes(value);
}

export function nextLoopMode(mode: LoopMode): LoopMode {
	return LOOP_MODES[(LOOP_MODES.indexOf(mode) + 1) % LOOP_MODES.length];
}

export function loopModeLabel(mode: LoopMode): string {
	if (mode === 'once') return 'Stop at the end';
	return mode === 'loop_one' ? 'Repeat this' : 'Play through';
}

/* Two glyphs for three answers: stopping is the repeat arrows unlit, never a stop glyph. */
export function loopModeIcon(mode: LoopMode): 'repeat_one' | 'repeat' {
	return mode === 'loop_one' ? 'repeat_one' : 'repeat';
}

export function loopRepeats(mode: LoopMode): boolean {
	return mode !== 'once';
}

export const PLAYS_THROUGH_ONLY = 'A picture plays through only on Play through';

export const PICTURES_LEFT_OUT =
	'A picture plays through only with Include photos on a playthrough';
