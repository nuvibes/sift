import { isMissing } from '$lib/api/client';
import { reloadOnLibraryChange } from '$lib/library/changes.svelte';

/* The one row an entity detail page is about: fetching it, re-fetching it, and what the screen
 * says while there is no answer yet. */
export class EntitySubject<T> {
	#fetch: (id: string) => Promise<T>;

	/** The row itself, or `null` when there is none to show. */
	value = $state<T | null>(null);

	/** Whether the first answer for the CURRENT subject is still out. */
	settling = $state(true);

	/** A failure that is NOT the thing being gone. Kept apart because they are different
	 * sentences and only one of them is about the library. */
	unreadable = $state(false);

	/** Rising, so a slow answer for the subject somebody has navigated AWAY from cannot land
	 * under the name of the one they are now on. */
	#wanted = 0;

	/** Which id `value` belongs to, which is what tells a re-read from a change of subject. */
	#shown: string | null = null;

	constructor(fetch: (id: string) => Promise<T>) {
		this.#fetch = fetch;
	}

	/** Follow one id for as long as this screen is open, through every library change under it. */
	follow(id: () => string): void {
		$effect(() => {
			void this.load(id());
		});
		/* Already untracked by the helper, which is what keeps this from depending on the very
		   state it is about to write. */
		reloadOnLibraryChange(() => void this.load(id()));
	}

	/** Fetch it, once, and settle the three flags together. */
	async load(id: string): Promise<void> {
		if (!id) {
			this.value = null;
			this.#shown = null;
			this.settling = false;
			return;
		}
		const mine = ++this.#wanted;
		if (id !== this.#shown) {
			this.value = null;
			this.#shown = null;
			this.settling = true;
		}
		try {
			const found = await this.#fetch(id);
			if (mine !== this.#wanted) return;
			this.value = found;
			this.#shown = id;
			/* Put back on success. A page that failed once and then read perfectly well must not
			   go on saying it could not be loaded. */
			this.unreadable = false;
		} catch (error) {
			if (mine !== this.#wanted) return;
			this.value = null;
			this.#shown = null;
			this.unreadable = !isMissing(error);
		} finally {
			if (mine === this.#wanted) this.settling = false;
		}
	}
}

/* One answer per kind, asked before the part that draws it is mounted; that part's first read
   takes it rather than asking again, so the part draws its answer on its first frame. */
const primed = new Map<string, { id: string; answer: Promise<unknown> }>();

/** Ask now, for a reading a part not yet drawn will want. See `primedOr`. */
export function prime<T>(kind: string, id: string, answer: Promise<T>): Promise<T> {
	primed.set(kind, { id, answer });
	return answer;
}

/** The answer primed for this subject, taken once, or else a fresh ask. */
export function primedOr<T>(kind: string, id: string, ask: () => Promise<T>): Promise<T> {
	const held = primed.get(kind);
	if (held?.id !== id) return ask();
	primed.delete(kind);
	return held.answer as Promise<T>;
}

/** One READING about a subject (a count, a score) kept on screen while it is re-read. */
export function readingAbout<T>(
	subject: () => string,
	ask: (id: string) => Promise<T>,
	nothing: T,
	also: () => unknown = () => undefined
): { readonly value: T } {
	let held = $state<T>(nothing);
	/** Who the answer on screen belongs to, which is what tells a re-read from a new subject. */
	let about: string | null = null;

	$effect(() => {
		const wanted = subject();
		also();
		if (wanted !== about) {
			held = nothing;
			about = null;
		}
		void ask(wanted)
			.then((found) => {
				// A slower answer for a subject navigated away from must not land under the new one's
				// name: it would be a number about somebody else, drawn as though it were theirs.
				if (wanted !== subject()) return;
				held = found;
				about = wanted;
			})
			.catch(() => {
				// One line on a screen that is about something else.
			});
	});

	return {
		get value() {
			return held;
		}
	};
}
