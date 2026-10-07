/* The app saying something happened, without stopping to be told it was heard.
 *
 * The pairing this exists for: a safe thing that worked says so quietly and gets out of the way, and
 * a destructive thing asks first. A toast is the quiet half. It is never how a question gets asked.
 */

import type { HistoryPiece } from '$lib/components/common/history';
import type { ToastWords } from '$lib/components/common/toast-pieces';
import type { IconName } from '$lib/design/icons';

const DISMISS_AFTER_MS = 4000;

/* An error is given longer to be read than a message about something that went right, because a
 * failure is the one worth not missing. It still leaves on its own: a toast that never goes becomes
 * a scrap of the screen somebody has to clear by hand, which is its own small papercut. */
const ERROR_DISMISS_AFTER_MS = 10000;

/** Three at a time. A fourth pushes the oldest out rather than growing a wall of them. */
const MAX_STACKED = 3;

export type ToastTone = 'info' | 'success' | 'error';

/*
 * A SENTENCE BOUNDARY INSIDE THE MESSAGE: a stop, a question mark or an exclamation with white
 * space after it. Anything ending the message itself is not one: there is nothing after it.
 */
const SENTENCE_BREAK = /[.?!]\s/;

/**
 * Whether a message is ONE sentence.
 *
 * The whole of the rule below turns on this, so it is a named function rather than a test written
 * twice: a toast of one sentence carries no full stop and a toast of several keeps every one of
 * theirs. The reason is not tidiness: a single line of feedback that ends in a stop reads as a
 * paragraph somebody has to finish, where the same words without one ("Link copied") read as a
 * label.
 */
export function oneSentence(message: string): boolean {
	return !SENTENCE_BREAK.test(message);
}

/**
 * The message as it is SHOWN: a lone terminal full stop taken off a single sentence.
 *
 * Here and not in the callers because there are about three hundred of them AND because some of
 * the sentences are not written here at all. A refusal the server explains arrives as `detail`
 * and goes through `bulk.announceRefusal` into this same door. A rule applied at the call sites
 * would be a rule the server's own sentences walked straight past.
 *
 * Only a full stop, and only when it is the last character. A question mark and an exclamation are
 * left alone (they carry meaning a stop does not), and a trailing `...` is not a stop at all but
 * a message saying the work is still going on, so it stays whole.
 */
function tidy(message: string): string {
	if (!oneSentence(message)) return message;
	if (message.endsWith('...')) return message;
	return message.endsWith('.') ? message.slice(0, -1) : message;
}

/** The words as the pieces `HistorySentence` draws, the lone stop of one sentence taken off. */
function piecesOf(words: ToastWords): { message: string; pieces: HistoryPiece[] } {
	const runs = (typeof words === 'string' ? [words] : words).map((one) =>
		typeof one === 'string' ? { text: one } : one
	);
	const said = runs.map((one) => one.text).join('');
	const last = runs[runs.length - 1];
	// A name is never cut: only plain words end in a stop that can be taken off.
	const cut = last && !last.kind ? said.length - tidy(said).length : 0;
	if (cut > 0) runs[runs.length - 1] = { text: last.text.slice(0, -cut) };
	const pieces = runs.map((one) => ({
		text: one.text,
		kind: one.kind ?? null,
		id: one.id ?? null,
		href: one.href ?? null,
		gone: false,
		rest: [],
		lead: ''
	}));
	return { message: said.slice(0, said.length - cut), pieces: pieces.filter((one) => one.text) };
}

interface ToastAction {
	label: string;
	run: () => void;
}

/** How far along a piece of work is. `null` is running-but-unmeasured, which draws as a sweep. */
export interface ToastProgress {
	value: number | null;
	max: number;
}

