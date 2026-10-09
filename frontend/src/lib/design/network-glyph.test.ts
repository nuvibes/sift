import { describe, expect, it } from 'vitest';
import generated from '$lib/generated/icon-codepoints.json';
import { ICON_NAMES } from '$lib/design/icons';
import { facetsFor } from '$lib/components/shell/facet-labels';
import hiddenDialog from '$lib/components/HiddenDialog.svelte?raw';
import shareDialog from '$lib/components/ShareDialog.svelte?raw';
import networkMark from '$lib/components/entity/NetworkMark.svelte?raw';

/* One glyph for a network, and it is not a house. */

const HOUSES = 'other_houses';
const NETWORK = 'hub';

describe('a network wears one glyph', () => {
	it('has no houses in the icon list or the generated map', () => {
		expect(ICON_NAMES as readonly string[]).not.toContain(HOUSES);
		expect(Object.keys(generated)).not.toContain(HOUSES);
		expect(ICON_NAMES as readonly string[]).toContain(NETWORK);
	});

	it('draws the network glyph on both Network facets', () => {
		const network = (subject: 'asset' | 'site', key: string) =>
			facetsFor(subject).find((one) => one.key === key)?.icon;
		expect(network('asset', 'network')).toBe(NETWORK);
		expect(network('site', 'parent')).toBe(NETWORK);
	});

	it('draws the network glyph in the dialogs and on the mark', () => {
		for (const [name, source] of [
			['HiddenDialog', hiddenDialog],
			['ShareDialog', shareDialog],
			['NetworkMark', networkMark]
		] as const) {
			expect(source, name).toContain(`<Icon name="${NETWORK}"`);
			expect(source, name).not.toContain(HOUSES);
		}
	});
});
