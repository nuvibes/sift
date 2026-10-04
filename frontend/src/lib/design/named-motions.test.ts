/* Every animation names a motion somebody declared: `scripts/check_named_motions.js`, read on
 * stylesheets written here so each rule is seen to fire and to stay quiet. */

import { describe, expect, it } from 'vitest';

import { named, stylesOf, unnamedMotions } from '../../../scripts/check_named_motions.js';

const GLOBAL = `
@keyframes appear { from { opacity: 0; } }
@keyframes rise { from { opacity: 0; translate: 0 var(--rise); } }
`;

function sheet(styles: string) {
	return [{ where: 'lib/X.svelte', styles }];
}

describe('the names an animation runs', () => {
	it('are read out of the shorthand, past its times, curves and keywords', () => {
		expect(
			named(
				'.a { animation: rise var(--dur-fast) cubic-bezier(0.2, 0, 0, 1) infinite both; }\n' +
					'.b { animation: turn 1s linear infinite, pan var(--dur-loop) steps(4) 200ms; }\n' +
					'.c { animation-name: appear; animation-delay: -0.2s; }\n' +
					'.d { animation: none; }'
			)
		).toEqual(['rise', 'turn', 'pan', 'appear']);
	});

	it('and from every style block a component has', () => {
		const code =
			'<style>.a { animation: rise 1s; }</style><p></p><style>.b { animation: pop 1s; }</style>';
		expect(named(stylesOf('X.svelte', code))).toEqual(['rise', 'pop']);
	});
});

describe('a motion nobody declared', () => {
	it('is refused, by name and file', () => {
		expect(
			unnamedMotions(sheet('.a { animation: rsie var(--dur-fast) var(--ease); }'), GLOBAL)
		).toEqual(['lib/X.svelte: animates `rsie`, and no @keyframes declares it.']);
	});

	it('passes when app.css declares it', () => {
		expect(
			unnamedMotions(sheet('.a { animation: rise var(--dur-fast) var(--ease); }'), GLOBAL)
		).toEqual([]);
	});

	it('and when the file declares it itself', () => {
		const own = '@keyframes wobble { to { rotate: 1deg; } } .a { animation: wobble 1s; }';
		expect(unnamedMotions(sheet(own), GLOBAL)).toEqual([]);
	});
});
