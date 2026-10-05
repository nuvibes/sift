# Changelog

What each release of Sift changes, newest first. A version's section is its release notes: the
release page and the Updates screen both show it. Changes land under Unreleased and move into a
version's section, dated, when that version is published.

## Unreleased

### What changed for you

**Players**

- **Every player bar keeps all its controls.** The repeat control, Previous, Play, Next and Shuffle
  stand in that order on every bar. A grayed-out control says why when you point at it.
- **The Mini player and the Audio player move on at the end of a clip.** They replayed the same clip
  before. Like the popout, they now get the next file ready while one plays.
- **A picture you open stays put** under Play through until you press Play, Next or Previous. It
  moved on after 2 seconds.
- **The Audio player wears Theater's bar,** with its picture centered and no file name. The popout
  shrinks back as it leaves, and in Trim the start can be grabbed across 8 px at 0:00, not 2 px.
- **The Remote has the same five controls,** with Back 5 seconds and Forward 5 seconds above them.

**Theater**

- **The wall uses the room the bars leave.** On a full-screen Center stage 1x3 the previews grew
  from 148 px to 212 px tall (43%). Cells ease with the bars, and a portrait cell stops 8 px above
  the bottom bar, where it ran about 100 px under it.
- **A cell takes its video's shape** even when the video's size isn't stored yet. Three portrait
  videos in a full-screen 1x3 went from 201 px to 607 px wide each.
- **1x2 (P) and 1x2 (L).** Layouts offers two cells side by side or two stacked, and pointing at
  either says Portrait or Landscape.
- **Layout Presets are now Saved Layouts,** saved through a dialog that shows the whole wall and
  what isn't kept. Renaming a Center stage Saved Layout no longer fails.
- **Pick up where you left off survives a reload,** bringing back each cell's file, paused. A
  preview comes up on a double-click, and its menu gains Close the strip.
- **Full screen no longer jumps.** Entering or leaving it, each cell moves one way to its new
  place. The largest move the wrong way was 53 px and is now under 2 px.
- **The keyboard reaches Theater's bar.** Tab brings the bars up and goes into them, and B brings
  them back as well as sending them away.

**Search, filters and the top bar**

- **The top bar's center holds still.** Filter, Sort by and the search box stay centered as the
  window gets smaller, where they drifted up to 104 px. When room runs out, the tile size moves to
  a Tile size press on the row under the bar.
- **Has and No in the Filter panel.** Six columns start with a pair such as Has tags and No tags,
  and a pressed chip now stays where it is. On 101,619 files the People column's count went from
  about 0.12 s to 0.21 s.
- **More ways to sort, and quicker.** Browse adds Favorites first and Highest rated. People, Sites,
  Tags and the other walls, and their pages' tabs, add Largest, Smallest, Longest and Shortest in
  total. On 101,619 files, Highest O count's first page went from 0.75 s to 0.13 s (83% less).
- **A search box on every tab** of a person's, Site's, tag's, collection's, Photo Set's or song's
  page. A collection now pages past its first 200 files, and Move earlier and Move later work
  anywhere in it.
- **The pager keeps your place** when files arrive, and Previous page returns to the page you left.
  The last page is never one lone row.
- **Saved filters go with what they name.** Deleting the last thing a saved filter names deletes the
  filter too, and History says so.
- **Clearer words.** The search box's suggestions say where each is set instead of showing a long
  ID. An empty wall says what emptied it, such as "No files match these filters."

**People and faces**

- **Export carries the people waiting for a matching face,** with no models downloaded. On one
  library the file went from 150 people to 657. Anyone marked Kept local or Don't swap stays out.
- **Two libraries' files no longer replace each other,** because each carries its own library's ID.
  Sift 0.2.0 can't read a file exported by this version.
- **A folder import's report lists the files** behind each reason. Each stash-box mark wears its own
  glyph, all in one accent tone.

**Importing, Activity and the benchmark**

- **Every folder you add is counted right away,** without waiting for another folder's read. Nine
  folders on a network share were all counted 24 seconds after they were added. In 0.2.0 that took
  71 to 136 minutes. Until all are counted, Activity says "Not known until every folder is counted."
- **A folder added while another is being read is read once.** It was scanned three times, so each
  new file was read twice.
