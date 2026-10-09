/* The app saying something happened: the quiet half; never how a question is asked. */

import type { HistoryPiece } from '$lib/components/common/history';
import type { ToastWords } from '$lib/components/common/toast-pieces';
import type { IconName } from '$lib/design/icons';

const DISMISS_AFTER_MS = 4000;

/* An error stays longer, but still leaves on its own. */
const ERROR_DISMISS_AFTER_MS = 10000;

const MAX_STACKED = 3;

export type ToastTone = 'info' | 'success' | 'error';

const SENTENCE_BREAK = /[.?!]\s/;

/** One sentence carries no full stop: a lone line ending in one reads as a paragraph. */
export function oneSentence(message: string): boolean {
	return !SENTENCE_BREAK.test(message);
}

/**
 * Applied here because the server's sentences come through here too. Only a final full stop; `?`,
 * `!` and `...` stay.
 */
function tidy(message: string): string {
	if (!oneSentence(message)) return message;
	if (message.endsWith('...')) return message;
	return message.endsWith('.') ? message.slice(0, -1) : message;
}

function piecesOf(words: ToastWords): { message: string; pieces: HistoryPiece[] } {
	const runs = (typeof words === 'string' ? [words] : words).map((one) =>
		typeof one === 'string' ? { text: one } : one
	);
	const said = runs.map((one) => one.text).join('');
	const last = runs[runs.length - 1];
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

/** `null` draws a sweep. */
export interface ToastProgress {
	value: number | null;
	max: number;
}

interface Toast {
	id: number;
	message: string;
	tone: ToastTone;
	action?: ToastAction;
	/* Does NOT leave on its own: the message must outlast the work. See `settle`. */
	progress?: ToastProgress;
	pieces: HistoryPiece[];
	/** For a toast reporting work in flight, where the tone's glyph would be wrong. */
	icon?: IconName;
}

class Toasts {
	#items = $state<Toast[]>([]);
	#nextId = 0;
	/* One value, so a pause can always say how much was left. */
	#timers = new Map<number, { handle: ReturnType<typeof setTimeout>; dueAt: number }>();

	/* The remainder makes this a PAUSE, not a reset. */
	#leftOver = new Map<number, number>();

	/* A count: the pointer and the keyboard can both hold one. */
	#held = new Map<number, number>();

	get items(): readonly Toast[] {
		return this.#items;
	}

	/** Returns the id, so a caller can take the message away; an error is given ten seconds. */
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

		// Over capacity, the oldest that went RIGHT goes first.
		while (this.#items.length > MAX_STACKED) {
			const goes = this.#items.find((toast) => toast.tone !== 'error') ?? this.#items[0];
			this.dismiss(goes.id);
		}

		if (toast.progress === undefined) this.#expire(toast.id, tone);
		return toast.id;
	}

	/** Ignored for a toast already gone. */
	advance(id: number, progress: ToastProgress, words?: ToastWords): void {
		this.#items = this.#items.map((toast) =>
			toast.id === id
				? { ...toast, progress, ...(words === undefined ? {} : piecesOf(words)) }
				: toast
		);
	}

	settle(id: number, words: ToastWords, tone: ToastTone = 'info'): void {
		if (!this.#items.some((toast) => toast.id === id)) return;
		this.#items = this.#items.map((toast) =>
			toast.id === id ? { ...toast, ...piecesOf(words), tone, progress: undefined } : toast
		);
		this.#expire(id, tone);
	}

	/** The pointer on it or the keyboard inside it: the only undo must not leave under somebody. */
	hold(id: number): void {
		this.#held.set(id, (this.#held.get(id) ?? 0) + 1);
		if ((this.#held.get(id) ?? 0) > 1) return;
		const running = this.#timers.get(id);
		/* A running toast has no timer; a remainder banked here would start one. */
		if (running === undefined) return;
		clearTimeout(running.handle);
		this.#timers.delete(id);
		this.#leftOver.set(id, Math.max(0, running.dueAt - Date.now()));
	}

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

	/** The one place a timer starts; armed while held, it is banked instead. */
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
