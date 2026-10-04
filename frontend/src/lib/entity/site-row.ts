/*
 * A Site, as a row in a picker: the name, and the picture the rest of the application draws it by.
 * The picture is `drawnBy`'s (`$lib/entity/entity-picture`): a chosen cover, else the Site's own mark from
 * the shipped pack, drawn whole, else no picture, and the row wears the verb's glyph.
 */

import { pickRow, type Drawable } from '$lib/entity/entity-picture';
import type { PickChoice } from '$lib/components/common/verbs';

export function siteRow(site: Drawable): PickChoice {
	return pickRow('site', site);
}
