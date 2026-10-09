/* The people picker's two questions ("who is on this page of people?" */

import type { PickChoice, PickPage } from '$lib/components/common/verbs';
import { people } from '$lib/people/people.svelte';
import { personRow } from '$lib/people/person-row';

/** One page of people for the picker, from the server and drawn by their faces. */
export async function askPeople(typed: string): Promise<PickPage> {
	const asked = await people.choices(typed);
	return {
		choices: asked.items.map(personRow),
		more: Math.max(0, asked.total - asked.items.length)
	};
}

/* Somebody the library has never heard of, made and then picked: the picker's own contract, and
 * the same two writes the Unnamed faces picker makes. */
export async function makePerson(named: string): Promise<PickChoice> {
	const made = await people.create(named);
	return { id: made.id, name: made.name };
}