- **Time left is closer to the truth.** It takes in the files the scan hasn't read yet, and prices a
  new batch apart from older work. The Scan row counts a folder's whole total from the start. A
  two-minute batch read "About 1 to 3 hours" and now reads a few minutes. A fresh library gets a
  floor from the benchmark, such as "At least 6 hours".
- **A folder that stops answering is one finding.** One moved away after 9 of its 100 files were
  read had 91 refused one by one; now 1 is, and nothing in it is marked missing. The Scan row says
  the scan failed and why.
- **The benchmark measures what Sift uses:** previews on your GPU, each installed model, and every
  drive and share. It runs its picks together for a minute, and can give each share a number
  measured for it.
- **The benchmark pauses your waiting tasks and says so.** A whole run now takes ten to twenty
  minutes on a busy computer, where it took one to three. Cancel stops it at once and keeps your
  last result.
- **A tool that can't start says why.** Its task fails with "A tool Sift runs couldn't start,
  because a file it needs was in use or missing." where it said "no detail".
- **A new setting, Use less system resources while other programs are busy,** is off until you turn
  it on, and works on Windows only.
- **Activity's totals leave out files whose folder you removed.** On 101,619 files, that makes the
  library's count take 9.3 ms where it took 1.8 ms.

**Settings and docs**

- **Documentation in Settings** holds every page of the docs with its pictures, offline and for the
  version you run. It makes Sift about 4.6 MB bigger. Each heading has a Copy link press, and on the
  docs site a heading's link now copies itself.
- **Backup and restore saves as you go.** Save a backup spins from the moment you press it, where
  nothing showed for about 38 seconds. A second window shows a save that is going.
- **Picking a Download folder anywhere saves it** as the default, and the choosers list only folders
  Sift can write to.
- **Test the GPU says Passed,** where it said Working, which read as still running.
- **Icons and order.** Importing wears Upload, Export wears Publish, and Sites and Tunnels sits
  above Downloads. In the desktop app, an admin sees an update waiting as a button in the title bar.
- **History names a tunnel,** never its ID. Renaming a song no longer marks its files as edited: all
  50 files of a song were marked.
- **The first-visit hints are gone.**

**Guests and privacy**

- **A guest's search no longer hints at files they can't see.** A word matching such files made the
  search slower. Words, names, and filters on people, tags and other things are now matched only
  against what the guest may see.
- **What that costs.** A guest who may see half of a 100,000-file library waits about a quarter of a
  second the first time they search a word. Their Closest match lists newest first.
- **Smart Search and Similar to this rank only what you may see.** A guest, or an admin while Hidden
  was locked, could get an empty page. A guest's first search by meaning takes about half a second.
- **History and pages no longer name things a guest isn't shown,** a Site or a folder included. A
  guest is no longer offered presses that would be refused, such as Remove from this collection.
  Only you can read your saved filter's name in History.
- **Has and No follow Hidden.** While Hidden is locked and shows locked tiles, a file whose only tag
  is hidden no longer counts under Has tags.

### For people who build Sift

- **A function's branches are held as its length is.** The code-shape ratchet records each
  function's cyclomatic complexity over 10 in Python or 20 in TypeScript. A recorded number may only
  fall.
- **A release refuses a commit whose hosted suite isn't green.** `scripts/release.py --publish`
  names the commit and the run it found. A new release is made as a draft with its files, then
  published.
- **Every library inside a shipped program is named.** `bin/THIRD-PARTY-NOTICES.txt` lists all 262
  with their version, source and license. A signed build refuses while any lacks one.
- **Building the HEIF wheel yourself.** Anywhere but the computer that builds Sift's releases, add
  `--wheel-by-contents` to `scripts/fetch_vendor.py`. A seeded wheel is used only when it matches
  the pinned digest.
- **The client build reads the docs.** `npm run build`, `dev` and `prepare` turn the docs site's
  pages into the client's Documentation. A page the reader doesn't understand stops the build. It
  needs Node 22.18 or later.

## 0.2.0 - 2026-10-04

The first public release of Sift.

### What changed for you

- **Sift is open source.** Its source is public on GitHub under the AGPL-3.0 licence, where you can
  read it, report a problem or suggest a change.
- **Every release is listed here from now on.** What a release changes for you is written under its
  version, newest first, and `Settings > Updates and Info` shows the same notes when a new version
  is ready.
- **The docs have a page for every screen and every setting,** and Help says where to ask a
  question.
