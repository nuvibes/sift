/** How many stars a rating is DRAWN as, and the one place that converts to what is stored. */
import { fetchSettingValues, onSettingsSaved } from '$lib/settings-ui/settings';

/* The key the scale is stored under, which the Appearance pane names too. */
export const RATING_SCALE_KEY = 'ratings.scale';

/** The scales offered. The server declares the same two and refuses anything else. */
const SCALES = [5, 10] as const;
type Scale = (typeof SCALES)[number];

/** What a library that has never been asked draws. The same default the server declares. */
const DEFAULT_SCALE: Scale = 5;

/** How many stars are stored at most. Not a scale: the storage, which never changes. */
const STORED_MAX = 10;

function asScale(value: unknown): Scale {
	const found = SCALES.find((one) => String(one) === String(value));
	return found ?? DEFAULT_SCALE;
}

class RatingScale {
	/** How many stars to draw. Five or ten. */
	stars = $state<Scale>(DEFAULT_SCALE);

	#loading: Promise<void> | null = null;

	/* How many times these answers have been thrown away. */
	#discarded = 0;

	/** Read it from the server, once per page load. Repeated calls join the request in flight. */
	load(): Promise<void> {
		if (this.#loading) return this.#loading;
		const asked = this.#discarded;
		this.#loading = (async () => {
			try {
				const values = await fetchSettingValues();
				if (this.#discarded !== asked) return;
				this.stars = asScale(values.get(RATING_SCALE_KEY));
			} catch {
				// A preference that could not be read is not worth a message.
			}
		})();
		return this.#loading;
	}

	/** Drop what was read, because it belonged to somebody else. */
	forget(): void {
		this.#discarded += 1;
		this.#loading = null;
		this.stars = DEFAULT_SCALE;
	}

	/** A stored rating as the number of stars to show, or null when there is no rating. */
	shown(stored: number | null | undefined): number | null {
		if (stored === null || stored === undefined) return null;
		return this.stars === STORED_MAX ? stored : Math.ceil(stored / 2);
	}

	/** A number of stars somebody picked, as the rating to store. Null clears it, and stays null. */
	stored(picked: number | null): number | null {
		if (picked === null) return null;
		return this.stars === STORED_MAX ? picked : picked * 2;
	}

	/** A typed query with each `rating:` clause moved to stored units, by the rule `stored`
	   applies. */
	storedQuery(query: string): string {
		if (this.stars === STORED_MAX) return query;
		return query.replace(
			/\brating:(\d+)(?:-(\d+))?(\+?)/gi,
			(_whole, low: string, high: string | undefined, plus: string) => {
				const from = this.stored(Number(low));
				const to = high === undefined ? null : this.stored(Number(high));
				return `rating:${from}${to === null ? '' : `-${to}`}${plus}`;
			}
		);
	}

	/** Whether a stored rating and a shown one mean the same thing. */
	isShowing(stored: number | null | undefined, star: number): boolean {
		return this.shown(stored) === star;
	}
}

export const ratingScale = new RatingScale();

/* Take effect behind the settings sheet rather than at the next page load. */
onSettingsSaved((saved) => {
	if (RATING_SCALE_KEY in saved) ratingScale.stars = asScale(saved[RATING_SCALE_KEY]);
});
