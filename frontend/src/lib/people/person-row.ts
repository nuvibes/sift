/* A person, as a row in a picker: the name, and the picture the rest of the application draws
 * them by. */

import { pickRow, type Drawable } from '$lib/entity/entity-picture';
import type { PickChoice } from '$lib/components/common/verbs';

export function personRow(person: Drawable): PickChoice {
	return pickRow('person', person);
}
