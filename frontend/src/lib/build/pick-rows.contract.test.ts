/*
 * Every picker of people, Sites, tags, collections or Photo Sets draws rows by `pickRow` (or
 * `personRow`, `siteRow`). Static: it proves each picker asks the rule, not that the rule is right.
 */

import { readFileSync, readdirSync, statSync } from 'node:fs';
import { dirname, join, relative, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

import { describe, expect, it } from 'vitest';

const SOURCE = resolve(dirname(fileURLToPath(import.meta.url)), '..', '..');

const OFFERS_A_PICK = /<PickDialog\b|<PickMenu\b|\bask: async\b|\bchoices: /;

/** The stores the five kinds are read from, and `knownPeople`. */
const READS_A_KIND =
	/from '\$lib\/(entity\/tags|people\/people|library\/collections|library\/photo-sets|people\/faces)\.svelte'/;

const ASKS_THE_RULE = /from '\$lib\/(entity\/entity-picture|people\/person-row|entity\/site-row)'/;

/** A row of a name and an id alone, which drops the picture; an `as` on the id too. */
const NAME_ONLY_ROW = /\(\{\s*id:\s*[\w.]+\.id(?:\s+as\s+\w+)?,\s*name:\s*[\w.]+\.name\s*\}\)/;

const HAND_BUILT_PICTURE = /\bpicture:\s*\{|\bsrc:\s*(coverUrl|faceCoverUrl)\(/;

/** The DESIGN GALLERY is a separate checkout. */
const NOT_SURVEYED = ['routes/design/'];

function sources(directory: string): string[] {
	return readdirSync(directory).flatMap((name) => {
		const path = join(directory, name);
		if (statSync(path).isDirectory()) return sources(path);
		const isSource = /\.(svelte|ts)$/.test(name) && !/\.test\.(ts|svelte)$/.test(name);
		return isSource ? [path] : [];
	});
}

const pickers = sources(SOURCE)
	.map((path) => ({ file: relative(SOURCE, path).replaceAll('\\', '/'), path }))
	.filter(({ file }) => !NOT_SURVEYED.some((skipped) => file.startsWith(skipped)))
	.map(({ file, path }) => ({ file, text: readFileSync(path, 'utf8') }))
	.filter(({ text }) => OFFERS_A_PICK.test(text) && READS_A_KIND.test(text));

describe('the rows a picker offers', () => {
	it('are found at all', () => {
		const files = pickers.map(({ file }) => file);
		for (const known of [
			'lib/components/common/FileVerbs.svelte',
			'lib/components/FacesInThis.svelte',
			'lib/entity/entity-tags.svelte.ts',
			'lib/components/swap/StartSwap.svelte',
			'lib/settings-ui/ExportPeopleSheet.svelte',
			'routes/loops/+page.svelte'
		]) {
			expect(files, `${known} is not surveyed`).toContain(known);
		}
	});

	it('are drawn by the one rule', () => {
		const strays = pickers.filter(({ text }) => !ASKS_THE_RULE.test(text)).map(({ file }) => file);
		expect(strays, 'these pickers never ask pickRow, personRow or siteRow').toEqual([]);
	});

	it('never drop the picture by carrying a name and an id alone', () => {
		const strays = pickers.filter(({ text }) => NAME_ONLY_ROW.test(text)).map(({ file }) => file);
		expect(strays, 'build each row with pickRow instead').toEqual([]);
	});

	it('never put a picture together themselves', () => {
		const strays = pickers
			.filter(({ text }) => HAND_BUILT_PICTURE.test(text))
			.map(({ file }) => file);
		expect(strays, 'the picture is drawnBy, in $lib/entity/entity-picture').toEqual([]);
	});
});
