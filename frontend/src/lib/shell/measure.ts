/*
 * Turning a measurement Sift STORES into the one somebody reads.
 *
 * ## Why this is a module and not four lines in the component that draws a height
 *
 * Because it is arithmetic, and arithmetic written where it is drawn is arithmetic written again
 * the next time something draws the same fact. `RecordValue` is the record's reader, the facet
 * panel labels a band of heights, and a hover card may yet say how tall somebody is: three
 * surfaces, one sum. `$lib/library/facts` is the same decision one fact along: a size, a rate and a shape
 * are worked out in one place because the record and the player both draw them.
 *
 * ## `measure`, not `units`, and the difference is a real one
 *
 * `$lib/shell/units` next door is about the unit a NUMBER IS TYPED IN on a settings field (kilobytes or
 * megabytes, seconds or minutes), and it converts between rungs of one ladder. This is about which
 * SYSTEM OF MEASUREMENT an account reads a record in, which is a preference that follows the person
 * rather than a control on one field. Two different questions, so two names: one word per thing.
 *
 * ## What is stored is metric, always
 *
 * `people.height_cm` holds whole centimetres, every stash-box sends centimetres, and every filter
 * and facet in Sift is built on that number. Nothing here writes; the choice is about READING, so
 * a library is exactly as searchable whichever answer somebody picks and two accounts on one
 * install can disagree without anything having to be converted on disk.
 *
 * ## What has an imperial form, and what does not
 *
 * A LENGTH does, and it is the only measured kind a record carries: `height_cm` is its one
 * field. The other measured things in Sift are in units with no imperial form at all: a file size
 * is bytes, a shape is pixels, a duration is seconds, a rate is per second, a bit depth is bits.
 * A person's `measurements` is free text, written as "34B-27-37" by the convention of the thing it
 * describes rather than by Sift, and nothing here parses it, so it is shown exactly as it was
 * typed rather than half-converted into something nobody writes.
 */

/** The two systems a measurement can be read in. The server's own words (see `records/__init__`). */
export type UnitSystem = 'metric' | 'imperial';

/** Where the choice is stored, per account. One spelling, shared by the store and the settings pane. */
export const UNITS_KEY = 'appearance.units';

/** What a fresh install reads in, and what the server's registration says. */
export const DEFAULT_UNITS: UnitSystem = 'imperial';

/** Anything that is not the metric answer is the imperial one, which is what an unset value means. */
export function unitSystem(value: unknown): UnitSystem {
	return value === 'metric' ? 'metric' : 'imperial';
}

/** How many centimetres are in an inch. Exact by definition since 1959, not a measurement. */
const CM_PER_INCH = 2.54;

const INCHES_PER_FOOT = 12;

/** The same constant as whole hundredths, for arithmetic that has to be exact. See `inchAtLeast`. */
const HUNDREDTHS_PER_INCH = 254;

/**
 * A height, from the whole centimetres Sift stores.
 *
 * Imperial is rounded to the nearest INCH rather than carrying a fraction, because that is how a
 * height is said: nobody is 5 ft 9.4 in. The rounding is done once, on the total, and the feet and
 * inches are taken out of that. Rounding each part separately is what produces "5 ft 12 in".
 *
 * Metric is rounded too, for a value that arrived as a fraction from somewhere. The column is whole
 * centimetres, so on Sift's own data this changes nothing and it is not an assumption this has to
 * make.
 *
 * A number that is not one (a NaN, an infinity, something a stash-box sent as a word) is not
 * converted at all: this is a formatter and an invented answer is worse than the raw value, which
 * at least shows that something is wrong with the field.
 */
export function height(cm: number, system: UnitSystem): string {
	if (!Number.isFinite(cm)) return String(cm);
	if (system !== 'imperial') return `${Math.round(cm)} cm`;
	const { feet, inches } = heightParts(cm);
	return saidHeight(feet, inches);
}

/**
 * A height taken APART into feet and whole inches, for a screen that has to draw the two halves
 * separately rather than as one phrase.
 *
 * The editor is why this is exported: under imperial a height is typed into two boxes, and a box
 * holding "5" and another holding "9" cannot be cut out of the string `height` returns. The
 * rounding is the same rounding and it is done in the one place, so the pair somebody TYPES and
 * the phrase somebody READS can never disagree about the same centimetres.
 *
 * Not guarded against a number that is not one: `height` above checks before it calls, and the
 * editor only ever hands this a height it has already read off the draft. A guard here would be a
 * second opinion about what an unfilled field is, in a file whose whole job is arithmetic.
 */
export function heightParts(cm: number): { feet: number; inches: number } {
	const inches = Math.round(cm / CM_PER_INCH);
	const feet = Math.floor(inches / INCHES_PER_FOOT);
	const rest = inches - feet * INCHES_PER_FOOT;
	return { feet, inches: rest };
}

