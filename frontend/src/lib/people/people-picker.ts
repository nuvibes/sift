/*
 * The people picker's two questions ("who is on this page of people?" and "make somebody new"),
 * answered once, for every picker that says who a USERNAME belongs to.
 *
 * ## Why a module and not a copy in each caller
 *
 * "Who is this?" on Usernames Waiting and "Choose..." in a username's sheet are the SAME act (say
 * who posts under this username), so they open the same picker: `PickMenu`, over the whole library,
 * each person drawn by their face, its create row making somebody new from what was typed. Two
 * copies of what it asks would be two lists that could come to disagree about who is offered. Both
 * username screens read these two functions; the face pickers still carry their own `askPeople`,
 * word for word the same.
 */

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

/*
 * Somebody the library has never heard of, made and then picked: the picker's own contract, and
 * the same two writes the Faces to name picker makes. The server's `new_person_name` branch would
 * make and join in one request, but only by stepping outside the picker: the create row would stop
 * being the same act it is in every other picker in the application.
 */
export async function makePerson(named: string): Promise<PickChoice> {
	const made = await people.create(named);
	return { id: made.id, name: made.name };
}
