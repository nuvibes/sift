/* Support for the tests, and only for the tests. */

const SPELLED: Record<string, string> = {
	hover: 'data-hover',
	active: 'data-active',
	'focus-visible': 'data-focus'
};

const STATE = /^:(hover|active|focus-visible)(?![\w-])/;

/** The same component source, its `<style>` block rewritten as the header says. */
export function heldStates(source: string): string {
	return source.replace(/<style>([\s\S]*?)<\/style>/, (_whole, css: string) => {
		let out = '';
		/* Which open parentheses belong to a `:global(`, innermost last. */
		const groups: boolean[] = [];
		for (let at = 0; at < css.length; at += 1) {
			if (css.startsWith(':global(', at)) {
				groups.push(true);
				out += ':global(';
				at += ':global('.length - 1;
				continue;
			}
			const found = STATE.exec(css.slice(at));
			if (found) {
				const attribute = `[${SPELLED[found[1]]}]`;
				const inGlobal = groups.includes(true);
				out += `:is(${found[0]}, ${inGlobal ? attribute : `:global(${attribute})`})`;
				at += found[0].length - 1;
				continue;
			}
			if (css[at] === '(') groups.push(false);
			else if (css[at] === ')') groups.pop();
			out += css[at];
		}
		return `<style>${out}</style>`;
	});
}
