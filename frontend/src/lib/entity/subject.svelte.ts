import { isMissing } from '$lib/api/client';
import { reloadOnLibraryChange } from '$lib/library/changes.svelte';

/*
 * The one row an entity detail page is about: fetching it, re-fetching it, and what the screen says
 * while there is no answer yet.
 *
 * ## Why this is shared rather than written per page
 *
 * Every entity page (a person, a site, a tag, a photo set) needs the same rising counter, the
 * same three flags and the same `finally`, apart from the call that fetches and the noun in the
 * message. That is the shape that drifts: a rule in one copy would have to be remembered in the
 * others, and the one nobody remembers is the one that keeps the fault.
 *
 * ## What it decides, and it is one thing
 *
 * **A placeholder is for a screen with NOTHING on it.** Re-reading a page that already has its
 * answer leaves the answer where it is until a fresher one arrives.
 *
 * Every write on a person announces a library-wide change, so hearting them or changing their cover
 * makes their own page re-read. And a re-read that put the word "Loading..." over the whole
 * screen and then rebuilt it would be indistinguishable from somebody pressing F5, on every detail
 * page.
 *
 * Nothing about the row on screen has stopped being true while a fresher copy is on its way. What
 * IS worth a placeholder is a different subject: navigating from one person to another must not
 * leave the first one's name and picture up under the second one's address, so a change of id
 * clears the screen and a re-read of the same id does not.
 */
export class EntitySubject<T> {
	#fetch: (id: string) => Promise<T>;

	/**
	 * The row itself, or `null` when there is none to show.
	 *
	 * Public and writable on purpose: a page that saves an edit gets the updated row back from the
	 * server and assigns it here, which is one round trip rather than two and is the only way the
	 * screen and the database cannot disagree about what was just written.
	 */
	value = $state<T | null>(null);

	/**
	 * Whether the first answer for the CURRENT subject is still out.
	 *
	 * Never true for a re-read of something already on screen. See the note above the class. It
	 * starts true because a page that has asked for nothing yet has nothing to draw, and the
	 * alternative is a frame of "That person is not here" before the request has even left.
	 */
	settling = $state(true);

	/**
	 * A failure that is NOT the thing being gone.
	 *
	 * Kept apart because they are different sentences and only one of them is about the library.
	 * Saying something has gone when the request was refused or the server was briefly unreachable
	 * is the most alarming thing a screen can say, and the reader has no way to tell it from the
	 * truth. Only a 404 is a fact about the library. See `isMissing`.
	 */
	unreadable = $state(false);

	/** Rising, so a slow answer for the subject somebody has navigated AWAY from cannot land under
	 *  the name of the one they are now on. Without it the previous row appears under this row's
	 *  heading, and acting on what it shows acts on the wrong thing. */
	#wanted = 0;

	/** Which id `value` belongs to, which is what tells a re-read from a change of subject. */
	#shown: string | null = null;

	constructor(fetch: (id: string) => Promise<T>) {
		this.#fetch = fetch;
	}

	/**
	 * Follow one id for as long as this screen is open, through every library change under it.
	 *
	 * Call it during component setup, like any other rune helper. Two subscriptions rather than one
	 * because they answer different questions: the first is "the address moved", the second is
	 * "what this account may see moved": a share taken back, a rename, a cover, a heart. Without
	 * the second, a page would go on showing a name somebody had just changed until it was reloaded
	 * by hand.
	 */
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
			   go on saying it could not be loaded. And with a re-read arriving on every
			   library change, that would be a sentence somebody sat looking at. */
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

/* One answer per kind, asked before the part that draws it is mounted; that part's first read takes
   it rather than asking again, so the part draws its answer on its first frame. */
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

/**
 * One READING about a subject (a count, a score) kept on screen while it is re-read.
 *
 * The same rule `EntitySubject` holds, for the things beside the row rather than the row itself: a
 * placeholder belongs on a screen with nothing on it, so asking again about the same subject leaves
 * the last answer up and asking about a different one clears it immediately.
 *
 * A component that clears its state at the top of its effect, and is drawn behind an `{#if}` on
 * that state, is torn out of the page and rebuilt on every library write: a person's page
 * flashing even once the page itself does not. Written once here, the rule is not a chance per
 * component to write it wrongly, and these readings are small enough that the rule is most of what
 * they are.
 *
 * `also` is anything else that should make it ask again: a counter a sibling bumps when it
 * changes something this reads. Bumping it re-asks WITHOUT clearing, which is the point: agreeing
 * to a face should update the number beside it, not make the line disappear and come back.
 */
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
				// One line on a screen that is about something else. The page is still worth reading
				// when this could not be asked for.
			});
	});

	return {
		get value() {
			return held;
		}
	};
}