/**
 * A height PUT BACK together, as the whole centimetres Sift stores.
 *
 * The inverse of `heightParts`, and it has to be exact enough to survive the round trip: a record
 * opened at 5 ft 9 in, saved untouched and opened again must still read 5 ft 9 in. It does: a
 * whole centimetre is at most half a centimetre away from the true measurement and that is under a
 * fifth of an inch, so rounding back to the nearest inch always lands on the inch that was typed.
 *
 * Whole centimetres because the column is whole centimetres: the server reads a height with
 * `int(...)` and files a fraction as nothing at all, so a value with a decimal point on it would
 * be a save that silently cleared the field.
 */
export function heightFromParts(feet: number, inches: number): number {
	const total = Math.round(feet) * INCHES_PER_FOOT + Math.round(inches);
	if (!Number.isFinite(total)) return 0;
	return Math.round(total * CM_PER_INCH);
}

/** A number of feet and inches as it is said. "5 ft" on the nose rather than "5 ft 0 in": a nought
 *  there reads as a measurement somebody failed to fill in, which is what the dash on an empty
 *  record row means. */
function saidHeight(feet: number, inches: number): string {
	return inches === 0 ? `${feet} ft` : `${feet} ft ${inches} in`;
}

/*
 * A BAND of heights, as the facet column says it.
 *
 * The People wall cannot list a row per height (almost nobody shares one), so the column counts
 * ten-centimetre bands, and the band is STORED as the filter it writes: `<150`, `160-169`, `200+`.
 * Under metric that is already the answer, with its unit on it. Under imperial it is not: nobody
 * reads a height as a range of centimetres, and "63-66 in" is not how one is said either.
 *
 * ## The band is converted by its EDGES, and they are whole inches
 *
 * `160-169` covers every height from 160 cm up to but not including 170 cm. In inches that is 63
 * up to but not including 66.93, so the whole inches inside it are 63 to 66: "5 ft 3 in to 5 ft
 * 6 in". Taking the edges this way is what keeps the column CONTIGUOUS: the band below ends at
 * 5 ft 2 in and the one above begins at 5 ft 7 in, so every inch belongs to exactly one row.
 *
 * The other way round (converting the two stored numbers and rounding each to the nearest inch)
 * reads better on one row and breaks the column: 169 cm and 170 cm both round to 67 in, so two
 * neighbouring rows would each claim 5 ft 7 in and a reader could not tell which one held them.
 *
 * The price is paid at the top centimetre of a band: somebody 169 cm has 5 ft 7 in on their record
 * and is counted in the row reading "5 ft 3 in to 5 ft 6 in". That is one centimetre per band and
 * it is the honest half of the trade. The alternative is a column whose rows overlap, which is
 * wrong on every row instead of near one edge of each.
 *
 * A band this cannot read is drawn as it is stored, with its unit, exactly as the metric answer is:
 * this is a formatter, and a band nothing claims is a band a reader should be able to SEE rather
 * than one hidden behind invented words.
 */
export function heightBand(band: string, system: UnitSystem): string {
	if (system !== 'imperial') return `${band} cm`;
	const edges = bandEdges(band);
	if (edges === null) return `${band} cm`;
	const { low, high } = edges;
	if (low === null) return `${said(high as number)} and under`;
	if (high === null) return `${said(low)} and over`;
	return low === high ? said(low) : `${said(low)} to ${said(high)}`;
}

/** One edge of a band as a phrase. The same words `height` uses, from the same pair of numbers. */
function said(inches: number): string {
	const feet = Math.floor(inches / INCHES_PER_FOOT);
	return saidHeight(feet, inches - feet * INCHES_PER_FOOT);
}

/**
 * The whole inches a stored band covers, or nothing where it is not a band this knows.
 *
 * `null` at an end means the band is open at that end: `<150` has no bottom and `200+` has no
 * top. The three shapes are the three the server's own `HEIGHT_BAND` can produce and no others.
 */
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

/**
 * The first whole inch that is at least this many centimetres.
 *
 * In hundredths rather than as `Math.ceil(cm / CM_PER_INCH)`, because 2.54 has no exact form in
 * binary and a height that IS a whole number of inches (127 cm is exactly 50) can divide to a
 * hair under or over it. A ceiling on a hair is a whole inch wrong. Both sides here are integers
 * and the division is exact wherever the answer is one, so the ceiling is the true one.
 *
 * The 254 is written out rather than worked out from `CM_PER_INCH`: `2.54 * 100` does come back as
 * exactly 254, but only because the rounding happens to land there, and a constant this depends on
 * being exact should not rest on a product that rounds well.
 */
function inchAtLeast(cm: number): number {
	return Math.ceil((cm * 100) / HUNDREDTHS_PER_INCH);
}
