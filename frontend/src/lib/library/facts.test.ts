import { describe, expect, it } from 'vitest';
import { codec, depth, dimensions, length, rate, size, sizeOf } from './facts';

/* The one definition of a file's facts. Two things are being checked and the second is the point
 * of the file. */

describe('a size on disk', () => {
	it('is written in whichever unit keeps it to three or four figures', () => {
		expect(size(999)).toBe('999 B');
		expect(size(1000)).toBe('1.0 kB');
		expect(size(1_500_000)).toBe('1.5 MB');
		expect(size(6_000_000_000)).toBe('6.0 GB');
		expect(size(45_000_000_000)).toBe('45 GB');
		expect(size(2_500_000_000_000)).toBe('2.5 TB');
	});

	it('divides by a thousand, because that is what the units it writes mean', () => {
		// The one that matters: dividing by 1024 and writing `GB` anyway would make this file read
		// 5.6 GB in one panel and 6.0 GB on the record below it.
		expect(size(6_000_000_000)).toBe('6.0 GB');
	});

	it('stops at terabytes rather than inventing a unit nobody uses', () => {
		expect(size(9_000_000_000_000_000)).toBe('9000 TB');
	});

	it('says nothing about a size it does not have', () => {
		expect(size(null)).toBeNull();
		expect(size(undefined)).toBeNull();
		expect(size(-1)).toBeNull();
		expect(size(Number.NaN)).toBeNull();
	});

	it('draws an empty file as a size, because that is a fact and not a blank', () => {
		expect(size(0)).toBe('0 B');
	});
});

describe('a size on its way to a total', () => {
	it('keeps one decimal however large, so the figure moves to the last tenth', () => {
		expect(sizeOf(11_030_000_000, 11_530_000_000)).toBe('11.0 GB of 11.5 GB');
		expect(sizeOf(11_530_000_000, 11_530_000_000)).toBe('11.5 GB of 11.5 GB');
		expect(sizeOf(123_456_000_000, 250_000_000_000)).toBe('123.4 GB of 250.0 GB');
		// Where `size` drops the tenth, the moving figure keeps it.
		expect(size(11_030_000_000)).toBe('11 GB');
	});

	it("says both figures in the total's unit", () => {
		expect(sizeOf(400_000_000, 11_500_000_000)).toBe('0.4 GB of 11.5 GB');
		expect(sizeOf(0, 2_500_000)).toBe('0.0 MB of 2.5 MB');
		expect(sizeOf(300, 900)).toBe('300 B of 900 B');
	});

	it('says nothing for a total or a figure it cannot read', () => {
		expect(sizeOf(10, null)).toBeNull();
		expect(sizeOf(null, 10)).toBeNull();
		expect(sizeOf(-1, 10)).toBeNull();
	});
});

describe('a length', () => {
	it('shows hours only when there are some', () => {
		expect(length(247_000)).toBe('4:07');
		expect(length(5_400_000)).toBe('1:30:00');
	});

	it('pads the minutes once there is an hour in front of them and not before', () => {
		expect(length(3_660_000)).toBe('1:01:00');
		expect(length(61_000)).toBe('1:01');
	});

	it('rounds to the nearest second', () => {
		expect(length(1_600)).toBe('0:02');
	});

	it('says nothing about a length it does not have', () => {
		expect(length(null)).toBeNull();
		expect(length(0)).toBeNull();
		expect(length(-5)).toBeNull();
	});
});

describe('a frame rate', () => {
	it('keeps two places and drops a trailing zero on a whole number', () => {
		expect(rate(29.97)).toBe('29.97 fps');
		expect(rate(30)).toBe('30 fps');
		expect(rate(23.976)).toBe('23.98 fps');
	});

	it('says nothing about a rate it does not have', () => {
		expect(rate(null)).toBeNull();
		expect(rate(0)).toBeNull();
	});
});

describe('a shape', () => {
	it('is both halves, in pixels', () => {
		expect(dimensions(1920, 1080)).toBe('1920 x 1080');
	});

	it('needs both halves', () => {
		expect(dimensions(1920, null)).toBeNull();
		expect(dimensions(null, 1080)).toBeNull();
		expect(dimensions(0, 0)).toBeNull();
	});
});

describe('an encoder', () => {
	it('is named the way the person who chose it knows it', () => {
		expect(codec('h264')).toBe('H.264');
		expect(codec('avc1')).toBe('H.264');
		expect(codec('hevc')).toBe('H.265');
		expect(codec('av1')).toBe('AV1');
		expect(codec('aac')).toBe('AAC');
	});

	it('does not care how ffprobe spelled it', () => {
		expect(codec('H264')).toBe('H.264');
		expect(codec(' h264 ')).toBe('H.264');
	});

	it('passes through a name nobody here anticipated', () => {
		// More use than "Unknown": a codec this table has never heard of is still readable.
		expect(codec('dirac')).toBe('dirac');
	});

	it('says nothing about an encoder it does not have', () => {
		expect(codec(null)).toBeNull();
		expect(codec('')).toBeNull();
		expect(codec('   ')).toBeNull();
	});
});

describe('a bit depth', () => {
	it('carries its unit', () => {
		expect(depth(8)).toBe('8-bit');
		expect(depth(10)).toBe('10-bit');
	});

	it('treats zero as not knowing', () => {
		// Zero is what a file that could not be read is written down as, not a picture with no
		// colour in it.
		expect(depth(0)).toBeNull();
		expect(depth(null)).toBeNull();
	});
});
