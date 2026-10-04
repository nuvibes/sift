/*
 * The units a number can be SHOWN in, and how to convert between them.
 *
 * ## What this does not change
 *
 * The stored value. A setting declares one unit in the registry and that is what is written to the
 * database for ever: `download.bandwidth_kbps` holds kilobytes per second whatever the box on
 * screen says. This module only decides what the reader is shown and what their typing means.
 *
 * That is deliberate and it is the whole reason a unit menu is cheap. The alternative (storing
 * the number and its unit together) is a migration, a second field on every numeric setting, a
 * validator that has to know both, and a class of bug where two clients disagree about what a
 * stored 5 means. Here the server is not told anything and cannot be confused.
 *
 * ## Why the unit is not remembered
 *
 * Because remembering it is 145 new settings, one per numeric field, to record a preference nobody
 * has. A field opens on the unit its setting is DECLARED in (see `openingRung` for why that is
 * not the largest unit that fits), and a reader who picks a different one keeps it until they
 * leave the screen.
 *
 * ## The rule for adding one
 *
 * A ladder is only worth having where the canonical unit produces numbers people cannot read at a
 * glance: four-digit kilobytes, five-digit megabytes, thousands of milliseconds. A percentage has
 * no ladder because there is nothing above it, and neither does a count of files.
 */

import { BYTE_UNITS } from '$lib/library/facts';

/** One rung: what it is called, and how many canonical units make one of it. */
export interface Rung {
	unit: string;
	/** Multiply a reading in this unit by this to get the canonical value. */
	per: number;
	/** How many decimal places are worth typing here. The canonical rung is always whole numbers. */
	places: number;
}

/*
 * The ladders, keyed by the unit a setting DECLARES.
 *
 * The first rung of each is the canonical one and its `per` is always 1: a ladder whose bottom
 * rung converted would be a ladder that cannot represent the stored value exactly.
 */
/**
 * Every byte rung, and the one above it, from the one place those names are written down.
 *
 * DERIVED rather than listed, and not only for tidiness: `check_one_facts_definition.js` refuses a
 * quoted byte-unit name anywhere but `facts.ts`, because a second ladder of them lets one file read
 * one figure on one surface and a different one two inches below it. Passing a unit name as an
 * argument still spells one here and the gate refuses it, rightly. So no byte unit is named in
 * this file at all.
 *
 * THAT GATE READS THE RAW FILE, COMMENTS INCLUDED. A note here quoting the names it forbids
 * fails it exactly as the code would. Describe them; do not spell them.
 *
 * Two rungs each, not the whole ladder: a setting stored in megabytes is worth reading in gigabytes
 * and is not worth reading in terabytes, and a menu whose entries are mostly absurd is a menu
 * somebody has to read past.
 */
const BYTE_LADDERS: Record<string, Rung[]> = Object.fromEntries(
	BYTE_UNITS.slice(0, -1).map((unit, at) => [
		unit,
		[
			{ unit, per: 1, places: 0 },
			{ unit: BYTE_UNITS[at + 1], per: 1000, places: 2 }
		]
	])
);

const LADDERS: Record<string, Rung[]> = {
	/* Time. `ms` and `s` are separate ladders rather than one long one because a setting measured
	   in milliseconds is never also usefully read in hours. */
	ms: [
		{ unit: 'ms', per: 1, places: 0 },
		{ unit: 'sec', per: 1000, places: 2 }
	],
	sec: [
		{ unit: 'sec', per: 1, places: 0 },
		{ unit: 'min', per: 60, places: 1 },
		{ unit: 'hours', per: 3600, places: 1 }
	],
	min: [
		{ unit: 'min', per: 1, places: 0 },
		{ unit: 'hours', per: 60, places: 1 }
	],
	days: [
		{ unit: 'days', per: 1, places: 0 },
		{ unit: 'weeks', per: 7, places: 1 }
	],
	/* A RATE, so it is not the byte ladder: `KB/s` is not one of the byte names (that ladder spells
	   its kilo rung `kB`), and a rate has no rung above MB/s worth offering on a home connection. */
	'KB/s': [
		{ unit: 'KB/s', per: 1, places: 0 },
		{ unit: 'MB/s', per: 1000, places: 2 }
	],
	/* Sizes, spread in from the derivation above. Powers of ten, the way a disk is sold, because
	   that is what `facts` decided. There is no reason to import the GB-versus-GiB argument into
	   a settings screen. */
	...BYTE_LADDERS
};

/** The rungs a declared unit can be shown on, or none if it has no ladder. */
export function ladderFor(unit: string | undefined | null): Rung[] {
	if (!unit) return [];
	return LADDERS[unit] ?? [];
}

/**
 * Which rung a field opens on: the CANONICAL one, always.
 *
 * Not "the largest rung the value sits at or above", which sounds better than it looks: 5000 KB/s
 * would open on MB/s and 60 seconds on 1 min, each field choosing its own unit from its own number.
 *
 * On one field that is helpful. Down a COLUMN of them it is chaos: KB/s, ms, sec and min stacked
 * one under another, none of them the unit the setting is declared in, and one of them ("1 min",
 * for a sixty-second back-off) reading as a different setting from the one the help text describes.
 * A column of numbers is read down, and it can only be read down if the units agree.
 *
 * So the unit is the declared one and the menu is how somebody changes it. Nothing is taken away:
 * anybody who wants MB/s is one press from it, and the pane is legible before they press
 * anything, which is the case that matters far more often.
 */
export function openingRung(rungs: Rung[]): Rung {
	if (rungs.length === 0) throw new Error('no rungs');
	return rungs[0];
}

/** A canonical value, read on a rung. */
export function toRung(value: number, rung: Rung): number {
	if (rung.per === 1) return value;
	const scaled = value / rung.per;
	const factor = 10 ** rung.places;
	return Math.round(scaled * factor) / factor;
}

/**
 * A reading on a rung, back to a canonical whole number.
 *
 * Rounded, not truncated: 1.5 MB/s is 1500 KB/s and 0.0004 GB is 0 MB. Truncating would make a
 * reader who typed a small number on a large rung watch it become zero, which reads as a refusal.
 */
export function fromRung(reading: number, rung: Rung): number {
	if (rung.per === 1) return Math.round(reading);
	return Math.round(reading * rung.per);
}
