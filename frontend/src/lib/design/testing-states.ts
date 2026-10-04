/* Support for the tests, and only for the tests.
 *
 * A component's stylesheet with the states only a pointer or a keyboard can hold (a hover, a press,
 * a focus) ALSO spelled as attributes a test can set: `data-hover`, `data-active`, `data-focus`.
 * The unit environment has no pointer, so this is how a test holds one of those states and asks
 * `getComputedStyle` what the component looks like in it. Hand the result to `applyStyles`.
 *
 * Each state becomes `:is(<the state>, <the attribute>)`, so the rule still means what it did and
 * the cascade is unchanged. Outside `:global(...)` the attribute is written as `:global(...)`
 * itself: the compiler drops a selector for an attribute the markup never writes, and it cannot
 * know the real state never matches here.
 *
 * Nothing in the app imports this, and it is not in the bundle.
 */

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
