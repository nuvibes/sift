// SPDX-License-Identifier: AGPL-3.0-or-later
/* The docs site's Markdown, read into blocks of plain text that the Documentation pane draws.
 * The build runs it (`scripts/docs_pages.js`), and anything it does not know stops the build. */

export type DocLink =
	| { kind: 'page'; slug: string; anchor?: string }
	| { kind: 'setting'; section: string; key?: string }
	| { kind: 'place'; path: string }
	| { kind: 'outside'; href: string };

/** A run of text: a plain string, or words with a mark. */
export type DocInline =
	string | { kind: 'strong' | 'code'; text: string } | { kind: 'link'; text: string; to: DocLink };

interface DocItem {
	anchors?: string[];
	inline: DocInline[];
}

export type DocBlock =
	| { kind: 'heading'; level: 2 | 3; anchors: string[]; inline: DocInline[] }
	| { kind: 'paragraph'; anchors?: string[]; inline: DocInline[] }
	| { kind: 'list'; ordered: boolean; items: DocItem[] }
	| { kind: 'note'; inline: DocInline[] }
	| { kind: 'code'; text: string }
	| { kind: 'image'; screen: string; alt: string }
	| { kind: 'table'; head: DocInline[][]; rows: DocInline[][][] };

export interface DocPage {
	slug: string;
	title: string;
	blocks: DocBlock[];
}

export type DocMenuItem = { slug: string; title: string } | { label: string; items: DocMenuItem[] };

export interface DocBook {
	/** The front page's words, drawn above the menu. */
	home: DocBlock[];
	menu: DocMenuItem[];
	pages: Record<string, DocPage>;
}

/** What only the build knows: where a link goes and which picture a path names. */
interface DocReading {
	link(href: string): DocLink;
	screen(src: string): string;
}

export class DocRefused extends Error {}

const refuse = (what: string): never => {
	throw new DocRefused(what);
};

/** The front matter's title and menu order, and the Markdown under it. */
export function frontOf(markdown: string): { title: string; order?: number; body: string } {
	const text = markdown.replace(/\r\n?/g, '\n');
	const end = text.startsWith('---\n') ? text.indexOf('\n---\n', 3) : -1;
	if (end === -1) refuse('a page with no front matter');
	const head = text.slice(4, end).split('\n');
	const title = head.find((line) => line.startsWith('title:'));
	const order = head.find((line) => /^\s+order:\s*\d+\s*$/.test(line));
	if (title === undefined) refuse('a page with no title');
	return {
		title: unquoted((title as string).slice('title:'.length).trim()),
		...(order === undefined ? {} : { order: Number(order.split(':')[1]) }),
		body: text.slice(end + '\n---\n'.length)
	};
}

function unquoted(value: string): string {
	if (value.startsWith("'") && value.endsWith("'")) return value.slice(1, -1).replace(/''/g, "'");
	if (value.startsWith('"') && value.endsWith('"')) return JSON.parse(value) as string;
	return value;
}

/** The id the docs site gives a heading: lower case, punctuation dropped, spaces as hyphens. */
export function slugOf(words: string): string {
	return words
		.toLowerCase()
		.replace(/[^\p{L}\p{M}\p{N}\p{Pc} -]/gu, '')
		.replace(/ /g, '-');
}

/** The words of some runs, without their marks. */
export function wordsOf(inline: DocInline[]): string {
	return inline.map((run) => (typeof run === 'string' ? run : run.text)).join('');
}

