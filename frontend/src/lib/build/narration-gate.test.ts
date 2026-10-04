/** What the narration gate refuses, and the comment reader it stands on.
 *
 * `scripts/check_no_narration.js` holds every comment in the client and the desktop shell to the
 * list in `tests/gates/data/narration.json`. The reading is `scripts/lib/narration.js` over
 * `commentsIn` in `scripts/lib/tree.js`, the same pass `withoutComments` makes, so each is driven
 * here with text written for the purpose.
 */

import { describe, expect, it } from 'vitest';

import { narrationEntries, narrationIn } from '../../../scripts/lib/narration.js';
import { commentsIn, withoutComments } from '../../../scripts/lib/tree.js';

type Entry = { family: string; source: string; pattern: RegExp; example: string; clean: string };

const stamp = ['20', '26-01-02'].join('');

describe('the narration list', () => {
	it.each((narrationEntries() as Entry[]).map((one) => [one.family, one.source, one]))(
		'%s %s matches its example and not its near miss',
		(_family, _source, one) => {
			const entry = one as Entry;
			expect(entry.example.match(entry.pattern)).not.toBeNull();
			expect(entry.clean.match(entry.pattern)).toBeNull();
		}
	);
});

describe('the comment reader', () => {
	it('finds a line comment, a block comment and a markup comment, each at its own line', () => {
		const text =
			'<script lang="ts">\n' +
			`\t// one ${stamp}\n` +
			"\tconst url = 'http://example.test/a';\n" +
			`\t/* two\n\t   ${stamp} */\n` +
			'</script>\n' +
			`<!-- three ${stamp} -->\n` +
			'<input accept="image/*" />\n';
		expect(narrationIn(text).map((one) => [one.line, one.family])).toEqual([
			[2, 'date'],
			[5, 'date'],
			[7, 'date']
		]);
	});

	it('leaves code alone: a date in a string is data, not narration', () => {
		expect(narrationIn(`const when = '${stamp}';\n`)).toEqual([]);
	});

	it('takes out exactly what withoutComments blanks', () => {
		const text =
			'<script>\n\t// a\n\tconst x = 1; /* b */\n</script>\n<!-- c -->\n<p>{x}</p>\n' +
			'<style>\n\t/* d */\n\tp { color: red; }\n</style>\n';
		let rebuilt = text;
		for (const one of commentsIn(text)) {
			rebuilt =
				rebuilt.slice(0, one.offset) +
				one.text.replace(/[^\n]/g, ' ') +
				rebuilt.slice(one.offset + one.text.length);
		}
		expect(rebuilt).toBe(withoutComments(text));
		expect(commentsIn(text).map((one) => one.text)).toEqual([
			'// a',
			'/* b */',
			'<!-- c -->',
			'/* d */'
		]);
	});
});
