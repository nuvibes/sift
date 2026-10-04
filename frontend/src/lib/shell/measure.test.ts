/* The one place a stored measurement becomes a read one. */
import { describe, expect, it } from 'vitest';
import {
	DEFAULT_UNITS,
	height,
	heightBand,
	heightFromParts,
	heightParts,
	unitSystem
} from './measure';

describe('which system a stored answer means', () => {
	it('reads the imperial word and treats everything else as metric', () => {
		expect(unitSystem('imperial')).toBe('imperial');
		expect(unitSystem('metric')).toBe('metric');
	});

	it('falls to feet and inches for an account that has never chosen', () => {
		// An absent row is what a fresh account has, and it must mean what the server's default
		// means rather than leaving the screen with no answer at all.
		expect(unitSystem(undefined)).toBe('imperial');
		expect(unitSystem(null)).toBe('imperial');
		expect(unitSystem('METRIC')).toBe('imperial');
		expect(DEFAULT_UNITS).toBe('imperial');
	});
});

describe('a height', () => {
	it('is the stored centimetres under metric', () => {
		expect(height(175, 'metric')).toBe('175 cm');
	});

	it('is feet and inches under imperial', () => {
		// 175 cm is 68.9 inches, which is 5 ft 9 in to the nearest inch.
		expect(height(175, 'imperial')).toBe('5 ft 9 in');
		expect(height(160, 'imperial')).toBe('5 ft 3 in');
	});

	it('carries into the next foot instead of saying twelve inches', () => {
		// 182.5 cm is 71.85 inches. Rounding the total gives 72, which is six feet; rounding the
		// feet and the inches separately is what produces "5 ft 12 in".
		expect(height(182.5, 'imperial')).toBe('6 ft');
	});

	it('says a whole number of feet without a nought on the end', () => {
		// A nought there reads as a measurement nobody filled in, which is what the dash means.
		expect(height(152.4, 'imperial')).toBe('5 ft');
	});

	it('rounds a fraction that arrived from somewhere else', () => {
		expect(height(174.6, 'metric')).toBe('175 cm');
	});

	it('leaves a value that is not a number alone rather than inventing one', () => {
		// A formatter that answers "0 ft" for a NaN hides the fact that the field is wrong.
		expect(height(Number.NaN, 'imperial')).toBe('NaN');
		expect(height(Number.POSITIVE_INFINITY, 'metric')).toBe('Infinity');
	});
});

describe('a height taken apart and put back', () => {
	it('splits a stored height into the two numbers the editor types', () => {
		expect(heightParts(175)).toEqual({ feet: 5, inches: 9 });
		// A whole number of feet leaves no inches behind, and the carry is the total's.
		expect(heightParts(152.4)).toEqual({ feet: 5, inches: 0 });
		expect(heightParts(182.5)).toEqual({ feet: 6, inches: 0 });
	});

	it('puts a typed pair back as whole centimetres, never as a fraction', () => {
		// The column is an integer and the server files anything else as nothing at all.
		expect(heightFromParts(5, 9)).toBe(175);
		expect(heightFromParts(6, 0)).toBe(183);
		expect(heightFromParts(0, 0)).toBe(0);
		expect(Number.isInteger(heightFromParts(6, 7))).toBe(true);
	});

	it('survives the round trip at every inch, which is what an untouched save must not move', () => {
		// A record opened, saved and opened again has to read the same. Every inch from 4 ft to 7 ft.
		for (let inches = 48; inches <= 84; inches += 1) {
			const feet = Math.floor(inches / 12);
			const rest = inches - feet * 12;
			expect(heightParts(heightFromParts(feet, rest))).toEqual({ feet, inches: rest });
		}
	});

	it('answers nought for a pair that is not a pair of numbers', () => {
		expect(heightFromParts(Number.NaN, 0)).toBe(0);
	});
});

describe('a band of heights', () => {
	it('is the stored band with its unit under metric', () => {
		expect(heightBand('160-169', 'metric')).toBe('160-169 cm');
		expect(heightBand('200+', 'metric')).toBe('200+ cm');
		expect(heightBand('<150', 'metric')).toBe('<150 cm');
	});

	it('is the whole inches the band covers under imperial', () => {
		// 160 cm is 63 inches; 170 cm is 66.93, so the last whole inch inside the band is 66.
		expect(heightBand('160-169', 'imperial')).toBe('5 ft 3 in to 5 ft 6 in');
		expect(heightBand('150-159', 'imperial')).toBe('5 ft to 5 ft 2 in');
		expect(heightBand('190-199', 'imperial')).toBe('6 ft 3 in to 6 ft 6 in');
	});

	it('says an open band as an open one, at both ends', () => {
		expect(heightBand('200+', 'imperial')).toBe('6 ft 7 in and over');
		expect(heightBand('<150', 'imperial')).toBe('4 ft 11 in and under');
	});

	it('leaves no inch in two bands and no inch in none', () => {
		/* The column is read down, so the row above must end one inch below where the next begins.
		   Converting each stored number to the nearest inch instead would put 169 cm and 170 cm both
		   at 5 ft 7 in, and two neighbouring rows would each claim it. */
		const said = ['150-159', '160-169', '170-179', '180-189', '190-199'].map((band) =>
			heightBand(band, 'imperial')
		);
		expect(said).toEqual([
			'5 ft to 5 ft 2 in',
			'5 ft 3 in to 5 ft 6 in',
			'5 ft 7 in to 5 ft 10 in',
			'5 ft 11 in to 6 ft 2 in',
			'6 ft 3 in to 6 ft 6 in'
		]);
	});

	it('draws a band it cannot read as it is stored, so a wrong one can be seen', () => {
		expect(heightBand('tall', 'imperial')).toBe('tall cm');
		expect(heightBand('170-160', 'imperial')).toBe('170-160 cm');
	});

	it('says a band holding one inch once rather than as a range of itself', () => {
		// Not a band the server produces (the ladder is ten centimetres wide), but a band that
		// narrows to one inch must not read "5 ft 3 in to 5 ft 3 in".
		expect(heightBand('160-161', 'imperial')).toBe('5 ft 3 in');
	});
});
