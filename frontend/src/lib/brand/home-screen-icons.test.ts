/* A home-screen icon is the dark tile in both lists (`apple-touch-icon` and the manifest): each
 * file's corner pixel must be the charcoal ground. */
import { describe, expect, it } from 'vitest';
import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import { inflateSync } from 'node:zlib';

const FRONTEND = join(__dirname, '..', '..', '..');
const STATIC = join(FRONTEND, 'static');

/** The charcoal every tile is drawn on (`CHARCOAL` in `scripts/brand-icons.mjs`). */
const CHARCOAL = [0x0f, 0x11, 0x15];

/** A PNG's first pixel: every row filter predicts it from zero, so its bytes are the pixel. */
function cornerPixel(file: string): number[] {
	const png = readFileSync(file);
	expect(png.subarray(1, 4).toString('ascii'), `${file} is not a PNG`).toBe('PNG');
	const parts: Buffer[] = [];
	let colourType = -1;
	for (let at = 8; at < png.length;) {
		const length = png.readUInt32BE(at);
		const kind = png.subarray(at + 4, at + 8).toString('ascii');
		const body = png.subarray(at + 8, at + 8 + length);
		if (kind === 'IHDR') colourType = body[9];
		if (kind === 'IDAT') parts.push(body);
		at += 12 + length;
	}
	// 2 is RGB and 6 is RGBA: the only two the generator writes.
	expect([2, 6], `${file} has colour type ${colourType}`).toContain(colourType);
	const rows = inflateSync(Buffer.concat(parts));
	return [rows[1], rows[2], rows[3]];
}

function fromStatic(href: string): string {
	return join(STATIC, ...href.replace(/^\//, '').split('/'));
}

function expectDark(href: string): void {
	expect(cornerPixel(fromStatic(href)), `${href} is not the dark tile`).toEqual(CHARCOAL);
}

describe('the home-screen icons', () => {
	it('Safari reads the apple-touch-icon, and it is the dark tile', () => {
		const page = readFileSync(join(FRONTEND, 'src', 'app.html'), 'utf8');
		const link = page.match(/<link rel="apple-touch-icon" href="([^"]+)"/);
		expect(link, 'app.html names no apple-touch-icon').not.toBeNull();
		expectDark(link?.[1] ?? '');
	});

	it('every icon the manifest names is the dark tile', () => {
		const manifest = JSON.parse(readFileSync(join(STATIC, 'manifest.webmanifest'), 'utf8')) as {
			icons: { src: string; purpose?: string }[];
		};
		expect(manifest.icons.length).toBeGreaterThan(0);
		for (const icon of manifest.icons) expectDark(icon.src);
	});
});
