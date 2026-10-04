/*
 * EVERY READ OF A SAFE AREA GOES THROUGH ONE OF THE FOUR TOKENS.
 *
 * A phone draws over the page at its edges (the notch or the camera, the rounded corners, the home
 * bar), and the page learns how far through the browser's safe-area insets. `app.css` names each of the
 * four once (`--safe-top`, `--safe-right`, `--safe-bottom`, `--safe-left`) with one fallback, and
 * every bar standing at an edge reads the token for that edge. A second spelling of the inset,
 * written straight into a component, is how one bar clears the home bar and the bar beside it does
 * not: a different fallback, or the wrong edge, and nothing on a desk shows it.
 *
 * This is the rule as a function, so the test beside it can hold the whole tree to it and show it
 * refusing a planted read.
 */

/** The one place an inset may be read: the four token declarations in the root stylesheet. */
export const TOKEN_FILE = 'src/app.css';

/** A read of an inset, anywhere in a file. */
const DIRECT = /env\(\s*safe-area-inset-/g;

/** One of the four token declarations, which is the only line allowed to hold a read. */
const DECLARATION = /^\s*--safe-(top|right|bottom|left):\s*env\(safe-area-inset-\1,\s*0px\);\s*$/;

/** A file that reads an inset directly, and on which lines. */
interface DirectInset {
	file: string;
	lines: number[];
}

/**
 * Every file that reads an inset other than through a token, with the lines. Comments count: a
 * read explained in a comment is still the spelling the next person copies.
 */
export function directInsets(files: readonly { file: string; text: string }[]): DirectInset[] {
	const found: DirectInset[] = [];
	for (const { file, text } of files) {
		const lines: number[] = [];
		text.split('\n').forEach((line, at) => {
			if (!DIRECT.test(line)) return;
			DIRECT.lastIndex = 0;
			if (file.endsWith(TOKEN_FILE) && DECLARATION.test(line)) return;
			lines.push(at + 1);
		});
		DIRECT.lastIndex = 0;
		if (lines.length > 0) found.push({ file, lines });
	}
	return found;
}
