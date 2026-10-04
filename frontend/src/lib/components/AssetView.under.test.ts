/*
 * The order of what stands under the picture on a file's own screen: who is in the file first, what
 * looks like it second. Each strip's own tests mount it alone, so this is the one place the pair is
 * read together.
 *
 * Read from the source rather than mounted: both strips draw nothing until the server answers, so a
 * mounted screen would need both requests faked, and a fake answering only one would pass with
 * either order. The markup decides the order, so the markup is read, with its comments taken out
 * first.
 */
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { describe, expect, it } from 'vitest';

/* The screen with the band and the record it draws through `FileBandRows` and `FileRecordPanel`. */
const source = ['AssetView.svelte', 'FileBandRows.svelte', 'FileRecordPanel.svelte']
	.map((file) => readFileSync(resolve(`src/lib/components/${file}`), 'utf8'))
	.join('\n');

/** The markup with every `<!-- -->` comment gone, so only what is drawn is read. */
const drawn = source.replace(/<!--[\s\S]*?-->/g, '');

describe('what stands under the picture', () => {
	it('draws who is in the file before what looks like it', () => {
		const faces = drawn.indexOf('<FacesInThis');
		const alike = drawn.indexOf('<LooksLikeThis');
		// Both present, once each: an order between a strip and nothing proves nothing.
		expect(faces).toBeGreaterThan(-1);
		expect(alike).toBeGreaterThan(-1);
		expect(drawn.indexOf('<FacesInThis', faces + 1)).toBe(-1);
		expect(drawn.indexOf('<LooksLikeThis', alike + 1)).toBe(-1);
		expect(faces).toBeLessThan(alike);
	});

	it('heads the record the way the strips head theirs: outside its panel, at one level', () => {
		const block = drawn.indexOf('summary="File info"');
		const panel = drawn.indexOf('<Panel', block);
		expect(block).toBeGreaterThan(-1);
		expect(drawn.lastIndexOf('<Fold', block)).toBeGreaterThan(drawn.lastIndexOf('</Fold>', block));
		expect(block).toBeLessThan(panel);
	});

	it('folds every section under the picture through the one Fold, each remembered', () => {
		/* Enrichment, Who is in this, Similar to this and File info: each a `Fold section` with a
		   key of its own, so a fold drawn another way, or two sharing a key, cannot come back. */
		const read = (file: string) =>
			readFileSync(resolve(`src/lib/components/${file}`), 'utf8').replace(/<!--[\s\S]*?-->/g, '');
		const folds = [drawn, read('FacesInThis.svelte'), read('LooksLikeThis.svelte')]
			.flatMap((text) => [...text.matchAll(/<Fold\s([^>]*?)>/g)].map((one) => one[1]))
			.filter((attributes) => /\bsection\b/.test(attributes));
		const named = (attributes: string) => /summary="([^"]+)"/.exec(attributes)?.[1];
		const kept = (attributes: string) => /remember="([^"]+)"/.exec(attributes)?.[1];
		expect(folds.map(named)).toEqual([
			'Enrichment',
			'File info',
			'Who is in this',
			'Similar to this'
		]);
		const keys = folds.map(kept);
		expect(keys.every((key) => key !== undefined)).toBe(true);
		expect(new Set(keys).size).toBe(keys.length);
		expect(drawn).not.toContain('About this file');
	});

	it('keeps the faces strip able to move the playhead', () => {
		const faces = drawn.slice(
			drawn.indexOf('<FacesInThis'),
			drawn.indexOf('/>', drawn.indexOf('<FacesInThis'))
		);
		expect(faces).toContain('onseek=');
	});

	it('draws no chip for what a watermark read: that is a line in the History', () => {
		// The watermark reading is in the file's History; the blue Enriched-by mark stays. This
		// checks only that no grey chip is drawn.
		expect(drawn).not.toContain('WatermarkMark');
		expect(source).not.toContain("from '$lib/components/entity/WatermarkMark.svelte'");
		expect(drawn).toContain('<EnrichmentMarks');
	});

	it('draws Trim, Create GIF and Compress from the one table the wall reads', () => {
		/* One table for both lists (`fileVerbs`, which carries `clipEditsOffered` and the refusal
		   "No writable folder"): the file's own screen declares none of the three itself, so it
		   holds no private copy to drift from the wall's. */
		const own = source.slice(source.indexOf('function ownVerbs('));
		const body = own.slice(0, own.indexOf('\n\t}\n'));
		for (const id of ['edit', 'gif', 'compress']) expect(body).not.toContain(`id: '${id}'`);
		expect(source).toMatch(
			/all\.filter\(\(one\) => \['edit', 'gif', 'compress'\]\.includes\(one\.id\)\)/
		);
	});
});