interface Toast {
	id: number;
	message: string;
	tone: ToastTone;
	action?: ToastAction;
	/* A toast that carries this DOES NOT LEAVE ON ITS OWN, and that is the point of it.
	 *
	 * Everything else here announces something that already happened, so four seconds is generous.
	 * Work that is still running is the opposite: the message has to outlast the work, and nobody
	 * knows in advance how long a file takes to come across a network. Whoever started it clears it.
	 * See `settle`, which puts the toast back on an ordinary timer once there is nothing left to
	 * watch. */
	progress?: ToastProgress;
	/** The words as runs, each thing named drawn as the way to it. `message` is them joined. */
	pieces: HistoryPiece[];
	/**
	 * A mark of this toast's own, instead of the one its tone would give it.
	 *
	 * The tone decides the glyph for nearly everything, and should: `info` is an `i`, a failure is a
	 * warning triangle, and a toast that chose its own picture per message would be a hundred small
	 * decisions nobody is making consistently.
	 *
	 * What this is for is the handful of toasts that are not ANNOUNCING something but reporting work
	 * still in flight. "Downloading..." under an `i` says the app has a fact for you; under the
	 * turning download ring it says a thing is happening, which is what it means. Same rule as
	 * `Badge`'s icon override: the override has to add a fact the tone does not have.
	 */
	icon?: IconName;
}

class Toasts {
	#items = $state<Toast[]>([]);
	#nextId = 0;
	/* A running timer, and the moment it is due, as ONE value.
	 *
	 * Two maps that must always agree are two maps that can stop agreeing:
	 * a timer with no due time recorded beside it is a pause that cannot say how much was left, and
	 * there is no way to notice from the outside: the toast simply goes at the wrong moment.
	 * Written together and cleared together because they ARE together.
	 */
	#timers = new Map<number, { handle: ReturnType<typeof setTimeout>; dueAt: number }>();

	/* How long is left on the ones that have been taken off the clock. Keeping the remainder is
	 * the whole of what makes this a PAUSE rather than a reset: a message hovered three seconds
	 * into its four does not get another four when the pointer leaves, and it does not vanish the
	 * instant it does either. */
	#leftOver = new Map<number, number>();

	/* How many things are holding a toast open. A number rather than a flag: the pointer can be
	 * over a toast while the keyboard is inside it, and two holds that each clear the other on
	 * leaving would start the timer again while the thing was still being read. */
	#held = new Map<number, number>();

	get items(): readonly Toast[] {
		return this.#items;
	}

	/**
	 * Say something. Returns the toast's id, so a caller that knows the thing it announced has been
	 * undone can take the message away itself.
	 *
	 * Everything leaves on its own; an error is simply given longer (ten seconds against four)
	 * because a failure nobody saw is a failure the app mentioned to no one. Nothing stays forever: a
	 * message that will not go is a scrap of the screen somebody has to reach up and clear.
	 */
	show(
		words: ToastWords,
		options: {
			tone?: ToastTone;
			action?: ToastAction;
			progress?: ToastProgress;
			icon?: IconName;
		} = {}
	): number {
		const tone = options.tone ?? 'info';
		const toast: Toast = {
			id: this.#nextId++,
			...piecesOf(words),
			tone,
			action: options.action,
			progress: options.progress,
			icon: options.icon
		};

		this.#items = [...this.#items, toast];

		// Over capacity, the oldest thing that went RIGHT goes first.
		//
		// Oldest-first alone would lose the message worst placed to survive. An error's own timer is
		// the long one, but three cheerful messages arriving in a second could still push it off the
		// end well before that, exactly when things are going wrong and more of them are landing.
		// Only when every one of them is an error does the oldest error go, and by then the screen is
		// saying enough.
		while (this.#items.length > MAX_STACKED) {
			const goes = this.#items.find((toast) => toast.tone !== 'error') ?? this.#items[0];
			this.dismiss(goes.id);
		}

		if (toast.progress === undefined) this.#expire(toast.id, tone);
		return toast.id;
	}

