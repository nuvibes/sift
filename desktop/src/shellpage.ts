/* Serving one screen off the disk, for the moment when there is no server to serve it.
 *
 * Client mode's first run is a chicken and an egg: the screen that asks which computer the
 * library is on cannot come from that computer, because nobody has said which one it is. The
 * screen is a real route in the Sift client (`/connect`), so the shell serves the built client
 * itself, through a scheme of its own, and loads that route.
 *
 * This is not a media protocol: it serves static files from inside the application's own bundle,
 * answers nothing else, and is used only until an address has been saved.
 *
 * `file://` does not work: the built client is served under a Content-Security-Policy of
 * `default-src 'self'`, and a file URL's origin is `null`, so `'self'` matches nothing and the
 * browser refuses to run the application's own scripts. A registered scheme is a real origin,
 * which makes the policy mean what it says.
 */

import { net, protocol } from 'electron';
import * as path from 'node:path';
import { pathToFileURL } from 'node:url';

import { clientFiles } from './paths';

/** The scheme, and the one host under it. A URL of any other shape is refused. */
export const SHELL_SCHEME = 'sift-shell';
export const SHELL_ORIGIN = `${SHELL_SCHEME}://app`;

/**
 * Declare the scheme before the app is ready. It cannot be done later.
 *
 * `standard` makes it a real origin rather than an opaque one, which is what lets the client's own
 * `default-src 'self'` policy match its own files. `secure` puts it in the same class as https, so
 * the parts of the platform that refuse to work in an insecure context work here.
 */
export function declareShellScheme(): void {
	protocol.registerSchemesAsPrivileged([
		{
			scheme: SHELL_SCHEME,
			privileges: { standard: true, secure: true, supportFetchAPI: true }
		}
	]);
}

/** Wire the scheme up to the built client. Called once, after the app is ready. */
export function serveShellPages(): void {
	const root = clientFiles();

	protocol.handle(SHELL_SCHEME, async (request) => {
		const url = new URL(request.url);
		/* One host, and nothing else. Without this, `sift-shell://anything/../../secret` is a URL
		 * somebody could put in front of this handler. */
		if (url.host !== 'app') return new Response('Not found', { status: 404 });

		const target = resolveWithin(root, url.pathname);
		if (target === null) return new Response('Not found', { status: 404 });
		return net.fetch(pathToFileURL(target).toString());
	});
}

/**
 * Where a request lands on the disk, or null if it lands outside the client's own folder.
 *
 * The escape check is on the resolved path rather than on the text of the URL, because that is the
 * only form that is comparable: `%2e%2e`, a backslash and a symbolic link all look like different
 * things as text and like the same thing once resolved.
 *
 * A path that is not a file falls back to `index.html`, because the client is a single page that
 * routes itself.
 */
export function resolveWithin(root: string, pathname: string): string | null {
	const decoded = safeDecode(pathname);
	if (decoded === null) return null;
	const resolvedRoot = path.resolve(root);
	const target = path.resolve(resolvedRoot, `.${decoded}`);
	const inside =
		target === resolvedRoot || target.startsWith(resolvedRoot + path.sep);
	if (!inside) return null;
	return path.extname(target) === '' ? path.join(resolvedRoot, 'index.html') : target;
}

function safeDecode(pathname: string): string | null {
	try {
		const decoded = decodeURIComponent(pathname);
		/* A NUL truncates a path in every C API underneath, so a name carrying one means one thing
		 * to this check and another to the filesystem. */
		return decoded.includes('\0') ? null : decoded;
	} catch {
		return null;
	}
}
