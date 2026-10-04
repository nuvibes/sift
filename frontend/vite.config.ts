import adapter from '@sveltejs/adapter-static';
import { sveltekit } from '@sveltejs/kit/vite';
import { defineConfig } from 'vitest/config';
import type { ProxyOptions } from 'vite';

// The client is built to static files and served by the Python application on its own port. There
// is no Node process in a running install: the server answers /api itself, and everything else is
// this bundle. That is why the adapter is `static` and not one of the hosting adapters: there is
// no host, only the Python application that serves it.
export default defineConfig(({ mode }) => ({
	plugins: [
		sveltekit({
			compilerOptions: {
				// Runes everywhere in our own code; libraries keep whatever they were written in.
				runes: ({ filename }) =>
					filename.split(/[/\\]/).includes('node_modules') ? undefined : true
			},

			adapter: adapter({
				// Into the Python package, not a build/ directory beside this one. The server finds
				// the client next to its own module rather than by walking up to a repo layout that
				// does not exist once installed, so there is no path to configure.
				pages: '../src/sift/web',
				assets: '../src/sift/web',

				// Every page is served by handing back this one file and letting the client route.
				// A deep link has to survive a refresh (a filtered view is a link people send),
				// and the addressable ones carry an id no build could enumerate in advance.
				fallback: 'index.html',
				precompress: false,
				strict: false
			}),

			// The server sends Content-Security-Policy with `default-src 'self'` and no
			// 'unsafe-inline'. SvelteKit's bootstrap is an inline script, so it has to be named in
			// the policy or the browser refuses to start the app.
			//
			// Hashes, not a nonce: the pages are prebuilt, so there is no per-request value to put
			// in them, and a nonce fixed at build time is a constant pretending to be a secret.
			// SvelteKit writes the hashes into a <meta> tag in the built page; the browser enforces
			// that and the server's header together, so the header has to allow the same hashes.
			csp: {
				mode: 'hash',
				directives: {
					'script-src': ['self']
				}
			}
		})
	],

	// Dev only. `npm run dev` serves the client on 5173 and forwards /api to the Python server, so
	// the browser sees one origin and the cookie and CSRF flow work exactly as they do in
	// production. Nothing here is part of the build.
	server: {
		// The backend the dev client proxies to: the installed app on 5171 by default, or the one
		// SIFT_DEV_API names (a second backend, so the source tree can be looked at live against it).
		// A reset on a proxied socket (the backend closing a long-lived connection, the live socket
		// among them) is logged, never thrown: unhandled it takes the dev server down.
		proxy: { '/api': devProxy(), '/health': devProxy() }
	},

	// Under test only. Without it the components resolve to the framework's server build, where
	// mounting one is not a thing that exists: the tests run in jsdom, which is a browser as far as
	// they are concerned, and they have to get the browser half of the library to match.
	//
	// Kept to test mode rather than set for everything: the build has its own idea of how to resolve
	// this and is not having an argument with it.
	resolve: mode === 'test' ? { conditions: ['browser'] } : {},

	test: {
		environment: 'jsdom',
		include: ['src/**/*.test.ts'],
		// The unit environment is a document with no renderer, so it carries none of the browser's
		// animation machinery, which the framework's own transitions are built on. See the file.
		setupFiles: ['./src/test-setup.ts'],

		/*
		 * Coverage, and what it is actually for here.
		 *
		 * The number itself is the less useful half. What this exists to catch is a file with NO
		 * test at all, which looks exactly like a file whose tests all pass. A file nothing reaches
		 * reads as 0% and is counted by `scripts/check_client_coverage.js`.
		 *
		 * `all: true` is the whole mechanism. Without it only the files a test happens to import
		 * are measured, so the untested ones are absent from the report rather than at zero: the
		 * same blindness in a different shape.
		 *
		 * Routes are out of scope on purpose. A page is assembled from the components below it and
		 * is walked end to end in a real browser; measuring it here would count a route as covered
		 * because a single mount touched a third of its lines.
		 */
		coverage: {
			provider: 'v8',
			all: true,
			include: ['src/lib/**/*.{ts,svelte}'],
			exclude: [
				'src/lib/**/*.test.ts',
				// Generated from the server's own description. Regenerated, never written.
				'src/lib/api/schema.d.ts',
				// Type-only, so there is nothing to execute and nothing to cover.
				'src/lib/**/*.d.ts'
			],
			reporter: ['text-summary', 'json-summary'],
			reportsDirectory: './coverage'
		}
	}
}));

/** The backend the dev client proxies to: the installed app on 5171 by default, or the one
 *  SIFT_DEV_API names (a second backend, so the source tree can be looked at live against it). */
function devProxy(): ProxyOptions {
	return {
		target: process.env.SIFT_DEV_API ?? 'http://127.0.0.1:5171',
		ws: true,
		configure: (proxy) => {
			proxy.on('error', (error) => {
				console.warn(`dev proxy: ${error.message}`);
			});
		}
	};
}
