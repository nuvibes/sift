/* The docs reader: what it turns into blocks, and every shape it refuses so the build stops. */
import { describe, expect, it } from 'vitest';

import { DocRefused, frontOf, inlineOf, readBlocks, slugOf, type DocLink } from './read';

const reading = {
	link: (href: string): DocLink => ({ kind: 'outside', href }),
	screen: (src: string) => src.split('/').at(-1) as string
};
const read = (body: string) => readBlocks(body, reading);
const refused = (body: string) => () => read(body);

describe('the front matter', () => {
	it('reads the title, quoted or not, and the menu order', () => {
		const page = "---\ntitle: 'Step 1: Install Sift'\nsidebar:\n  order: 1\n---\n\nText.\n";
		expect(frontOf(page)).toEqual({ title: 'Step 1: Install Sift', order: 1, body: '\nText.\n' });
		expect(frontOf('---\ntitle: "A \\"b\\""\n---\n').title).toBe('A "b"');
		expect(frontOf("---\ntitle: It's\n---\n")).toEqual({ title: "It's", body: '' });
	});

	it('refuses a page with no front matter or no title', () => {
		expect(() => frontOf('# Hello')).toThrow(DocRefused);
		expect(() => frontOf('---\ndescription: x\n---\n')).toThrow('no title');
	});
});

describe('the blocks', () => {
	it('reads headings with the ids the site gives them, a repeat numbered', () => {
		const blocks = read('## A file\'s menu\n\n### A file\'s menu\n\n<a id="x.y"></a>\n\n## Next');
		expect(blocks).toEqual([
			{ kind: 'heading', level: 2, anchors: ['a-files-menu'], inline: ["A file's menu"] },
			{ kind: 'heading', level: 3, anchors: ['a-files-menu-1'], inline: ["A file's menu"] },
			{ kind: 'heading', level: 2, anchors: ['next', 'x.y'], inline: ['Next'] }
		]);
		expect(slugOf('0.2.0 - 2026-10-04')).toBe('020---2026-10-04');
	});

	it('reads paragraphs, lists with their anchors, notes, code and a picture', () => {
		const blocks = read(
			[
				'<!-- GENERATED -->',
				'One line',
				'and the next.',
				'',
				'- <a id="a.b"></a>**Bold**: an item',
				'  carried on.',
				'- Two',
				'1. First',
				'',
				'> **Note:** read this',
				'',
				'```sh',
				'uv run sift',
				'```',
				'![The pane](../../assets/screens/pane.jpg)'
			].join('\n')
		);
		expect(blocks).toEqual([
			{ kind: 'paragraph', inline: ['One line and the next.'] },
			{
				kind: 'list',
				ordered: false,
				items: [
					{ anchors: ['a.b'], inline: [{ kind: 'strong', text: 'Bold' }, ': an item carried on.'] },
					{ inline: ['Two'] }
				]
			},
			{ kind: 'list', ordered: true, items: [{ inline: ['First'] }] },
			{ kind: 'note', inline: [{ kind: 'strong', text: 'Note:' }, ' read this'] },
			{ kind: 'code', text: 'uv run sift' },
			{ kind: 'image', screen: 'pane.jpg', alt: 'The pane' }
		]);
	});

	it('reads a table, its head and its rows', () => {
		expect(read('| Key | What |\n|---|---|\n| `M` | Mutes |')).toEqual([
			{
				kind: 'table',
				head: [['Key'], ['What']],
				rows: [[[{ kind: 'code', text: 'M' }], ['Mutes']]]
			}
		]);
	});

	it.each([
		['a first-level heading', '# Title'],
		['a fourth-level heading', '#### Deep'],
		['a list inside a list', '- One\n  - Inner'],
		['an indented line outside a list', '    code by indent'],
		['a code block that never closes', '```\ncode'],
		['a comment over lines', '<!-- one\ntwo -->'],
		['a table with no rule', '| a | b |\n| c | d |'],
		['a table row with a cell missing', '| a | b |\n|---|---|\n| c |'],
		['a picture with no words', '![](x.jpg)'],
		['an anchor before a picture', '<a id="x"></a>\n![Pane](x.jpg)'],
		['an anchor with nothing after it', 'Text.\n\n<a id="x"></a>'],
		['an anchor in a heading', '## <a id="x"></a>Title'],
		['markup the pane does not draw', 'A <b>bold</b> word'],
		['a picture inside a sentence', 'See ![this](x.jpg) here'],
		['a character reference it does not know', 'A &copy; mark'],
		['a bold mark that never closes', 'A **bold word'],
		['a code mark that never closes', 'A `code word']
	])('refuses %s, so the build stops', (_, body) => {
		expect(refused(body)).toThrow(DocRefused);
	});
});

describe('the runs of a line', () => {
	it('reads bold, code, links, escapes and character references', () => {
		const seen: string[] = [];
		const link = (href: string): DocLink => {
			seen.push(href);
			return { kind: 'place', path: href };
		};
		const { inline, anchors } = inlineOf(
			'A \\*star\\*, __b__ and [**`Browse`**](/browse) &hellip; &#65; a < b & c [no link',
			{ ...reading, link }
		);
		expect(inline).toEqual([
			'A *star*, ',
			{ kind: 'strong', text: 'b' },
			' and ',
			{ kind: 'link', text: 'Browse', to: { kind: 'place', path: '/browse' } },
			' \u2026 A a < b & c [no link'
		]);
		expect(anchors).toEqual([]);
		expect(seen).toEqual(['/browse']);
	});
});
