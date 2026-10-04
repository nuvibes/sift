/**
 * One read out at a time, and one more after it for everybody who asked meanwhile.
 *
 * The jobs bell rings for every job that moves, so a busy queue asks the same question several
 * times a second. Sent side by side, those reads pile up on a server already slow to answer them,
 * and the last to land is not always the newest. Here an ask that comes while a read is out waits
 * for that read and one more: the one more answers every ask that came in between, and each ask's
 * promise settles only once a read begun after it has landed.
 */
export class OneRead {
	#read: () => Promise<void>;
	#reading: Promise<void> | null = null;
	#again = false;

	constructor(read: () => Promise<void>) {
		this.#read = read;
	}

	/** Read now, or join the read out and the one after it. */
	ask(): Promise<void> {
		if (this.#reading) {
			this.#again = true;
			return this.#reading;
		}
		this.#reading = (async () => {
			do {
				this.#again = false;
				await this.#read();
			} while (this.#again);
		})().finally(() => {
			this.#reading = null;
		});
		return this.#reading;
	}

	/** Read once more after this read, from inside it (a page past the end, read again at the last). */
	again(): void {
		this.#again = true;
	}

	/** Drop the read owed after the one out: nobody it was owed to is listening any more. */
	forget(): void {
		this.#again = false;
	}
}
