---
name: images
description: Inspect and edit images with Pillow - resize, crop, convert formats (PNG, JPEG, WebP, PDF), compress for email or the web, add a caption or watermark, strip location metadata, and build contact sheets. Use whenever a task changes an image file or needs images prepared for a document, deck or email.
metadata:
  category: documents
---

# Images

Runs in the sandbox with `run_code`; inputs are in `in/`, write results to `out/`.

```bash
I=skills/core/images/scripts/image_tools.py
python $I info in/photo.jpg                                  # size, mode, format, DPI, EXIF (incl. GPS)
python $I resize in/photo.jpg out/photo-1600.jpg --max 1600  # longest side, keeps aspect ratio
python $I resize in/logo.png out/logo-512.png --width 512
python $I crop in/shot.png out/crop.png --box 100,80,900,600  # left,top,right,bottom in pixels
python $I crop in/photo.jpg out/square.jpg --aspect 1:1      # centred crop to an aspect ratio
python $I convert in/scan.png out/scan.pdf                   # or .jpg/.webp; several inputs -> one PDF
python $I compress in/photo.jpg out/photo-small.jpg --kb 300 # lowers quality/size to fit
python $I watermark in/photo.jpg out/marked.jpg "Draft · Shivam"
python $I strip in/photo.jpg out/clean.jpg                   # remove EXIF/GPS before sharing
python $I sheet out/sheet.png in/a.jpg in/b.jpg in/c.jpg --cols 3   # contact sheet with names
```

## Notes

- Photos from phones carry GPS in EXIF: run `strip` before sending them to anyone else.
- JPEG has no transparency; converting a transparent PNG to JPEG puts it on white.
- For documents and decks, about 1600 px on the longest side is plenty; 2400 px for print.
- `info` prints the EXIF orientation; `resize`, `crop` and `convert` apply it, so images
  aren't sideways afterwards.
- Charts are made with `core/charts`, not here.
