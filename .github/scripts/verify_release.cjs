// SPDX-License-Identifier: AGPL-3.0-or-later
//
// Checks one published release the way an installed copy will: with the desktop shell's own
// verifier and the public key compiled into it. A release this refuses is one no installed copy
// would take, so it fails here first.
//
//   node .github/scripts/verify_release.cjs <folder of downloaded assets> <tag>
//
// Needs the shell compiled (`npm run build` in desktop/). Refuses when the tag, the installer's
// name, the signed manifest and pyproject.toml disagree about the version, when either signature
// does not verify, or when the installer's SHA-256 is not the one both signed files name.

'use strict';

const crypto = require('node:crypto');
const fs = require('node:fs');
const Module = require('node:module');
const path = require('node:path');

const ROOT = path.resolve(__dirname, '..', '..');

// The verifier sits in a module that imports Electron for its other half. Nothing used here
// touches it, and outside Electron the package would try to fetch its binary, so it is stood in
// for by an empty object.
const load = Module._load;
Module._load = function (request, ...rest) {
	return request === 'electron' ? {} : load.call(this, request, ...rest);
};
const shell = require(path.join(ROOT, 'desktop', 'dist', 'update.js'));

function fail(message) {
	console.error(`release-verify: ${message}`);
	process.exit(1);
}

function sha256(file) {
	return crypto.createHash('sha256').update(fs.readFileSync(file)).digest('hex');
}

const [folder, tag] = process.argv.slice(2);
if (!folder || !tag) fail('usage: verify_release.cjs <folder> <tag>');

const version = tag.replace(/^v/, '');
const pyproject = fs.readFileSync(path.join(ROOT, 'pyproject.toml'), 'utf8');
let section = '';
let declared = null;
for (const line of pyproject.split(/\r?\n/)) {
	const header = /^\[([^\]]+)\]\s*$/.exec(line);
	if (header) section = header[1];
	const found = /^version\s*=\s*"([^"]+)"/.exec(line);
	if (section === 'project' && found) declared = found[1];
}
if (declared === null) fail('pyproject.toml declares no [project] version');
if (declared !== version) fail(`the tag says ${version} and pyproject.toml ${declared}`);

const installers = fs.readdirSync(folder).filter((name) => /-x64-setup\.exe$/.test(name));
const name = `Sift-${version}-x64-setup.exe`;
if (installers.length !== 1 || installers[0] !== name) {
	fail(`expected exactly ${name}, found ${JSON.stringify(installers)}`);
}
const installer = path.join(folder, name);
const digest = sha256(installer);

function signed(file) {
	if (!fs.existsSync(file) || !fs.existsSync(`${file}.minisig`)) {
		fail(`${path.basename(file)} or its signature is missing`);
	}
	const body = fs.readFileSync(file);
	if (!shell.signatureIsGood(body, fs.readFileSync(`${file}.minisig`, 'utf8'))) {
		fail(`the signature over ${path.basename(file)} does not verify against the shipped key`);
	}
	return body.toString('utf8');
}

const manifest = shell.manifestFrom(signed(`${installer}.manifest.json`));
if (manifest === null) fail('the signed manifest does not read as one');
if (manifest.version !== version) fail(`the manifest says ${manifest.version}, the tag ${version}`);
if (manifest.installer !== name) fail(`the manifest names ${manifest.installer}, not ${name}`);
if (manifest.sha256 !== digest) fail(`the installer hashes to ${digest}, the manifest says ${manifest.sha256}`);

const [listed, listedName] = signed(`${installer}.sha256`).trim().split(/\s+/);
if (listed !== digest || listedName !== name) fail('the signed .sha256 file names another file');

console.log(`release-verify: ${name} ${digest}, both signatures good, version ${version}`);