	/**
	 * Move a running toast along. Ignored for a toast that has already gone, so a fetch that
	 * finishes after somebody dismissed its message does not bring the message back.
	 */
	advance(id: number, progress: ToastProgress, words?: ToastWords): void {
		this.#items = this.#items.map((toast) =>
			toast.id === id
				? { ...toast, progress, ...(words === undefined ? {} : piecesOf(words)) }
				: toast
		);
	}

	/**
	 * The work is over. The bar goes, the wording becomes the outcome, and the toast starts the
	 * ordinary timer it never had, so a finished job leaves by itself like everything else.
	 */
	settle(id: number, words: ToastWords, tone: ToastTone = 'info'): void {
		if (!this.#items.some((toast) => toast.id === id)) return;
		this.#items = this.#items.map((toast) =>
			toast.id === id ? { ...toast, ...piecesOf(words), tone, progress: undefined } : toast
		);
		this.#expire(id, tone);
	}

	/**
	 * Keep a toast on screen for as long as somebody is reading it.
	 *
	 * The pointer being on it, or the keyboard being inside it: both are somebody attending to the
	 * message, and both are why it must not leave under them. The keyboard half is not a nicety:
	 * these are the only place an undo is ever offered, so a toast that goes while somebody is
	 * tabbing towards its button takes the action with it.
	 *
	 * A toast still doing work has no timer at all and needs no holding; see `progress`.
	 */
	hold(id: number): void {
		this.#held.set(id, (this.#held.get(id) ?? 0) + 1);
		if ((this.#held.get(id) ?? 0) > 1) return;
		const running = this.#timers.get(id);
		/* Nothing to stop. A toast still doing work has no timer at all (see `progress`), so
		   holding one is not a special case to handle, it is simply nothing happening. What it must
		   NOT do is bank a remainder, or settling under the pointer would start a clock. */
		if (running === undefined) return;
		clearTimeout(running.handle);
		this.#timers.delete(id);
		this.#leftOver.set(id, Math.max(0, running.dueAt - Date.now()));
	}

	/** Let it go again, and give it back exactly what it had left. */
	release(id: number): void {
		const holds = (this.#held.get(id) ?? 0) - 1;
		if (holds > 0) {
			this.#held.set(id, holds);
			return;
		}
		this.#held.delete(id);
		const left = this.#leftOver.get(id);
		if (left === undefined) return;
		this.#leftOver.delete(id);
		this.#arm(id, left);
	}

	#expire(id: number, tone: ToastTone): void {
		this.#arm(id, tone === 'error' ? ERROR_DISMISS_AFTER_MS : DISMISS_AFTER_MS);
	}

	/**
	 * Put one toast on the clock for a given number of milliseconds.
	 *
	 * The one place a dismissal timer is started, so the bookkeeping that a pause depends on cannot
	 * be right in one place and missing in another. A toast armed while something is holding it is
	 * banked instead: that happens when a running job settles under the pointer, and starting its
	 * timer there would be the one message about work most worth reading leaving fastest.
	 */
	#arm(id: number, after: number): void {
		const existing = this.#timers.get(id);
		if (existing !== undefined) clearTimeout(existing.handle);
		this.#timers.delete(id);
		if ((this.#held.get(id) ?? 0) > 0) {
			this.#leftOver.set(id, after);
			return;
		}
		this.#timers.set(id, {
			handle: setTimeout(() => this.dismiss(id), after),
			dueAt: Date.now() + after
		});
	}

	dismiss(id: number): void {
		const running = this.#timers.get(id);
		if (running !== undefined) {
			clearTimeout(running.handle);
			this.#timers.delete(id);
		}
		this.#leftOver.delete(id);
		this.#held.delete(id);
		this.#items = this.#items.filter((toast) => toast.id !== id);
	}

	/** For tests, and for a screen that is tearing down and has nothing left to say. */
	clear(): void {
		for (const running of this.#timers.values()) clearTimeout(running.handle);
		this.#timers.clear();
		this.#leftOver.clear();
		this.#held.clear();
		this.#items = [];
	}
}

export const toasts = new Toasts();
export { DISMISS_AFTER_MS, ERROR_DISMISS_AFTER_MS, MAX_STACKED };
export type { Toast };
