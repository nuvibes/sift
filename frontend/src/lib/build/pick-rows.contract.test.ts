/*
 * Every picker that offers people, Sites, tags, collections or Photo Sets draws its rows by the one
 * rule for the picture a thing is drawn by: `pickRow` in `$lib/entity/entity-picture` (and `personRow` and
 * `siteRow`, which are it). A row built any other way looks one way in a picker and another on the
 * thing's card and page.
 *
 * Static: it reads the source of every file that both offers a pick and reads one of the stores
 * those kinds live in, so it proves each picker asks the rule, not that the rule is right
 * (`entity-picture.test.ts` holds that).
 */

import { readFileSync, readdirSync, statSync } from 'node:fs';
import { dirname, join, relative, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

import { describe, expect, it } from 'vitest';

/** The whole of `src`, resolved from this file so the runner's working directory cannot move it. */
const SOURCE = resolve(dirname(fileURLToPath(import.meta.url)), '..', '..');

/** Where rows reach a pick surface: the sheet, the flyout, a verb's `ask`, or a page of choices. */
const OFFERS_A_PICK = /<PickDialog\b|<PickMenu\b|\bask: async\b|\bchoices: /;

/** The stores the five kinds with pictures are read from, and the one the people who can be
 *  recognized are read from (`knownPeople`), who are People too. */
const READS_A_KIND =
	/from '\$lib\/(entity\/tags|people\/people|library\/collections|library\/photo-sets|people\/faces)\.svelte'/;

/** The rule, under any of its three names. */
const ASKS_THE_RULE = /from '\$lib\/(entity\/entity-picture|people\/person-row|entity\/site-row)'/;

/** A row made of a name and an id alone: the shape that drops the picture. An `as` on the id
 *  is matched too, or a renamed id would hide one from this. */
const NAME_ONLY_ROW = /\(\{\s*id:\s*[\w.]+\.id(?:\s+as\s+\w+)?,\s*name:\s*[\w.]+\.name\s*\}\)/;

/** A picture put together in the picker rather than asked of the rule. */
const HAND_BUILT_PICTURE = /\bpicture:\s*\{|\bsrc:\s*(coverUrl|faceCoverUrl)\(/;

/** The component gallery is a separate checkout and not part of the product. */
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
		// Every rule below iterates; a survey that found nothing would pass them all.
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
