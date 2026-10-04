/* Which control a setting gets, decided from what the setting itself declares.
 *
 * The one place a settings control is chosen, so no pane can draw a number as a switch. The order
 * is the rule:
 *
 *   choices          -> a menu, named by the option names the setting declares
 *   boolean default  -> a toggle
 *   number, unit %   -> a slider with its number beside it
 *   number           -> a number field, with its unit as a suffix
 *   string like HH:MM -> a clock, typed one segment at a time
 *   string default   -> a text field
 *
 * Choices win over the default's type, since a fixed set is a fixed set. A percentage is the one
 * number tuned by feel, so the one slider. A type nothing here knows is `unknown`, said on the row,
 * never drawn as the nearest thing.
 */

import type { SettingEntry } from '$lib/settings-ui/settings';

type ControlKind = 'menu' | 'toggle' | 'slider' | 'number' | 'time' | 'text' | 'unknown';

/** The suffix that means "this is a proportion", and so the one number that gets a slider. */
export const PERCENT = '%';

/*
 * A clock time, read off the DEFAULT's shape (two digits, a colon, two digits) rather than a list
 * of keys the server owns, so a text box never accepts `11pm`.
 */
const CLOCK = /^([01]\d|2[0-3]):[0-5]\d$/;

export function controlFor(entry: SettingEntry): ControlKind {
	if (entry.choices !== undefined && entry.choices !== null) return 'menu';

	/* The DEFAULT decides the type: a stored value may be absent or an older version's leftover,
	   while the default is declared beside its validator. */
	const shape = entry.default ?? entry.value;
	if (typeof shape === 'boolean') return 'toggle';
	if (typeof shape === 'number') return entry.unit === PERCENT ? 'slider' : 'number';
	if (typeof shape === 'string') return CLOCK.test(shape) ? 'time' : 'text';
	return 'unknown';
}

/** The options for a menu, in the order they were declared, under the names they were given. */
export function optionsFor(entry: SettingEntry): { value: string; label: string }[] {
	const values = entry.choices ?? [];
	const names = entry.choice_labels ?? [];
	return values.map((value, index) => ({
		value: String(value),
		/* Reached only with an older server, which registered choices without names: the stored
		   word shows plainly that something is out of step. */
		label: names[index] ?? String(value)
	}));
}

/**
 * The declared choice a picked option stands for, in the TYPE it was declared in.
 *
 * A menu speaks strings, and the server validates against the choices as declared, so `"720"`
 * would be refused for a number choice and the row would snap back. The way back is decided here,
 * beside the way there. Undefined for a string that names no choice: the caller sends nothing.
 */
export function choiceFor(entry: SettingEntry, picked: string): unknown {
	return (entry.choices ?? []).find((choice) => String(choice) === picked);
}
