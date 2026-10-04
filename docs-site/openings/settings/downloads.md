Downloads is where you choose how Sift downloads from a link: the quality, the limits, where files land and what they're named. To open it, go to [Settings > Downloads](/settings/downloads).

Come here to set a download folder, a name template or a size limit. Sift downloads videos with yt-dlp and galleries of photos with gallery-dl.

![The Downloads pane in Settings](../../../assets/screens/settings-downloads.jpg)

## On this pane

The pane draws these groups, in this order:

- **What gets downloaded**: the video quality, skipping links you already downloaded, and adding creators to People.
- **Limits**: the smallest and largest file to keep, and the free space to leave on the disk.
- **When a download finishes**: the message and the sound.
- **Folders and file names**: the download folder, the name template and its presets.
- **Per-Site settings**: settings of a Site's own.

## Rows the pane draws by hand

- <a id="downloads.default_folder"></a>**Download folder**: where a download lands when you don't choose a folder for it. **Choose a folder** sets it.
- <a id="downloads.name_template"></a>**Name template for other addresses**: the file name for a link from an address Sift has no Site for.
- **Name presets**: a ready-made rule. Choosing one fills the template.
- **Give a Site its own settings**: **Select a Site** to give it its own folder, name template or downloader.
- <a id="downloads.more"></a>**More settings**: **Edit** opens how many downloads run at the same time, the speed limit, the wait between requests, retries and time-outs.

## What a name template can hold

Choose one of these to add it to the name:

- `{site}`: the Site it came from.
- `{creator}`: whoever posted it, where the Site says.
- `{name}`: the name the file already had, which is usually the Site's own title for it.
- `{date}`: the date it was downloaded.
- `{time}`: the time it was downloaded.
- `{id}`: the post's own ID on the Site.
- `{n}`: which file of the post it is, as 1, 2, 3.
- `{title}`: the post's title or caption, where the Site has one.
- `{posted}`: the date it was posted, where the Site says.
