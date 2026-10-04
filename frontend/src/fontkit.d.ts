/* fontkit ships no type declarations, and only the gates use it: `scripts/prepare_fonts.js`, which
 * is plain JavaScript, and `lib/design/figures.test.ts`, which asks two questions of a font file.
 * Those two questions are declared here rather than pulling in a types package for them. */
declare module 'fontkit' {
	export interface Glyph {
		advanceWidth: number;
	}
	export interface Font {
		availableFeatures?: string[];
		glyphForCodePoint(codePoint: number): Glyph;
	}
	export function create(buffer: Buffer | Uint8Array): Font;
}
