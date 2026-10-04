/*
 * A person, as a row in a picker: the name, and the picture the rest of the application draws them
 * by. The picture is `drawnBy`'s (`$lib/entity/entity-picture`), the one rule every picker reads, so a
 * person looks the same in a picker as on their card and their page.
 */

import { pickRow, type Drawable } from '$lib/entity/entity-picture';
import type { PickChoice } from '$lib/components/common/verbs';

export function personRow(person: Drawable): PickChoice {
	return pickRow('person', person);
}
