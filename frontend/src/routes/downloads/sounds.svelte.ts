/* WHY NOT FOLLOWED: routes/downloads/+page.svelte watches `settingChanges` and calls `load` again.
   `Sounds` is built per screen (it owns an AudioContext, which is a thing a page may not have
   until somebody has clicked), so the screen that builds one is what can re-read for it. */
import type { components } from '$lib/api/schema';
import { fetchSettings } from '$lib/settings-ui/settings';

/* A short sound when something finishes, if somebody asked for one. */

/** What can make a noise. All of them, once sounds are on at all: a per-event list nobody
 *  ever filters is four more controls for nothing, and turning the lot off is one click. */
const EVENTS = {
	done: 'A download finishes',
	failed: 'A download fails',
	blocked: 'A download needs cookies',
	empty: 'The whole queue finishes'
} as const;

type SoundEvent = keyof typeof EVENTS;

/** The shape of each tone: how high, how long, and whether it rises or falls. */
const TONES: Record<SoundEvent, { from: number; to: number; seconds: number }> = {
	done: { from: 660, to: 880, seconds: 0.12 },
	failed: { from: 440, to: 300, seconds: 0.18 },
	blocked: { from: 520, to: 520, seconds: 0.1 },
	empty: { from: 660, to: 990, seconds: 0.22 }
};

/** The two preferences this reads, by the keys the server declared them under. */
const ON = 'download.sound';
const VOLUME = 'download.sound_volume';

export class Sounds {
	/** Whether anything plays at all. Off until somebody turns it on, and off for everybody else. */
	on = $state(false);
	/** How loud, from 0 to 1. Stored as a percentage, because a percentage is drawn as a slider. */
	volume = $state(0.4);

	#context: AudioContext | null = null;

	/** Take the current preferences. Called when the screen opens; a failure leaves sounds off,
	 *  which is the safe direction: silence nobody asked about is not a fault. */
	async load(): Promise<void> {
		try {
			const sections = await fetchSettings();
			for (const section of sections) {
				for (const entry of section.settings) {
					if (entry.key === ON) this.on = entry.value === true;
					if (entry.key === VOLUME && typeof entry.value === 'number') {
						this.volume = Math.min(1, Math.max(0, entry.value / 100));
					}
				}
			}
		} catch {
			// Silent, in both senses. The queue works whether or not it can make a noise.
		}
	}

	/** Make the noise for one event, if that event is meant to make one. */
	play(event: SoundEvent): void {
		if (!this.on) return;
		try {
			this.#tone(TONES[event]);
		} catch {
			// A browser that will not make a noise. The badge already said it.
		}
	}

	#tone({ from, to, seconds }: { from: number; to: number; seconds: number }): void {
		// Built on first use rather than at construction: making one before any interaction leaves
		// it suspended in most browsers, and a suspended context stays that way.
		this.#context ??= new AudioContext();
		const context = this.#context;
		const now = context.currentTime;

		const oscillator = context.createOscillator();
		const gain = context.createGain();
		oscillator.type = 'sine';
		oscillator.frequency.setValueAtTime(from, now);
		oscillator.frequency.linearRampToValueAtTime(to, now + seconds);

		// Faded in and out rather than switched. A tone that starts and stops abruptly clicks, and
		// the click is louder than the tone.
		gain.gain.setValueAtTime(0, now);
		gain.gain.linearRampToValueAtTime(this.volume, now + 0.01);
		gain.gain.linearRampToValueAtTime(0, now + seconds);

		oscillator.connect(gain).connect(context.destination);
		oscillator.start(now);
		oscillator.stop(now + seconds + 0.02);
	}
}

/** One download settling, as the screen hears about it. */
export interface Landing {
	kind: SoundEvent;
	/** How many settled this way in the same look. */
	count: number;
	/** The file it produced, and what it is called, for a landing that is ONE download. */
	assetId: string | null;
	filename: string | null;
}

/** What a look needs to know about a row. */
type Settling = Pick<
	components['schemas']['DownloadItem'],
	'id' | 'status' | 'asset_id' | 'filename'
>;

/** Watches a list of downloads and says what just changed, so a caller can make a noise about it. */
export class Changes {
	#seen = new Map<string, string>();
	#started = false;

	/** What settled since the last look. Empty on the first look, deliberately: opening the screen
	 *  is not the moment to be told about everything that ever happened. */
	since(items: Settling[]): Landing[] {
		const landed = new Map<SoundEvent, Settling[]>();
		const active = new Set(['queued', 'running']);
		const kinds: Record<string, SoundEvent> = {
			done: 'done',
			failed: 'failed',
			blocked: 'blocked'
		};

		for (const item of items) {
			const before = this.#seen.get(item.id);
			this.#seen.set(item.id, item.status);
			// A row seen for the first time is not a transition either, whatever its state: the
			// list arrives after the screen's first empty look, and opening the page must not
			// announce every finished download at the same time.
			if (!this.#started || before === undefined || before === item.status) continue;
			const kind = kinds[item.status];
			if (!kind) continue;
			const together = landed.get(kind);
			if (together) together.push(item);
			else landed.set(kind, [item]);
		}

		// The queue emptying is its own event and the one most worth hearing: it is the moment
		// somebody can walk away.
		const busyNow = items.some((item) => active.has(item.status));
		if (this.#started && !busyNow && this.#wasBusy && landed.size > 0) landed.set('empty', []);
		this.#wasBusy = busyNow;
		this.#started = true;

		// One of each. Twenty downloads finishing in the same second is one sound, not twenty, and
		// one message, which is why the count comes with it rather than being counted again outside.
		return [...landed].map(([kind, rows]) => ({
			kind,
			count: rows.length,
			assetId: rows.length === 1 ? (rows[0].asset_id ?? null) : null,
			filename: rows.length === 1 ? (rows[0].filename ?? null) : null
		}));
	}

	#wasBusy = false;
}
