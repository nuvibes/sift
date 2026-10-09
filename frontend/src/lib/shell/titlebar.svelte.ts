/* The desktop window's caption buttons, painted in the colour READ off the top bar. */

import { bridge } from '$lib/bridge';

const GROUND = '--sift-surface-1';
const MARK = '--sift-ink';

/** Null for anything not plain and opaque: a guess would be a confident wrong colour. */
export function asHex(value: string): string | null {
	const said = value.trim();
	if (/^#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6})$/.test(said)) return said;
	const parts = said.match(/^rgba?\(([^)]+)\)$/);
	if (!parts) return null;
	const tokens = parts[1].split(/[\s,/]+/).filter((one) => one !== '');
	const numbers = tokens.map((one) => Number.parseFloat(one));
	if (numbers.length < 3 || numbers.slice(0, 3).some((one) => !Number.isFinite(one))) return null;
	/* Read from the token: `0.5` and `50%` disagree about scale. */
	if (tokens.length > 3) {
		const raw = tokens[3];
		const alpha = raw.endsWith('%') ? numbers[3] / 100 : numbers[3];
		if (!Number.isFinite(alpha) || alpha < 1) return null;
	}
	const hex = numbers
		.slice(0, 3)
		.map((one) =>
			Math.max(0, Math.min(255, Math.round(one)))
				.toString(16)
				.padStart(2, '0')
		)
		.join('');
	return `#${hex}`;
}

export async function dressTitleBar(): Promise<boolean> {
	if (typeof document === 'undefined' || !bridge.canDressTitleBar()) return false;
	const style = getComputedStyle(document.documentElement);
	const color = asHex(style.getPropertyValue(GROUND));
	const symbolColor = asHex(style.getPropertyValue(MARK));
	if (color === null || symbolColor === null) return false;
	return bridge.dressTitleBar({ color, symbolColor });
}

/** Not `env(titlebar-area-*)`, which still applies in a browser. */
export function markOverlaidWindow(): void {
	if (typeof document === 'undefined') return;
	if (bridge.canDressTitleBar()) document.documentElement.setAttribute('data-window', 'overlaid');
}
