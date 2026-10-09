/* A Site, as a row in a picker: the name, and the picture the rest of the application draws it
 * by. */

import { pickRow, type Drawable } from '$lib/entity/entity-picture';
import type { PickChoice } from '$lib/components/common/verbs';

export function siteRow(site: Drawable): PickChoice {
	return pickRow('site', site);
}
