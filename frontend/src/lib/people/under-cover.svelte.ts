/*
 * What a person's page draws under the cover for an admin, asked beside the row so the header is
 * drawn once at its full height: drawn later, it lengthened the header under a wall that had
 * already measured its box. Each answer is primed for the part that draws it (`primedOr`).
 */
import { heldFaces } from '$lib/components/swap/swap';
import { prime } from '$lib/entity/subject.svelte';
import { identifiedForPerson, recognitionOf } from '$lib/people/faces.svelte';
import { session } from '$lib/shell/session.svelte';

/** Whether the reads under the cover have answered for the person `subject` names. */
export function underCover(subject: () => string): { readonly ready: boolean } {
	let readFor = $state<string | null>(null);
	$effect(() => {
		const id = subject();
		const asked = session.isAdmin
			? Promise.allSettled([
					prime('identified', id, identifiedForPerson(id, { limit: 1, offset: 0 })),
					prime('recognition', id, recognitionOf(id)),
					prime('held', id, heldFaces(id))
				])
			: Promise.resolve();
		void asked.then(() => {
			if (id === subject()) readFor = id;
		});
	});
	return {
		get ready() {
			return readFor === subject();
		}
	};
}
