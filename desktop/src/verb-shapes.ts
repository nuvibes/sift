/* The shapes the verbs hand the page and take from the shell. */

import type { LibraryList, Settled, SetupStep } from '../../shared/bridge';

import type { Browser } from './browsers';

/** What the folder screen is offered. `existing` says a database is already there. */
export interface Suggested {
	path: string;
	existing: boolean;
}

export interface Setup {
	mode(chosen: 'standalone' | 'client'): Promise<Settled>;
	suggested(): Promise<Suggested>;
	/** Settle the library folder; `told` hears each step as it starts, for the screen to say. */
	library(pick: boolean, told: (step: SetupStep) => void): Promise<Settled>;
	/** Unsay the mode, so the question before it is asked again. */
	back(): Promise<Settled>;
}

/** The libraries this copy has opened: handed in, since a switch is the backend's supervisor's to
 * do. */
export interface LibraryBook {
	list(): LibraryList;
	open(dataDir: string): Promise<Settled>;
	add(): Promise<Settled>;
	/** Take one off the list. The library itself is untouched; this forgets the shortcut. */
	forget(dataDir: string): LibraryList;
}

export interface SavedServer {
	label: string;
	origin: string;
}

export interface ConnectState {
	last: string | null;
	/** Why the last address did not answer, when that is why the screen is up. Null otherwise. */
	problem: string | null;
	servers: SavedServer[];
}

/** What the connect screen needs from the shell, so a dead server is a screen, not a crash. */
export interface ServerBook {
	remember(origin: string): Promise<string | null>;
	last(): string | null;
	problem(): string | null;
	saved(): SavedServer[];
	forget(origin: string): SavedServer[];
}

export type DragOutcome = 'dragged' | 'unavailable';

/** The end of the shell's log, in the shape of the server's own log route, so one screen draws both. */
export interface ShellLog {
	lines: string[];
	path: string;
	size: number;
	present: boolean;
}

export interface DownloadFolder {
	/** The folder itself, always a real path: the machine's own where none was chosen. */
	path: string;
	/** Whether that path was CHOSEN: only the unchosen one follows the machine if that folder
	   moves. */
	chosen: boolean;
}

export interface BrowserChoice {
	/** The executable chosen, or null for whatever Windows would have used. */
	chosen: string | null;
	browsers: Browser[];
}
