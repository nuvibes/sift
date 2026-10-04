/**
 * How many stars a rating is DRAWN as, and the one place that converts between that and what is
 * stored.
 *
 * ## What is stored is always out of ten
 *
 * The scale is a display preference. Every rating on the wire and in the database is 1..10,
 * whichever scale is on screen, and that is what makes the setting free: switching it never
 * rewrites a row, and switching back shows exactly what somebody meant the first time. A scale
 * that decided the stored number would have to migrate the whole library on every change, and
 * five-and-back could not recover an odd value: there is no half star to put it back as.
 *
 * ## Why the conversion is here and not at each star
 *
 * Six screens draw a rating and three of them can set one. A conversion written at each is six
 * copies of one rule, and the copy that drifts is the one nobody looks at, which in this case
 * would show one number on a card and a different one on the page behind it. So the rule is here,
 * the components take and give the SHOWN number, and nothing else in the client does the
 * arithmetic.
 */
import { fetchSettingValues, onSettingsSaved } from '$lib/settings-ui/settings';

/* The key the scale is stored under.
 *
 * Exported because the Appearance pane draws its row and has to name it. That pane draws named
 * keys rather than a whole section, so a setting nothing names is a setting nothing draws:
 * registered, saved, read by every star on screen, and on no screen anybody could reach. A gate
 * holds that shut; this is the naming it asks for.
 */
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

	/* How many times these answers have been thrown away. Only ever compared with itself.
	 *
	 * The same guard `Theme` carries, for the same reason: a read that was in the air when the
	 * account changed describes the PREVIOUS account's preferences, and letting it land would show
	 * one person's answers on another person's screen until the second read finished.
	 */
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
				// A preference that could not be read is not worth a message. Five stars is the
				// default, and every screen is usable at either scale.
			}
		})();
		return this.#loading;
	}

	/** Drop what was read, because it belonged to somebody else.
	 *
	 * Called from `account-scoped.ts` when the signed-in account changes, and nowhere else. Without
	 * it the memo above means "read once per PAGE" rather than once per account, and signing out
	 * and back in without a reload (which is what the desktop shell always does, because its page
	 * never reloads) would leave the previous account's answers in place for good.
	 */
	forget(): void {
		this.#discarded += 1;
		this.#loading = null;
		this.stars = DEFAULT_SCALE;
	}

	/**
	 * A stored rating as the number of stars to show, or null when there is no rating.
	 *
	 * Rounded UP on a five-star screen, deliberately. Seven out of ten is nearer to four stars than
	 * to three, and rounding down would make a rating somebody gave look like a lower one than they
	 * gave, which is the direction that matters, because the whole point of a rating is to say
	 * something was good.
	 */
	shown(stored: number | null | undefined): number | null {
		if (stored === null || stored === undefined) return null;
		return this.stars === STORED_MAX ? stored : Math.ceil(stored / 2);
	}

	/** A number of stars somebody picked, as the rating to store. Null clears it, and stays null. */
	stored(picked: number | null): number | null {
		if (picked === null) return null;
		return this.stars === STORED_MAX ? picked : picked * 2;
	}

	/**
	 * A typed query with each `rating:` clause moved from the stars on screen to the units the
	 * rows hold, through the same rule `stored` applies to a picked star.
	 *
	 * The server binds the number straight to the stored column and the facet panel converts
	 * before it asks; so must the box, or at five stars `rating:5` would answer nothing (nothing is
	 * stored above ten, and five stars is ten) and `rating:4+` would mean seven stored, which shows as
	 * four stars and is three and a half. A range is converted at both ends. At ten stars the
	 * shown scale is the stored one, so nothing moves.
	 */
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

	/**
	 * Whether a stored rating and a shown one mean the same thing.
	 *
	 * What a chooser asks to mark the current row. `===` on the two numbers is wrong at five stars:
	 * seven and eight both show as four, so a file rated seven would have no row marked at all.
	 */
	isShowing(stored: number | null | undefined, star: number): boolean {
		return this.shown(stored) === star;
	}
}

export const ratingScale = new RatingScale();

/* Take effect behind the settings sheet rather than at the next page load.
 *
 * Registered at module scope, like the record registry's watcher: the sheet sits OVER the wall the
 * scale changes, so the proof of the switch is two inches away and would otherwise be stale.
 */
onSettingsSaved((saved) => {
	if (RATING_SCALE_KEY in saved) ratingScale.stars = asScale(saved[RATING_SCALE_KEY]);
});
