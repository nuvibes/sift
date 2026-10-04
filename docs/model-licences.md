# The models Sift can download, and their licenses

Sift ships no model files, in its source or in its installer. Faces, Smart Search and Watermarks
each download their models when you turn the feature on, from the publisher's own address and on
the publisher's terms.

This page records each model's license and what it was trained on, read from the publisher's own
files rather than from documentation about them. A model added to a catalog
(`src/sift/slices/faces/weights.py`, `src/sift/slices/semantic/weights.py`,
`src/sift/slices/watermarks/weights.py`) gets a row here, written after reading that file's license.

## What Sift does with every model

- **Nothing until you turn the feature on.** Each feature is off until an admin turns it on, and
  says how much it downloads before it does.
- **Everything on this device.** No face, picture, frame or anything read from one is ever sent
  anywhere, under any setting.
- **Checked before use.** Every file is checked against a recorded SHA-256 each time it's loaded.
  A truncated download often loads and then returns wrong numbers without any error.
- **Never a surprise.** A download is a task you can see in Tasks and Activity, cancel and resume.
  You can also install a file you already have instead of downloading it.

## Faces

Faces uses two models together: a detector that finds faces, and a recognizer that describes each
one so similar faces group together. Both must come from the same set, because a detector and a
recognizer from different sets match worse without any error.
[`Settings > Faces > Recognition models`](https://nuvibes.github.io/sift/settings/faces/#faces.model)
offers two sets.

### Accurate (the default)

| | |
|---|---|
| **Detector** | SCRFD 500M, from the InsightFace `buffalo_s` release |
| **Recognizer** | ArcFace `w600k_r50`, from the InsightFace `buffalo_l` release |
| **License** | **Non-commercial research only.** The InsightFace model zoo states: *"ALL models are available for non-commercial research purposes only."* InsightFace's code is MIT; its pretrained models aren't covered by that, and the restriction covers redistribution as well as commercial use. |
| **Trained on** | The detector on WIDER FACE; the recognizer on WebFace600K, a subset of WebFace42M, which is faces gathered from the web. |
| **Download** | The whole of each release archive, about 416 MB, for the two files in them (about 177 MB). |
| **Downloaded from** | The InsightFace v0.7 release on GitHub: `https://github.com/deepinsight/insightface/releases/download/v0.7/buffalo_s.zip` and `.../buffalo_l.zip` |

This is why Sift doesn't ship them: putting them in a public installer would hand them on under
terms nobody granted. Downloading them for your own use on your own computer is your decision, and
Sift tells you so before it downloads anything.

### Permissive

| | |
|---|---|
| **Detector** | YuNet, from the OpenCV model zoo |
| **Recognizer** | ArcFace ResNet100, from the ONNX model zoo |
| **License** | **MIT** for YuNet (its `models/face_detection_yunet/LICENSE`) and **Apache-2.0** for ArcFace ResNet100 (declared by the repository and by the model's own README). Both allow use, change and redistribution. |
| **Trained on** | YuNet on WIDER FACE. ArcFace ResNet100 on Refined MS-Celeb-1M: 3.8 million images of 85,000 people. Microsoft withdrew MS-Celeb-1M over how it was assembled; this is recorded so you know, not as a judgment. |
| **Download** | About 261 MB. |
| **Downloaded from** | GitHub: YuNet from `https://github.com/opencv/opencv_zoo` (`models/face_detection_yunet/face_detection_yunet_2026may.onnx`), ArcFace ResNet100 from `https://github.com/onnx/models` (`validated/vision/body_analysis/arcface/model/arcfaceresnet100-8.onnx`) |

### How the two compare

Both were measured against a gallery of several hundred people, with a quarter of them held out so
their faces arrived as strangers. Most faces in a library belong to nobody you've named, so that's
the case that matters. Where about one match in a hundred is wrong:

| Recognition models | Appearances correctly identified |
|---|---|
| Accurate | about 98 in 100 |
| Permissive | about 90 in 100 |

The choice is between accuracy and license terms, so Sift makes it a setting rather than making it
for you.

## Smart Search

Smart Search uses three files together: a model that reads pictures, a model that reads the words
you type into the same space, and the vocabulary that turns a sentence into what the second model
expects. Files from different downloads don't fail; they return numbers that mean nothing to each
other.

| | |
|---|---|
| **Model** | SigLIP-2 base, patch 16, 224 pixels, published by Google as `google/siglip2-base-patch16-224` |
| **Files downloaded** | An ONNX conversion of it, published by a third party as `onnx-community/siglip2-base-patch16-224-ONNX` |
| **Downloaded from** | Hugging Face: `https://huggingface.co/onnx-community/siglip2-base-patch16-224-ONNX` |
| **License** | **Apache-2.0**, the license of the model the files are converted from. See the note below. |
| **Trained on** | WebLI, a web-scale collection of images paired with the text found beside them. Nearly every model of this kind is trained this way; this is recorded so you know, not as a judgment. |

### The conversion declares no license of its own

The files Sift downloads aren't the ones Google published. They're a format conversion, made and
hosted by a third party, and that repository has no license file and declares no license. It says
it's `google/siglip2-base-patch16-224` "with ONNX weights", naming the model it comes from.

- The model it converts is **Apache-2.0**, which allows making and distributing a derivative work,
  so making the conversion was allowed.
- Apache-2.0 therefore governs the weights in those files. The converter didn't restate it, which
  is untidy rather than restrictive.
- **Sift redistributes none of it.** You download the files yourself, on your own computer, when
  you turn Smart Search on, as with the face models.

That repository also calls itself "intended to be a temporary solution". Every file is pinned by
its SHA-256, so a file that moves or changes fails the download and says so; it's never installed
in place of what was described. If the repository goes, the fix is a new address and new digests in
the catalog.

### Compact and Full

[`Settings > Smart Search > Description models`](https://nuvibes.github.io/sift/settings/semantic/#semantic.model)
offers two choices. The table compares them
over ten photographs and ten plain descriptions of what's in them.

| Description models | Download | Time a picture, one core | Right answer first |
|---|---|---|---|
| Compact (the default) | about 380 MB | about 0.1 s | 10 of 10, both directions |
| Full | about 1.5 GB | about 0.15 s | 10 of 10, both directions |

Neither was ever beaten by nonsense text. The difference is how far ahead the right answer sits:
Compact's lead over the runner-up was about half of Full's. On a large library of similar files
that lead is headroom, which is why Full is offered.

The model that reads words is the larger file in both: it carries a vocabulary of 256,000 words.
It runs once a search rather than once a picture, so its size costs download and memory, never
speed.

## Watermarks

Watermarks reads the Site address burned into a picture and adds the file to that Site.

| | |
|---|---|
| **Models** | PP-OCRv4 mobile, the detection model and the English recognition model, published by Baidu as part of PaddleOCR |
| **Files downloaded** | ONNX conversions of them, published by a third party as `tobiichioriguchi/PP-OCRv4_mobile_det_onnx` and `tobiichioriguchi/en_PP-OCRv4_mobile_rec_onnx` |
| **Downloaded from** | Hugging Face: `https://huggingface.co/tobiichioriguchi/PP-OCRv4_mobile_det_onnx` and `https://huggingface.co/tobiichioriguchi/en_PP-OCRv4_mobile_rec_onnx` |
| **License** | **Apache-2.0**, declared by both conversion repositories in their model cards, and the same as the upstream project, whose `LICENSE` file is the Apache License 2.0 (`Copyright (c) 2016 PaddlePaddle Authors`). Neither conversion carries a `LICENSE` file; on Hugging Face the declaration in the card's metadata is the usual place. |
| **Trained on** | Public collections of text in pictures, assembled by the publisher for PaddleOCR. This is recorded so you know, not as a judgment. |
| **Download** | About 12 MB for the pair: 4.8 MB for detection and 7.7 MB for recognition. |

Unlike Smart Search's conversion, these two declare their license themselves, and it agrees with
upstream.

The recognition model reads the Latin alphabet only. Sift uses it to read Site addresses, which are
written in Latin letters; a watermark in another script comes back as nothing found. Replace that file
before reusing these models as a general reader of text in pictures.
