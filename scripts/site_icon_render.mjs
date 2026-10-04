// SPDX-License-Identifier: AGPL-3.0-or-later
/*
 * The site-icon build's picture reader: bytes in, straight RGBA pixels out, through a real browser.
 *
 * A MAINTAINER'S HELPER, run only by `scripts/build_site_icons.py`, never by Sift. It exists because
 * the best picture most sites have of themselves is an SVG, and nothing else on the machine a
 * release is cut from can draw one: ffmpeg has no SVG decoder, and there is no rsvg, Inkscape or
 * ImageMagick. Chromium draws SVG correctly by definition (it is what the site itself was drawn in),
 * and it decodes every raster format a site serves (PNG, JPEG, WebP, AVIF, GIF, ICO) through the
 * same one path, so a picture is read the same way whatever it arrived as.
 *
 * ## An SVG is drawn as an IMAGE, which is what makes it safe to draw somebody else's
 *
 * Through an `<img>` element, never inlined into the page: a document drawn as an image runs no
 * script and loads nothing from anywhere, so a hostile SVG off the internet can do nothing but be a
 * picture. The page itself is `about:blank` and the bytes arrive as a blob, so nothing is asked of
 * the network by this process at all: the Python side did the fetching.
 *
 * ## The protocol: one JSON line in, one JSON line out
 *
 *   in:  {"id": 1, "svg": true|false, "data": "<base64>", "long": 1536}
 *   out: {"id": 1, "width": W, "height": H, "rgba": "<base64 of W*H*4 bytes, straight alpha>"}
 *        {"id": 1, "error": "why"}
 *
 *   in:  {"id": 2, "sheet": {"cells": [{"png", "label", "note", "ground", "low"}], "columns", "cell"}}
 *   out: {"id": 2, "png": "<base64 of a PNG>"}   (a contact sheet, for LOOKING at the pack)
 *
 * `long` is the size an SVG's longer side is drawn at; a raster is read at its own size. Usage:
 *
 *   node scripts/site_icon_render.mjs <path to playwright's index.mjs>
 */
import { createInterface } from 'node:readline';
import { pathToFileURL } from 'node:url';

const where = process.argv[2];
if (!where) {
	process.stderr.write('name the playwright index.mjs to load\n');
	process.exit(2);
}
const { chromium } = await import(pathToFileURL(where).href);
const browser = await chromium.launch();
const page = await browser.newPage();

/* Runs in the page. Everything it needs arrives as arguments; it touches no network. */
async function draw({ svg, data, long }) {
	const bytes = Uint8Array.from(atob(data), (c) => c.charCodeAt(0));
	let blob;
	let width = 0;
	let height = 0;
	if (svg) {
		/* An SVG says how big it is in three different ways or none. Read its viewBox (or its
		 * width/height), then WRITE the size wanted onto the root, so the image has one answer. */
		const text = new TextDecoder().decode(bytes);
		const doc = new DOMParser().parseFromString(text, 'image/svg+xml');
		const root = doc.documentElement;
		if (!root || root.nodeName.toLowerCase() !== 'svg') throw new Error('not an svg document');
		let vw = 0;
		let vh = 0;
		const box = (root.getAttribute('viewBox') || '').trim().split(/[\s,]+/).map(Number);
		if (box.length === 4 && box[2] > 0 && box[3] > 0) {
			vw = box[2];
			vh = box[3];
		} else {
			vw = parseFloat(root.getAttribute('width') || '0');
			vh = parseFloat(root.getAttribute('height') || '0');
			if (vw > 0 && vh > 0) root.setAttribute('viewBox', `0 0 ${vw} ${vh}`);
		}
		if (!(vw > 0 && vh > 0)) throw new Error('an svg with no size');
		const scale = long / Math.max(vw, vh);
		width = Math.max(1, Math.round(vw * scale));
		height = Math.max(1, Math.round(vh * scale));
		root.setAttribute('width', String(width));
		root.setAttribute('height', String(height));
		root.setAttribute('preserveAspectRatio', 'xMidYMid meet');
		blob = new Blob([new XMLSerializer().serializeToString(doc)], { type: 'image/svg+xml' });
	} else {
		blob = new Blob([bytes]);
	}
	const url = URL.createObjectURL(blob);
	try {
		const img = new Image();
		img.src = url;
		await img.decode();
		if (!svg) {
			width = img.naturalWidth;
			height = img.naturalHeight;
		}
		if (!(width > 0 && height > 0)) throw new Error('a picture with no size');
		const canvas = document.createElement('canvas');
		canvas.width = width;
		canvas.height = height;
		const context = canvas.getContext('2d', { willReadFrequently: true, colorSpace: 'srgb' });
		context.drawImage(img, 0, 0, width, height);
		const pixels = context.getImageData(0, 0, width, height).data;
		let binary = '';
		for (let i = 0; i < pixels.length; i += 0x8000) {
			binary += String.fromCharCode.apply(null, pixels.subarray(i, i + 0x8000));
		}
		return { width, height, rgba: btoa(binary) };
	} finally {
		URL.revokeObjectURL(url);
	}
}

