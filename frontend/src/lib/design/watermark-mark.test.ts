/*
 * One glyph for a watermark, wherever one is drawn: the corner the mark sits in.
 *
 * `position_bottom_right`, in the four places a watermark is drawn: the Enriched-by mark on a
 * file and its facet row (both read `facetValueIcon`), the History line (`markOf`, which reads the
 * same table and falls back to its own), and the Settings section. Four tables, so four places the
 * glyph could quietly differ; the type only proves each name exists.
 */
import { describe, expect, it } from 'vitest';

import { markOf } from '$lib/components/common/history';
import { facetValueIcon } from '$lib/components/shell/facet-labels';
import { SETTINGS_SECTIONS } from '$lib/settings-ui/sections';

const WATERMARK = 'position_bottom_right';

describe('the watermark mark', () => {
	it('is the bottom-right corner on the Enriched-by mark and its facet row', () => {
		expect(facetValueIcon('enriched', 'watermark')).toBe(WATERMARK);
	});

	it('is the same corner on a History line, with and without the pass named', () => {
		expect(markOf('watermark', 'watermark')).toBe(WATERMARK);
		expect(markOf('watermark')).toBe(WATERMARK);
	});

	it('is the same corner on the Watermarks section in Settings', () => {
		expect(SETTINGS_SECTIONS.find((one) => one.id === 'watermarks')?.icon).toBe(WATERMARK);
	});
});
