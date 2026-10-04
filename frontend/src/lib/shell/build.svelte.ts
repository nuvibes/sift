/* Whether the page you are looking at is still the page this server would serve.
 *
 * ## The fault
 *
 * In a browser, the next reload after an upgrade picks the new client up. In the desktop client
 * there is no reload: a window opened months ago is still running the page it loaded then, and
 * the only way anybody would find out is to quit the application and start it again.
 *
 * ## Why the live connection does not already solve this
 *
 * The live connection carries CHANGES TO DATA (a file appeared, a job finished, a share was
 * taken back), and the screens re-ask the ordinary endpoints when it rings. Every one of those
 * messages is handled by the code that is already running. New code is not data: there is no
 * message that can make a running page become a different page, and the only thing that ever
 * could is a reload.
 *
 * So what is missing is not a push. It is somebody NOTICING, and then somebody deciding.
 *
 * ## What this does
 *
 * It remembers which build it was loaded against, and re-asks whenever the live connection comes
 * back, which is the exact moment the answer can have changed, because a server that restarted
 * is a server whose socket dropped. When the two differ the shell offers a reload; it does not
 * take one. A window that reloads itself while somebody is halfway through typing a filter has
 * traded one annoyance for a worse one, and the person is the only one who knows whether now is a
 * good moment.
 *
 * A browser gets the banner too, and should: a tab left open across an upgrade is in exactly the
 * same position, and "press F5" is only obvious to somebody who already knows what happened.
 */

import { api } from '$lib/api/client';
import type { components } from '$lib/api/schema';

/* LIVE: followed by routes/+layout.svelte (asks again each time the live connection comes up, the one moment a new server can have started) */

type VersionReport = components['schemas']['VersionReport'];

export class BuildWatch {
	/** The build this page was loaded against. Null until the first answer lands. */
	#loaded = $state<string | null>(null);

	/** True once the server would serve a different client than the one running here. */
	stale = $state(false);

	/** What the server said it was running the moment this page started. For a test. */
	get loaded(): string | null {
		return this.#loaded;
	}

	/**
	 * Ask what the server is serving, and compare it with what we started from.
	 *
	 * The FIRST answer is the baseline and can never be stale: whatever the server says at that
	 * point is, by definition, what this page is running. Every answer after it is a comparison.
	 *
	 * An empty build is a server with no client built into it, which is a source checkout being
	 * developed against. There is nothing to compare and nothing worth saying, so it is ignored
	 * rather than treated as a change. Otherwise every developer would be told to reload on the
	 * first connection.
	 */
	async check(): Promise<void> {
		let report: VersionReport;
		try {
			report = await api.get<VersionReport>('/update/version');
		} catch {
			// Not signed in yet, or the server is not answering. Either way the next reconnect asks
			// again, and being wrong here would put a banner on the screen for a network blip.
			return;
		}
		const build = report.build ?? '';
		if (!build) return;
		if (this.#loaded === null) {
			this.#loaded = build;
			return;
		}
		if (build !== this.#loaded) this.stale = true;
	}

	/** Take the new client. The only thing that can, which is the whole point of the banner. */
	reload(): void {
		location.reload();
	}

	/** For a test, and for the sign-out path: a fresh window starts from nothing. */
	forget(): void {
		this.#loaded = null;
		this.stale = false;
	}
}

export const build = new BuildWatch();
