Maintenance is where you tidy Sift's own data: the database, the thumbnails and previews, and the files Sift couldn't import. To open it, go to [Settings > Maintenance](/settings/maintenance).

Come here to free disk space or to bring old thumbnails and previews up to date. Every row says what it would delete before you choose **Delete**, and every delete is permanent.

![The Maintenance pane in Settings](../../../assets/screens/settings-maintenance.jpg)

## On this pane

The first rows have no heading. Then the pane draws two groups:

- <a id="maintenance.tidy"></a>**Cleanup**: records and files left behind by things you have already done, one row for each kind, each with **Delete**.
- <a id="maintenance.quarantine"></a>**Quarantined files**: files Sift couldn't import. Review them in Organize, and choose how many days to keep them.

## Rows the pane draws by hand

- **Optimize the database**: **Optimize** frees disk space and can speed up search and slow screens. Nothing in your library changes.
- <a id="maintenance.rebuild"></a>**Generate all thumbnails again**: **Generate** makes every thumbnail again, so files imported before a size change get new ones.
- **Generate all hover previews again**: **Generate** brings every hover preview up to the length you chose.
- <a id="maintenance.survey"></a>**Count thumbnails and previews on disk**: **Count now** reads every thumbnail and preview on disk for the counts below.

The **Cleanup** rows, one for each kind of leftover, each with **Delete**:

- Records of files whose folders were all removed from your library.
- Work that ran out of attempts and stopped being retried.
- **Leftover thumbnails and previews**: pictures made from files Sift doesn't have now.
- **Repackaged copies of videos**: second copies kept so videos skip smoothly, offered while repair is off.
- Descriptions Smart Search kept of files that left your library.
- Descriptions from a model you switched away from.
- **Face pictures from earlier scans**: cropped faces no screen shows any more.
- Samples made while you chose how much to compress a file.

The duplicate settings listed under **Every setting** are drawn on Organize, on the **Near duplicates** card, beside the groups they decide.
