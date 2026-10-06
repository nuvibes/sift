/* The window on screen at once: Sift's own frame in the saved theme, drawn by the shell's copy of the
 * client in a view laid over the real page while that one loads, and taken away once the real page
 * says it has painted. Both draw the same frame in the same theme, so nothing flashes between them.
 *
 * What is kept between starts is the theme and the canvas colour, never a picture of the screen: a
 * picture would show the library before the password, from a file anyone at the computer can open.
 */

import { app, WebContentsView, type BrowserWindow } from 'electron';
import * as fs from 'node:fs';
import * as path from 'node:path';

import { log } from './log';

/** The shell's own route the frame is drawn from. */
export const OPENING_ROUTE = '/opening';

/** How long the frame stays over a real page that loaded and never said it painted: a page from an
 *  older Sift on another computer, which has no such verb. */
export const UNSAID_MS = 1_500;

/** The longest the window waits for the frame's own first paint before it is shown anyway. */
export const FRAME_WAIT_MS = 400;

/** The page's canvas when nothing has been kept: the default theme's, as the window's own. */
export const DEFAULT_CANVAS = '#0a0b10';

const uptimeMs = (): number => Math.round(process.uptime() * 1000);

/** How the page looked: its theme as the page mirrors it, and its canvas colour. */
export interface Look {
	theme: string | null;
	canvas: string;
}

const HEX = /^#[0-9a-fA-F]{6}$/;
/* The page's mirror is a few hundred bytes; anything larger is not one. */
const MOST_THEME = 4096;

/** A look as the page handed it, checked: the theme a JSON object's text, the canvas a hex colour. */
export function lookFrom(asked: unknown): Look | null {
	const given = asked as { theme?: unknown; canvas?: unknown } | null;
	if (given === null || typeof given !== 'object') return null;
	if (typeof given.canvas !== 'string' || !HEX.test(given.canvas)) return null;
	let theme: string | null = null;
	if (typeof given.theme === 'string' && given.theme.length <= MOST_THEME) {
		try {
			const parsed: unknown = JSON.parse(given.theme);
			if (parsed !== null && typeof parsed === 'object' && !Array.isArray(parsed)) {
				theme = given.theme;
			}
		} catch {
			/* not a mirror: the frame draws the default theme */
		}
	}
	return { theme, canvas: given.canvas };
}

function lookFile(): string {
	return path.join(app.getPath('userData'), 'opening.json');
}

/** The look kept by the last start, or the default one. A copy each time: the caller keeps it. */
export function keptLook(): Look {
	try {
		const kept = lookFrom(JSON.parse(fs.readFileSync(lookFile(), 'utf8')));
		if (kept !== null) return kept;
	} catch {
		/* nothing kept yet, or not readable: the default */
	}
	return { theme: null, canvas: DEFAULT_CANVAS };
}

/** Keep a look for the next start. True when it changed. */
export function keepLook(look: Look, kept: Look = keptLook()): boolean {
	if (look.theme === kept.theme && look.canvas === kept.canvas) return false;
	try {
		const target = lookFile();
		fs.mkdirSync(path.dirname(target), { recursive: true });
		fs.writeFileSync(`${target}.tmp`, JSON.stringify(look), 'utf8');
		fs.renameSync(`${target}.tmp`, target);
		return true;
	} catch (err) {
		log.warning('window.look_unkept', { reason: err instanceof Error ? err.message : String(err) });
		return false;
	}
}

/** The frame's address: the theme rides in the fragment, which is never sent anywhere. */
export function openingAddress(origin: string, look: Look): string {
	return `${origin}${OPENING_ROUTE}#${encodeURIComponent(look.theme ?? '')}`;
}

/** The frame laid over a window, until `remove`. */
export class Opening {
	private view: WebContentsView | null;
	private unsaid: ReturnType<typeof setTimeout> | null = null;
	private readonly fit = (): void => {
		const bounds = this.window.getContentBounds();
		this.view?.setBounds({ x: 0, y: 0, width: bounds.width, height: bounds.height });
	};

