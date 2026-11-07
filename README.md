# PixelProof

Find where your images are being used online. PixelProof runs fully on your Windows PC.
It never uploads your images anywhere.

This is **step 1, the quick prototype**:

1. **Fingerprint your images.** Point it at a folder (it works with Google Drive for desktop).
   It reads each image once, makes a small "fingerprint", and remembers the photographer,
   year and quarter from your folders (`Photographers \ Name \ Year \ Q1`) and the date from the file name.
   It never copies your images.
2. **Check one page.** Paste a link. PixelProof downloads the pictures on that page, compares them
   with your library, and opens a report in your browser showing **ours vs theirs** side by side,
   with a match ID for each hit.

The crawler, the full desktop app and Proof of Discovery come in later steps.

---

## Setup (one time, about 20 minutes)

**You need:** Windows 10 or 11, Python 3.10 to 3.13, and an internet connection for the setup downloads.

1. **Check Python.** Press the Windows key, type `cmd`, press Enter, then type `py --version` and press Enter.
   - If you see `Python 3.12.x` (or 3.10, 3.11, 3.13), you're good.
   - If not, install Python 3.12 from <https://www.python.org/downloads/>. On the first install screen,
     tick **"Add python.exe to PATH"**.
2. **Put the PixelProof folder somewhere simple**, for example `C:\PixelProof`.
   (Not inside Google Drive. The `data` folder it makes holds your fingerprint index.)
3. **Double-click `install.bat`.** A black window opens and installs everything.
   The first time it downloads about 3 GB (the AI engine with graphics card support), so be patient.
   At the end it runs a check and should say **All good!** and name your graphics card.

## Everyday use

Double-click **`PixelProof.bat`**. You get a menu:

| Type | What it does |
|---|---|
| `1` | Fingerprint a folder of your images |
| `2` | Check one page (paste a link) |
| `3` | Check a picture file on this PC (handy for testing) |
| `4` | Check a list of links (a `.txt` file, one link per line) |
| `5` | Show what's in your library (images per photographer) |
| `6` | Open the reports folder |
| `7` | Edit your ban list (sites to skip, one per line) |
| `8` | Check that everything works |

**Pasting a folder path:** in File Explorer, right-click the folder and choose **Copy as path**
(on Windows 10, hold **Shift** while you right-click). In the black window, **right-click** to paste.

**Stopping:** press **Ctrl+C** or close the window. Your progress is saved.
Run the same folder again and it carries on where it stopped. Later runs only look at new or changed files.

## Commands (optional)

If you prefer typing, open `cmd` in the PixelProof folder and run:

```
.venv\Scripts\activate
pixelproof index "G:\My Drive\Photographers\Maya Chen"
pixelproof check https://example.com/some-article/
pixelproof status
pixelproof doctor
```

## What's in the data folder

| File | What it is |
|---|---|
| `library.db` | One row per image: path, photographer, year, quarter, date, fingerprints, and every match found |
| `clip.faiss`, `hash.faiss` | The fast search indexes (rebuilt automatically) |
| `reports\` | Every check report, as a web page you can open again |
| `banned.txt` | Sites to skip |
| `models\` | The AI model, downloaded once |

Roughly 2 KB per image, so about 2 GB per million images.

## How matching works

Each picture gets two fingerprints:

- **Perceptual hash** (64 on/off values): catches straight copies, even resized, recompressed or flipped.
- **CLIP** (512 numbers from a free AI model that runs offline): catches cropped, filtered or watermarked copies.

| Category | Rule (starting values) |
|---|---|
| Exact copy | hash differs by 8 bits or fewer |
| Edited copy | flipped, or CLIP similarity 0.92 or higher |
| Possible | CLIP similarity 0.85 to 0.92, needs a human look |

The starting values live in `pixelproof\config.py` and will be tuned on your real images.

## For developers

```
pip install pytest
python -m pytest
```
