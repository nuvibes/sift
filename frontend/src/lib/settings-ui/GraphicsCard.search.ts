// SPDX-License-Identifier: AGPL-3.0-or-later
/* The GPU block on Performance: its words, and what somebody can type to find it. Not a
 * preference: it downloads support and tests it.
 *
 * ONE COPY MODULE PER PANE. `GraphicsCard.svelte` draws every word it adds from `COPY`, and the
 * search entry is built from the same object. "GPU" and "CPU" are the only words for the two,
 * never "graphics card", "card" or "processor". */
import type { Searchable } from './search';

export const COPY = {
	name: 'GPU',
	help: 'Face recognition, Smart Search and watermark reading can run on an NVIDIA GPU, which is faster than the CPU. Without GPU support they run on the CPU, more slowly.',
	unsupported:
		"Sift can't see a GPU it can use on this device, so Smart Search and face recognition run on the CPU. Sift needs an NVIDIA GPU with a working driver.",
	alreadyCapable:
		"This device already has a runtime that can use the GPU, so there's nothing to download and Sift doesn't replace it. Choose the GPU under Faces, Smart Search and Watermarks.",
	ready:
		'Ready. Choose the GPU under Faces, Smart Search and Watermarks; each decides separately what to run on.',
	missing:
		"Your GPU driver is already installed and isn't what is missing. Sift ships the CPU-only build of its model runtime, so Smart Search and face recognition run on the CPU until the GPU build is added. Sift downloads the GPU build once and keeps it beside its database. Installing an update doesn't delete it, and nothing already on this device is replaced.",
	gpu: 'GPU',
	downloading: 'Downloading GPU support',
	waiting: 'Waiting',
	downloadingNow: 'Downloading',
	leave:
		'You can leave this screen; the download continues. Follow or cancel it in Activity. A canceled download keeps what arrived, so starting again downloads only the rest.',
	restart: {
		label: 'Restart needed',
		help: 'The GPU runtime was installed after Sift had already loaded the CPU version, and only one can be in use at a time. This restarts Sift on the device that runs your library, wherever you are reading this. Nothing is lost: unfinished tasks continue, and you are asked for your password again if you had unlocked anything.',
		restarting: 'Restarting',
		action: 'Restart Sift',
		failed: "Sift didn't restart",
		cannot: "Couldn't restart Sift.",
		slow: "Sift hasn't finished restarting. It may still be starting; this screen updates the next time you open it."
	},
	test: {
		label: 'Test the GPU',
		notYet:
			'Not yet: Sift is still running the CPU version, so the test would fail whatever the GPU can do. Restart first, then test.',
		help: "Loads a real model onto the GPU and runs it in a separate process. A GPU that's present doesn't always work; this checks.",
		testing: 'Testing',
		action: 'Run the test',
		passed: 'Passed',
		refused: 'Refused',
		said: 'What the GPU reported',
		noModel: "It didn't run a model.",
		failed: 'The test failed.',
		noAnswer:
			"The test didn't answer in two and a half minutes. If an import is running, try again when it's done."
	},
	remove: {
		heading: 'GPU support',
		/* What the group IS, said before the one act in it: the heading alone named a thing nobody
		   had been told about. */
		lede: 'GPU support is the download that lets face recognition, Smart Search and watermark reading run on your NVIDIA GPU, which is faster. Without it they run on the CPU: everything still works, only more slowly. Nothing else in Sift uses it.',
		label: 'Delete GPU support',
		help: 'Face recognition, Smart Search and watermark reading go back to the CPU, and its disk space is freed. You can download it again.',
		action: 'Delete'
	},
	install: {
		label: 'Download the GPU runtime',
		help: 'The runtime Sift loads models with, built for the GPU, with the NVIDIA libraries it needs. Sift downloads it once from its publisher, checks it against a published fingerprint before unpacking it, and keeps it in its own folder. It works with any NVIDIA GPU a current driver supports.',
		action: 'Download',
		note: (size: string, room: string) => `${size} to download; needs ${room}`,
		room: (gb: string) => `${gb} GB free while it installs`,
		defaultSize: 'about 1.3 GB',
		defaultRoom: 'about 3.1 GB free while it installs',
		cannotStart: "Couldn't start the download. Check that this device can reach the internet."
	},
	lastDownload: 'Last download'
} as const;

export const SEARCHABLE: Searchable[] = [
	{
		name: COPY.name,
		key: 'performance.graphics_card',
		section: 'performance',
		help: COPY.help,
		keywords: 'gpu graphics card nvidia cuda hardware acceleration faster recognition test card'
	},
	/* Every other name the pane draws, so the search finds each (`check_settings_search_covers_panes.js`). */
	{
		name: COPY.test.label,
		section: 'performance',
		keywords: 'test gpu graphics card cuda check works'
	},
	{
		name: COPY.install.label,
		section: 'performance',
		keywords: 'download gpu runtime cuda graphics card install'
	},
	{
		name: COPY.remove.heading,
		key: 'performance.gpu-remove',
		section: 'performance',
		keywords: 'gpu support graphics card runtime'
	},
	{
		name: COPY.remove.label,
		section: 'performance',
		keywords: 'delete remove uninstall gpu runtime space'
	}
];
