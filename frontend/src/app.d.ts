declare global {
	namespace App {
		/**
		 * State attached to a history entry rather than to the address.
		 *
		 * `asset` is set when an asset was opened from a grid: the address becomes /asset/{id} while
		 * the grid stays mounted underneath, and this is what tells the layout to draw the modal over
		 * it. Absent on a direct hit or a refresh, which is what makes that the standalone page.
		 *
		 * `settings` is the same idea for the same reason: reached from inside the app, settings is a
		 * panel over the screen somebody was already on, at the section's own address. Opened at that
		 * address directly, it is a page.
		 */
		interface PageState {
			/**
			 * Whether this panel was opened at its own address rather than from inside the app.
			 *
			 * Set when a cold load or a refresh landed straight on a panel's address, so there is no
			 * screen behind it. Closing then goes to the library instead of stepping back, because a
			 * step back from the first entry leaves Sift altogether.
			 */
			direct?: boolean;
			asset?: string;
			/**
			 * A moment to open that asset at, in milliseconds, when it was opened from something
			 * that knows one: a face in a group of unidentified faces. The same number is in the
			 * address as `?t=`, so a refresh or a pasted link lands in the same place.
			 */
			at?: number;
			/**
			 * Where the stretch being opened ENDS, in milliseconds, for something opened from a
			 * saved loop rather than from a point.
			 *
			 * With both, the player repeats that range instead of running on to the end of the
			 * file. That is the difference between opening a loop and opening a bookmark with a duration
			 * printed on it. In the address as `&until=` beside `?t=`, so a refresh or a pasted
			 * link still opens the stretch.
			 */
			until?: number;
			settings?: string;
		}
	}
}

export {};