const ANCHOR_LINE = /^<a id="([^"]+)"><\/a>$/;
const COMMENT_LINE = /^<!--.*-->$/;
const HEADING = /^(#{1,6})\s+(.*?)\s*$/;
const BULLET = /^(?:[-*+]|(\d{1,9})[.)])\s+(.*)$/;
const FENCE = /^(```|~~~)/;
const IMAGE_LINE = /^!\[([^\]]*)\]\(([^)\s]+)\)$/;
const TABLE_RULE = /^\|(?:\s*:?-+:?\s*\|)+$/;

/** Read one page's Markdown into blocks. */
export function readBlocks(body: string, reading: DocReading): DocBlock[] {
	const page = new PageReader(reading);
	const lines = body.replace(/\r\n?/g, '\n').split('\n');
	for (let at = 0; at < lines.length; at += 1) at = page.line(lines, at);
	page.flush();
	if (page.anchors.length > 0)
		refuse(`an anchor with nothing after it: ${page.anchors.join(', ')}`);
	return page.blocks;
}

class PageReader {
	blocks: DocBlock[] = [];
	anchors: string[] = [];
	private paragraph: string[] = [];
	private quote: string[] = [];
	private list: { ordered: boolean; items: string[] } | null = null;
	private slugs = new Map<string, number>();
	private reading: DocReading;

	constructor(reading: DocReading) {
		this.reading = reading;
	}

	/** Read the line at `at`, and return the last line it used. */
	line(lines: string[], at: number): number {
		const line = lines[at] as string;
		if (FENCE.test(line)) return this.code(lines, at);
		if (line.startsWith('|')) return this.table(lines, at);
		if (line.startsWith('<!--') && !COMMENT_LINE.test(line)) refuse('a comment over lines');
		const anchor = ANCHOR_LINE.exec(line);
		const heading = HEADING.exec(line);
		const image = IMAGE_LINE.exec(line);
		if (line.trim() === '' || COMMENT_LINE.test(line)) this.flush();
		else if (anchor !== null) {
			this.flush();
			this.anchors.push(anchor[1] as string);
		} else if (heading !== null) this.heading(heading[1] as string, heading[2] as string);
		else if (image !== null) this.image(image[1] as string, image[2] as string);
		else if (line.startsWith('>')) this.quoted(line);
		else this.text(line);
		return at;
	}

	private text(line: string): void {
		const bullet = BULLET.exec(line);
		if (bullet !== null) {
			const ordered = bullet[1] !== undefined;
			if (this.list === null || this.list.ordered !== ordered) this.flush();
			this.list ??= { ordered, items: [] };
			this.list.items.push(bullet[2] as string);
			return;
		}
		if (/^\s/.test(line) && this.list === null) refuse(`an indented line outside a list: ${line}`);
		if (this.list !== null) {
			if (BULLET.test(line.trim())) refuse(`a list inside a list: ${line}`);
			const last = this.list.items.length - 1;
			this.list.items[last] = `${this.list.items[last]} ${line.trim()}`;
			return;
		}
		if (this.quote.length > 0) this.quote.push(line.trim());
		else this.paragraph.push(line.trim());
	}

	private quoted(line: string): void {
		if (this.quote.length === 0) this.flush();
		this.quote.push(line.replace(/^>\s?/, ''));
	}

	flush(): void {
		if (this.paragraph.length > 0) {
			const { inline, anchors } = this.runs(this.paragraph.join(' '));
			this.blocks.push({ kind: 'paragraph', ...anchored([...this.take(), ...anchors]), inline });
			this.paragraph = [];
		}
		if (this.quote.length > 0) {
			this.blocks.push({ kind: 'note', inline: this.bare(this.quote.join(' ')) });
			this.quote = [];
		}
		if (this.list !== null) {
			const items = this.list.items.map((item, at) => {
				const { inline, anchors } = this.runs(item);
				return { ...anchored(at === 0 ? [...this.take(), ...anchors] : anchors), inline };
			});
			this.blocks.push({ kind: 'list', ordered: this.list.ordered, items });
			this.list = null;
		}
	}

	private take(): string[] {
		const anchors = this.anchors;
		this.anchors = [];
		return anchors;
	}

	/** A block that cannot carry an anchor refuses one left waiting for it. */
	private plain(what: string): void {
		this.flush();
		if (this.anchors.length > 0) refuse(`an anchor before ${what}: ${this.anchors.join(', ')}`);
	}

	private heading(marks: string, words: string): void {
		this.flush();
		if (marks.length !== 2 && marks.length !== 3) refuse(`a heading of level ${marks.length}`);
		const inline = this.bare(words);
		const slug = slugOf(wordsOf(inline));
		const seen = this.slugs.get(slug) ?? 0;
		this.slugs.set(slug, seen + 1);
		const id = seen === 0 ? slug : `${slug}-${seen}`;
		const level = marks.length as 2 | 3;
		this.blocks.push({ kind: 'heading', level, anchors: [id, ...this.take()], inline });
	}

	private code(lines: string[], from: number): number {
		this.plain('a code block');
		const fence = (FENCE.exec(lines[from] as string) as RegExpExecArray)[1] as string;
		const text: string[] = [];
		let at = from + 1;
		for (; at < lines.length && !(lines[at] as string).startsWith(fence); at += 1) {
			text.push(lines[at] as string);
		}
		if (at === lines.length) refuse('a code block that never closes');
		this.blocks.push({ kind: 'code', text: text.join('\n') });
		return at;
	}

	private image(alt: string, src: string): void {
		this.plain('a picture');
		if (alt.trim() === '') refuse(`a picture with no words: ${src}`);
		this.blocks.push({ kind: 'image', screen: this.reading.screen(src), alt });
	}

	private table(lines: string[], from: number): number {
		this.plain('a table');
		let at = from;
		const rows: string[] = [];
		for (; at < lines.length && (lines[at] as string).startsWith('|'); at += 1) {
			rows.push((lines[at] as string).trim());
		}
		if (rows.length < 2 || !TABLE_RULE.test(rows[1] as string))
			refuse(`a table with no rule under its head: ${rows[0]}`);
		const cells = (row: string) =>
			row
				.slice(1, row.endsWith('|') ? -1 : undefined)
				.split('|')
				.map((cell) => this.bare(cell.trim()));
		const head = cells(rows[0] as string);
		const body = rows.slice(2).map(cells);
		if (body.some((row) => row.length !== head.length))
			refuse(`a table row with the wrong number of cells: ${rows[0]}`);
		this.blocks.push({ kind: 'table', head, rows: body });
		return at - 1;
	}

	/** Runs that may not carry an anchor. */
	private bare(text: string): DocInline[] {
		const { inline, anchors } = this.runs(text);
		if (anchors.length > 0) refuse(`an anchor where none is drawn: ${text}`);
		return inline;
	}

	private runs(text: string): { inline: DocInline[]; anchors: string[] } {
		return inlineOf(text, this.reading);
	}
}

const anchored = (anchors: string[]) => (anchors.length > 0 ? { anchors } : {});

const ENTITIES: Record<string, string> = {
	amp: '&',
	lt: '<',
	gt: '>',
	quot: '"',
	apos: "'",
	nbsp: '\u00a0',
	hellip: '\u2026'
};
const LINK = /^\[([^\]\n]*)\]\(\s*([^)\s]*)\s*\)/;
const INLINE_ANCHOR = /^<a id="([^"]+)"><\/a>/;
const ENTITY = /^&(#\d+|[a-z]+);/;
const PUNCTUATION = /[!-/:-@[-`{-~]/;

/** The runs of one line: bold, code, links and plain text, with the anchors it carries. */
export function inlineOf(
	text: string,
	reading: DocReading
): { inline: DocInline[]; anchors: string[] } {
	const runs = new Runs();
	const anchors: string[] = [];
	let at = 0;
	while (at < text.length) {
		const rest = text.slice(at);
		const ch = rest[0] as string;
		if (ch === '\\' && PUNCTUATION.test(rest[1] ?? '')) {
			runs.plain += rest[1];
			at += 2;
		} else if (ch === '`') at += runs.code(rest);
		else if ((ch === '*' || ch === '_') && rest[1] === ch) at += runs.strong(rest, ch);
		else if (ch === '[') at += runs.link(rest, reading);
		else if (ch === '<' && /^<[a-z/!]/i.test(rest)) at += anchorIn(rest, anchors);
		else if (ch === '&') at += runs.entity(rest);
		else if (ch === '!' && rest[1] === '[') refuse(`a picture inside a sentence: ${text}`);
		else {
			runs.plain += ch;
			at += 1;
		}
	}
	return { inline: runs.done(), anchors };
}

function anchorIn(rest: string, anchors: string[]): number {
	const anchor = INLINE_ANCHOR.exec(rest);
	if (anchor === null) return refuse(`markup the pane doesn't draw: ${rest.slice(0, 60)}`);
	anchors.push(anchor[1] as string);
	return anchor[0].length;
}

class Runs {
	plain = '';
	private runs: DocInline[] = [];

	keep(run: DocInline): void {
		if (this.plain !== '') this.runs.push(this.plain);
		this.plain = '';
		this.runs.push(run);
	}

	code(rest: string): number {
		const end = rest.indexOf('`', 1);
		if (end === -1) return refuse(`a code mark that never closes: ${rest.slice(0, 60)}`);
		this.keep({ kind: 'code', text: rest.slice(1, end) });
		return end + 1;
	}

	strong(rest: string, ch: string): number {
		const end = rest.indexOf(ch + ch, 2);
		if (end === -1) return refuse(`a bold mark that never closes: ${rest.slice(0, 60)}`);
		this.keep({ kind: 'strong', text: decoded(rest.slice(2, end)) });
		return end + 2;
	}

	link(rest: string, reading: DocReading): number {
		const link = LINK.exec(rest);
		if (link === null) {
			this.plain += '[';
			return 1;
		}
		const words = decoded((link[1] as string).replace(/\*\*|`/g, ''));
		this.keep({ kind: 'link', text: words, to: reading.link(link[2] as string) });
		return link[0].length;
	}

	entity(rest: string): number {
		const entity = ENTITY.exec(rest);
		if (entity === null) {
			this.plain += '&';
			return 1;
		}
		this.plain += decoded(entity[0]);
		return entity[0].length;
	}

	done(): DocInline[] {
		if (this.plain !== '') this.runs.push(this.plain);
		return this.runs;
	}
}

/** Text with its character references written out; one the pane does not know stops the build. */
function decoded(text: string): string {
	return text.replace(/&(#\d+|[a-z]+);/g, (whole, name: string) => {
		if (name.startsWith('#')) return String.fromCodePoint(Number(name.slice(1)));
		return ENTITIES[name] ?? refuse(`a character reference the pane doesn't know: ${whole}`);
	});
}
