/*
 * The units a number can be SHOWN in. The stored value never changes unit, so the server cannot be
 * confused; the unit chosen is not remembered. A ladder only where the stored unit is hard to read
 * at a glance.
 */

import { BYTE_UNITS } from '$lib/library/facts';

export interface Rung {
	unit: string;
	/** Multiply a reading in this unit by this to get the stored value. */
	per: number;
	places: number;
}

/*
 * Every byte rung, DERIVED from `facts.ts`: `check_one_facts_definition.js` reads this file,
 * comments included, and refuses a byte-unit name spelled here. Two rungs each.
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
	/* Separate ladders: milliseconds are never usefully read in hours. */
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
	/* A rate, not the byte ladder, which spells its kilo rung differently. */
	'KB/s': [
		{ unit: 'KB/s', per: 1, places: 0 },
		{ unit: 'MB/s', per: 1000, places: 2 }
	],
	/* Powers of ten, as `facts` decided. */
	...BYTE_LADDERS
};

export function ladderFor(unit: string | undefined | null): Rung[] {
	if (!unit) return [];
	return LADDERS[unit] ?? [];
}

/**
 * A field opens on the DECLARED unit, always: a column of fields each choosing its own unit from
 * its own number cannot be read down.
 */
export function openingRung(rungs: Rung[]): Rung {
	if (rungs.length === 0) throw new Error('no rungs');
	return rungs[0];
}

export function toRung(value: number, rung: Rung): number {
	if (rung.per === 1) return value;
	const scaled = value / rung.per;
	const factor = 10 ** rung.places;
	return Math.round(scaled * factor) / factor;
}

/** Rounded, not truncated, so a small number on a large rung does not become zero. */
export function fromRung(reading: number, rung: Rung): number {
	if (rung.per === 1) return Math.round(reading);
	return Math.round(reading * rung.per);
}
