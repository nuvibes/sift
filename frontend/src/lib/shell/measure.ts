/* A measurement Sift STORES (always metric) as an account reads it; only a length converts. */

/** The server's own words (see `records/__init__`). */
export type UnitSystem = 'metric' | 'imperial';

export const UNITS_KEY = 'appearance.units';

export const DEFAULT_UNITS: UnitSystem = 'imperial';

export function unitSystem(value: unknown): UnitSystem {
	return value === 'metric' ? 'metric' : 'imperial';
}

const CM_PER_INCH = 2.54;

const INCHES_PER_FOOT = 12;

/** As whole hundredths, for exact arithmetic. */
const HUNDREDTHS_PER_INCH = 254;

/** Imperial rounds the TOTAL to the nearest inch, then splits it, or it says "5 ft 12 in". */
export function height(cm: number, system: UnitSystem): string {
	if (!Number.isFinite(cm)) return String(cm);
	if (system !== 'imperial') return `${Math.round(cm)} cm`;
	const { feet, inches } = heightParts(cm);
	return saidHeight(feet, inches);
}

export function heightParts(cm: number): { feet: number; inches: number } {
	const inches = Math.round(cm / CM_PER_INCH);
	const feet = Math.floor(inches / INCHES_PER_FOOT);
	const rest = inches - feet * INCHES_PER_FOOT;
	return { feet, inches: rest };
}

/** Whole centimetres: the server files a fraction as nothing. */
export function heightFromParts(feet: number, inches: number): number {
	const total = Math.round(feet) * INCHES_PER_FOOT + Math.round(inches);
	if (!Number.isFinite(total)) return 0;
	return Math.round(total * CM_PER_INCH);
}

function saidHeight(feet: number, inches: number): string {
	return inches === 0 ? `${feet} ft` : `${feet} ft ${inches} in`;
}

/* A BAND converted by its EDGES, so every inch sits in one row of the facet column. */
export function heightBand(band: string, system: UnitSystem): string {
	if (system !== 'imperial') return `${band} cm`;
	const edges = bandEdges(band);
	if (edges === null) return `${band} cm`;
	const { low, high } = edges;
	if (low === null) return `${said(high as number)} and under`;
	if (high === null) return `${said(low)} and over`;
	return low === high ? said(low) : `${said(low)} to ${said(high)}`;
}

function said(inches: number): string {
	const feet = Math.floor(inches / INCHES_PER_FOOT);
	return saidHeight(feet, inches - feet * INCHES_PER_FOOT);
}

function bandEdges(band: string): { low: number | null; high: number | null } | null {
	const under = /^<(\d+)$/.exec(band);
	if (under) return { low: null, high: inchAtLeast(Number(under[1])) - 1 };
	const over = /^(\d+)\+$/.exec(band);
	if (over) return { low: inchAtLeast(Number(over[1])), high: null };
	const between = /^(\d+)-(\d+)$/.exec(band);
	if (!between) return null;
	const from = Number(between[1]);
	const to = Number(between[2]);
	if (to < from) return null;
	return { low: inchAtLeast(from), high: inchAtLeast(to + 1) - 1 };
}

/** In integer hundredths: 2.54 has no exact binary form. */
function inchAtLeast(cm: number): number {
	return Math.ceil((cm * 100) / HUNDREDTHS_PER_INCH);
}
