// What `check_one_veil.js` refuses, as a function of one file's code (comments already taken out)
// and where the file is, so the unit suite can hand it lines and watch it refuse them.
//
// Three layers dim or stand in for what is behind them, and each is drawn in ONE place:
//
//   - the veil, the dimmed sheet behind a dialog or a panel: the `.veil` rule in `app.css`, worn
//     by `Modal` and `Veil` only, on a layer only `Veil` names;
//   - the drop offer, the light veil while something is held over the window: `DropOffer`;
//   - the withheld face of a Hidden thing: `Withheld`, which is a face and not a layer.
//
// A component that writes the class `veil`, reads a veil's ground or layer, or blurs what is behind
// it at the veil's strength is drawing one of these by hand. The class alone is enough to go
// wrong: a card face named `veil` picks up the dialog's layer from `app.css` and rises over the
// settings panel.

/** Which files may say what, as paths from `src`. */
const OWNERS = {
	veilClass: ['lib/components/common/Modal.svelte', 'lib/components/common/Veil.svelte'],
	veilLayer: ['lib/components/common/Veil.svelte'],
	dropOffer: ['lib/components/common/DropOffer.svelte']
};

/** @param {string} text @param {number} index */
function lineOf(text, index) {
	return text.slice(0, index).split('\n').length;
}

const RULES = [
	{
		what: 'the class `veil` (the dialog sheet in app.css; it brings its layer and ground along)',
		pattern:
			/\bclass\s*=\s*(?:"[^"]*\bveil\b[^"]*"|'[^']*\bveil\b[^']*'|\{[^}]*\bveil\b[^}]*\})|\bclass:veil\b|(?:^|[\s,}>~+(])\.veil\b(?!-)/gm,
		owners: OWNERS.veilClass
	},
	{
		what: "a veil's ground (`--sift-overlay`), which only the `.veil` rule in app.css reads",
		pattern: /var\(\s*--sift-overlay\s*\)/g,
		owners: []
	},
	{
		what: "a veil's layer (`--z-*-veil`), which only `Veil` names",
		pattern: /--z-[a-z{}]+-veil\b/g,
		owners: OWNERS.veilLayer
	},
	{
		what: "the veil's blur behind an element (`backdrop-filter` at `--blur-veil`)",
		pattern: /backdrop-filter\s*:[^;]*--blur-veil/g,
		owners: []
	},
	{
		what: 'the drop veil or the drop layer, which only `DropOffer` draws',
		pattern: /--sift-drop-veil\b|--z-drop-overlay\b/g,
		owners: OWNERS.dropOffer
	}
];

/**
 * Every hand-drawn veil in one file.
 *
 * @param {string} code the file with its comments taken out
 * @param {string} where its path from `src`
 * @returns {{ line: number, what: string }[]}
 */
export function handDrawnVeilsIn(code, where) {
	const found = [];
	for (const rule of RULES) {
		if (rule.owners.includes(where)) continue;
		for (const match of code.matchAll(rule.pattern)) {
			found.push({ line: lineOf(code, match.index), what: rule.what });
		}
	}
	return found.sort((a, b) => a.line - b.line);
}
