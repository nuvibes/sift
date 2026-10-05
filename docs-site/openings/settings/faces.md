Faces is where you set up face recognition: how Sift finds the faces in your library and which people it can recognize. To open it, go to [Settings > Faces](/settings/faces).

Come here to check who Sift can recognize, or to move facial fingerprints between Sift libraries. Sift recognizes faces with the InsightFace models, or with YuNet and ArcFace when you choose Permissive.

![The Faces pane in Settings](../../../assets/screens/settings-faces.jpg)

## On this pane

The first rows have no heading: the switch, how thorough Sift is, when it runs, and **More settings**. The switch is turned on and off in [Settings > Importing](/settings/importing#importing.recognition). Then the pane draws these groups, in this order:

- <a id="faces.people"></a>**People Sift can recognize**: everyone with confirmed faces or starter pictures, so you can check before adding someone. Type in the box over the list to find someone by name.
- <a id="faces.waiting"></a>**Waiting for a matching face**: people from a facial fingerprints file or a folder that no face in your library matches yet. Type in the box over the list to find someone by name. The list scrolls in place, as the one above does. Each line says how many faces it holds and how many were confirmed. It also names the file or folder it came from.
- <a id="faces.packs"></a>**Facial fingerprints**: what Sift learned from the faces of the people it can recognize. They move between Sift libraries as one file, without any of your files.
- <a id="faces.folder"></a>**Creating facial fingerprints**: teach Sift many people together from photos you already keep, one subfolder for each person.
- **Start over**: delete every face Sift found.

## Rows the pane draws by hand

- <a id="faces.more"></a>**More settings**: **Edit** opens the device and models, the lowest face quality and the longest time on one file. It also holds **Group faces again** and **Identify all files again**. A download that can't connect shows why and what to check. It uses the proxy set in Windows, or in `HTTPS_PROXY`. A proxy set by an automatic configuration script isn't read.
- <a id="faces.starters"></a>**Use stash-box pictures as starters**: **Run** keeps up to five stash-box pictures of a person linked to a stash-box who has no confirmed faces.
- **Remove** and **Create a person**, at the end of each row waiting for a matching face: forget their facial fingerprints, or create that person now. Nothing in your library changes when you remove one.
- <a id="faces.pack-import"></a>**Add people Sift can recognize from a facial fingerprints file to your library**: **Import file** opens the file chooser. Sift can then recognize the people in the file, and nothing is added to your library.
- <a id="faces.pack-export"></a>**Export your library's facial fingerprints**: **Export** saves what Sift learned about everyone it can recognize as one file. **Choose people** opens the list of who goes in it, with two choices at its head. **Include everyone, except the people you pick** starts with everyone ticked, and you untick whoever stays out. **Include only the people you pick** starts with nobody ticked, and you tick whoever goes in. The press at the foot says how many people the file will carry, and Sift remembers your choice for your account. In that list, anyone with fewer than 20 confirmed faces wears the strength bar their page draws. The words beside it say "Only N confirmed faces: Sift recognizes them less surely". The file carries each person's count, so the library that imports it shows it.
- <a id="faces.pack-pictures"></a>**Include their face pictures**: facial fingerprints are numbers made by one face model, and a Sift set to a different model can't compare them. With the pictures in the file, the other library measures the faces again with its own model, so the file works anywhere. Without them, no picture of a real face leaves your computer.
- <a id="faces.folder-import"></a>**Import a structured folder to create facial fingerprints**: **Import folder** opens the folder chooser. Sift keeps the face it learns from each photo and never adds the photo to your library.
- <a id="faces.forget"></a>**Face data**: **Delete face data** deletes every face Sift found and everything it learned from them. Your files aren't touched.