	constructor(
		private readonly window: BrowserWindow,
		address: string,
		canvas: string
	) {
		/* No preload: the frame asks the shell for nothing. */
		this.view = new WebContentsView({
			webPreferences: {
				nodeIntegration: false,
				contextIsolation: true,
				sandbox: true,
				webSecurity: true
			}
		});
		this.view.setBackgroundColor(canvas);
		window.contentView.addChildView(this.view);
		this.fit();
		window.on('resize', this.fit);
		void this.view.webContents.loadURL(address).catch(() => this.remove('failed'));
	}

	/** Settles once the frame has drawn, or after `FRAME_WAIT_MS`, so the window is never held. */
	drawn(): Promise<void> {
		const view = this.view;
		if (view === null) return Promise.resolve();
		return new Promise((done) => {
			const timer = setTimeout(done, FRAME_WAIT_MS);
			view.webContents.once('did-finish-load', () => {
				clearTimeout(timer);
				done();
			});
		});
	}

	/** The real page has loaded: if it never says it painted, the frame goes anyway. */
	pageLoaded(): void {
		if (this.view === null || this.unsaid !== null) return;
		this.unsaid = setTimeout(() => this.remove('unsaid'), UNSAID_MS);
	}

	get showing(): boolean {
		return this.view !== null;
	}

	/** Take the frame away; the real page is underneath. Idempotent. */
	remove(why: 'painted' | 'unsaid' | 'failed' | 'shell page'): void {
		const view = this.view;
		if (view === null) return;
		this.view = null;
		if (this.unsaid !== null) clearTimeout(this.unsaid);
		this.window.removeListener('resize', this.fit);
		this.window.contentView.removeChildView(view);
		view.webContents.close();
		log.info('window.frame_removed', { why, uptime_ms: uptimeMs() });
	}
}

/** How far a page has drawn: its first screen, or a screen ready to use. */
export type WindowStage = 'painted' | 'usable';

/** The page telling the shell how far it has drawn. Written out in the preload too. */
export const WINDOW_STAGE = 'sift:windowStage';

/** The first segment of a page's path, which names a screen and never a file or a person. */
export function screenOf(url: string): string {
	try {
		return `/${new URL(url).pathname.split('/')[1] ?? ''}`;
	} catch {
		return '/';
	}
}

/** The frame through one start: laid over the window, shown, and taken away, and the stages the
 *  page reached, each logged once. */
export class StartFrame {
	/** The look the window and the frame are drawn in, kept by the last start. */
	readonly look: Look = keptLook();
	private opening: Opening | null = null;
	private shownInFrame = false;
	private readonly said = new Set<string>();

	constructor(private readonly startedAt: number) {}

	/** Lay the frame over `window` and, once it has drawn, `show` it unless something showed the
	 *  window first. The backend starts meanwhile: neither waits on the other. */
	open(window: BrowserWindow, origin: string, show: () => boolean): void {
		this.opening = new Opening(window, openingAddress(origin, this.look), this.look.canvas);
		void this.opening.drawn().then(() => {
			this.shownInFrame = show();
		});
	}

	/** True once, for the first page's show: the window has been on screen in the frame since. */
	takeShown(): boolean {
		const shown = this.shownInFrame;
		this.shownInFrame = false;
		return shown;
	}

	get framed(): boolean {
		return this.opening !== null;
	}

	/** A page has loaded under the frame. The shell's own screens are drawn at once; another
	 *  Sift's page says when it has painted. */
	loaded(address: string, shellOrigin: string): void {
		if (address.startsWith(shellOrigin)) this.opening?.remove('shell page');
		else this.opening?.pageLoaded();
	}

	failed(): void {
		this.opening?.remove('failed');
	}

	/** The page says how far it has drawn on `screen`: the frame goes at its first paint, and its
	 *  look is kept for the next start's frame. */
	drawn = (stage: WindowStage, asked: unknown, screen: string): void => {
		const seen = lookFrom(asked);
		if (seen !== null && keepLook(seen, this.look)) Object.assign(this.look, seen);
		this.opening?.remove('painted');
		const key = stage === 'painted' ? stage : `${stage} ${screen}`;
		if (this.said.has(key)) return;
		this.said.add(key);
		log.info(stage === 'painted' ? 'window.painted' : 'window.usable', {
			screen,
			uptime_ms: uptimeMs(),
			after_ms: Date.now() - this.startedAt
		});
	};
}
