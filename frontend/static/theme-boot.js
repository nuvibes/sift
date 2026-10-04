/* The theme, on the page before anything is drawn.
 *
 * WHY THIS FILE EXISTS AT ALL. The chosen theme lives on the server, because it belongs to an
 * account rather than to a browser, so it arrives one request after the page does. Left to that,
 * every load paints the default near-black with the blue accent, and then everything changes colour
 * a moment later. That flash is the most visible bug a theming feature can have, and it happens on
 * every single page load rather than occasionally.
 *
 * So the choice is mirrored into this browser's own storage whenever it is set or read, and this
 * runs before the first paint and puts it back. The server stays the source of truth; this is only
 * the fast path, and a mirror that disagrees is corrected within the same second by the store.
 *
 * WHY IT IS A SEPARATE FILE rather than a few lines inside the page. The policy Sift serves refuses
 * inline scripts unless each one is named by the hash of its own contents, and the build names
 * exactly one: the framework's bootstrap. An inline script here would be refused by the browser,
 * silently, leaving the flash it was written to prevent. Fetched from Sift's own origin it needs no
 * exception at all, and because the browser finds it while parsing the same <head> that names the
 * stylesheet, the two are fetched together and it costs no extra wait.
 *
 * It is deliberately plain, old JavaScript with no imports: it has to run before the module graph
 * exists, and anything it throws would stop the page. Hence the try, and hence doing nothing at all
 * if there is nothing to do: an unstyled attribute is simply the default theme, which is correct.
 */
(function () {
	try {
		var raw = localStorage.getItem('sift.theme');
		if (!raw) return;
		var choice = JSON.parse(raw);
		var root = document.documentElement;
		// Shape only, not a list of the themes that exist. Keeping the real list in one place means
		// there is no second copy here to fall out of date, and a value no stylesheet rule matches
		// simply leaves the default in force, which is the right answer for a stale or edited mirror.
		var sane = /^[a-z][a-z0-9-]{0,23}$/;
		// What a font FAMILY's name meant in each role, for a copy written before each face had a
		// name of its own: `grotesk` was Space Grotesk as the main font and Inter as the secondary.
		// The same table the store reads, written in here from `src/lib/theme/carried-faces.json`
		// by `scripts/write_theme_boot.js`, because this file cannot import it.
		// BEGIN carried faces: written by scripts/write_theme_boot.js. Edit the json.
		var carried = {
			display: { grotesk: 'space-grotesk', geist: 'geist-mono' },
			body: { archivo: 'instrument-sans', grotesk: 'inter', manrope: 'public-sans' }
		};
		// END carried faces
		// The attribute each part is stamped on, and the role its older family names are read in.
		// An older copy may hold one `face` that named a PAIRING rather than `faceDisplay` and
		// `faceBody`; that name is read as both halves, which is what it meant, so the one load after
		// an update does not flash the default pairing.
		var parts = [
			['base', 'base'],
			['accent', 'accent'],
			['faceDisplay', 'face-display', 'display'],
			['faceBody', 'face-body', 'body']
		];
		parts.forEach(function (part) {
			var value = choice ? choice[part[0]] : undefined;
			// The older name, and only where it meant something: a missing base is not a face.
			if (typeof value !== 'string' && part[2]) {
				value = choice ? choice.face : undefined;
			}
			var older = part[2] && carried[part[2]];
			if (
				older &&
				typeof value === 'string' &&
				Object.prototype.hasOwnProperty.call(older, value)
			) {
				value = older[value];
			}
			if (typeof value === 'string' && sane.test(value)) {
				root.setAttribute('data-' + part[1], value);
			}
		});

		// The seventh accent, which is a colour somebody chose rather than a block in the
		// stylesheet. THE MIRROR CARRIES THE FIVE FINISHED VALUES, not the colour they came from:
		// this file runs before the module graph exists and cannot import the derivation, and a
		// second copy of that arithmetic here is the one thing that must not exist. The store
		// re-derives and overwrites within the same second, so a stale copy costs a frame.
		var roles = {
			accent: '--p-accent',
			hover: '--p-accent-hover',
			text: '--p-accent-text',
			bg: '--p-accent-bg',
			ring: '--p-accent-ring-line'
		};
		var colour = /^#[0-9a-f]{6}$/i;
		var custom = choice && choice.custom;
		if (custom && choice.accent === 'custom') {
			Object.keys(roles).forEach(function (role) {
				var value = custom[role];
				if (typeof value === 'string' && colour.test(value)) {
					root.style.setProperty(roles[role], value);
				}
			});
		}
	} catch (ignored) {
		// Storage can be unavailable or full, and the mirror can be anything at all. None of that is
		// worth a broken page: without it the app opens in the default theme and corrects itself when
		// the server answers.
	}
})();
