/** One read out at a time, and one more after it for everybody who asked meanwhile. */
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