/* Runs in the page. A CONTACT SHEET: every icon at one size with its slug under it, on the ground
 * its tone asks for, so a person can LOOK at the whole pack in one picture. Answered as a PNG the
 * canvas encodes itself, because the sheet is for eyes, not for the pack's own reader. */
async function sheet({ cells, columns, cell }) {
	const label = 30;
	const gap = 8;
	const width = columns * (cell + gap) + gap;
	const rows = Math.max(1, Math.ceil(cells.length / columns));
	const height = rows * (cell + label + gap) + gap;
	const canvas = document.createElement('canvas');
	canvas.width = width;
	canvas.height = height;
	const context = canvas.getContext('2d', { colorSpace: 'srgb' });
	context.fillStyle = '#9a9ea6';
	context.fillRect(0, 0, width, height);
	context.textAlign = 'center';
	context.textBaseline = 'top';
	for (let index = 0; index < cells.length; index += 1) {
		const one = cells[index];
		const x = gap + (index % columns) * (cell + gap);
		const y = gap + Math.floor(index / columns) * (cell + label + gap);
		context.fillStyle = one.ground;
		context.fillRect(x, y, cell, cell);
		const bytes = Uint8Array.from(atob(one.png), (c) => c.charCodeAt(0));
		const bitmap = await createImageBitmap(new Blob([bytes], { type: 'image/png' }), {
			resizeWidth: cell,
			resizeHeight: cell,
			resizeQuality: 'high'
		});
		context.drawImage(bitmap, x, y, cell, cell);
		bitmap.close();
		context.fillStyle = '#ffffff';
		context.fillRect(x, y + cell, cell, label);
		context.fillStyle = one.low ? '#b00020' : '#111111';
		context.font = '11px sans-serif';
		context.fillText(one.label.slice(0, 18), x + cell / 2, y + cell + 3, cell - 2);
		context.font = '10px sans-serif';
		context.fillText(one.note.slice(0, 20), x + cell / 2, y + cell + 16, cell - 2);
	}
	const blob = await new Promise((resolve) => canvas.toBlob(resolve, 'image/png'));
	const raw = new Uint8Array(await blob.arrayBuffer());
	let binary = '';
	for (let i = 0; i < raw.length; i += 0x8000) {
		binary += String.fromCharCode.apply(null, raw.subarray(i, i + 0x8000));
	}
	return { png: btoa(binary) };
}

const lines = createInterface({ input: process.stdin, crlfDelay: Infinity });
for await (const line of lines) {
	if (!line.trim()) continue;
	let asked;
	try {
		asked = JSON.parse(line);
	} catch {
		continue;
	}
	let answer;
	try {
		answer = { id: asked.id, ...(await page.evaluate(asked.sheet ? sheet : draw, asked.sheet || asked)) };
	} catch (failure) {
		answer = { id: asked.id, error: String(failure && failure.message ? failure.message : failure).slice(0, 200) };
	}
	process.stdout.write(JSON.stringify(answer) + '\n');
}
await browser.close();
