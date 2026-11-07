# PixelProof

Find where your images are being used online. PixelProof runs fully on your Windows PC.
It never uploads your images anywhere.

**Double-click `PixelProof.bat`** and the PixelProof app opens in its own window. Everything is a click away:

| Screen | What it is for |
|---|---|
| **Home** | Paste a page link and press **Check page**. Also: check a list of links (.txt) or a picture file. |
| **Review** | Each match shows **ours vs theirs** side by side (or **Swipe** to slide between them). Press **Y** (stolen), **N** (not a match) or **→** (skip). |
| **Photographers** | Every photographer from your folders, with their matches, filtered by year and quarter. |
| **Sites** | Every site where your images turned up. Sister sites (one company) are grouped. |
| **Crawler** | Visits every site on your watch list, finds its newest pages and checks every picture. Matches land in Review. It can also read the free GDELT news feed to find new sites using your pictures. |
| **Site lists** | Banned sites, sites approved for one photographer (licensed), sister-site companies, and sites to watch. Import a list from a file. |
| **My images** | Add a folder of your images (fingerprinting), with a progress bar and a Stop button. |

On every match you can:

- **Open where found** opens the page in your normal browser.
- **Proof of Discovery (PDF)** saves `data\proofs\PX-2026-000123.pdf`: the page link, the date and time it was found,
  the match ID, ours and theirs side by side, and SHA-256 fingerprints.
- **Yes, it's stolen** shows the proof first. Save the PDF, then confirm again to mark it. Sister sites with the same
  article can be marked in the same step.
- **Approve for (photographer)** marks the site as licensed for that photographer only.
- **Ban site** hides the site for everyone (asks first, and offers its sister sites too).

**Evidence is saved the moment a match is found.** Their picture (exactly as downloaded) and the page are kept in
`data\evidence\<match ID>`, with SHA-256 fingerprints, so the proof still holds if the site changes or deletes it later.

When you close the app window, PixelProof stops by itself a few seconds later.
The old black-window menu is still there as `PixelProof (text menu).bat`.

### The crawler

Put sites on your watch list (**Site lists**, or **Import site list** with a .txt file), then open **Crawler**,
pick how many of the newest pages to check per site, and press **Start crawling**.

- It finds each site's newest pages from its sitemap or news feed (or its home page if it has neither).
- It follows each site's robots.txt and waits a moment between pages, so it is polite to the sites.
- Pages it checked in the last 7 days are skipped, so the next run only looks at new pages.
- Pictures that appear on many pages (logos, ads) are only checked once per run.
- Giant platforms like Google, YouTube or Facebook are skipped; use **Check one page** there.
- Sites built entirely with JavaScript may show few pictures; that is a known limit for now.
- You can stop any time and start again later.
- Sites where you marked a match as stolen go first (even if they are not on the watch list), then sites with
  earlier matches, then sites never crawled, then the ones crawled longest ago.

### Find new sites from the news

**Crawler → Find new sites from the news** reads the free news list from the GDELT project
(gdeltproject.org, no account needed). Every 15 minutes it lists the new articles it saw worldwide, with each
article's main picture. PixelProof checks every main picture against your library. When one is yours, it checks
the whole article (with evidence, like **Check one page**) and **adds that site to your watch list** by itself.

- Pick how far back to look (1 hour to 3 days). "US sites only" is on by default.
- News lists it already read are skipped next time, so run it as often as you like.

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

Double-click **`PixelProof.bat`**. The app opens (see the table at the top).

**Updating:** download the new ZIP and unzip it over your PixelProof folder, choosing **Replace the files**.
The first start after an update may add a few new parts (about a minute). Your library and matches carry over.

### The text menu (optional)

`PixelProof (text menu).bat` is the step 1 and 2 menu in a black window:

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
| `9` | Approve a site for one photographer (licensed use) |
| `10` | See everything found so far (repeat copies, sister sites, per photographer and per site) |
| `11` | Edit `approved.txt` and `sister-sites.txt` in Notepad |

When you check a **list of links** (option `4`), PixelProof opens the summary at the end
instead of one browser tab per link. Each link's own report is still saved in the reports folder.

## Approved sites and sister sites

`data\approved.txt` (one line per photographer):

```
Yang = nypost.com
Maya Chen = example.com, partner-site.org
```

`data\sister-sites.txt` (one line per company, only needed for groups PixelProof can't spot by itself):

```
Tuscaloosa radio group = catfishtuscaloosa.com, wtug.com
```

You can put a company name from `sister-sites.txt` in `approved.txt` to approve all of its sites at once.
Changes count right away, even for matches found earlier.

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
pixelproof approve "Yang" nypost.com
pixelproof summary
pixelproof doctor
pixelproof app
pixelproof proof PX-2026-000123
```

## What's in the data folder

| File | What it is |
|---|---|
| `library.db` | One row per image: path, photographer, year, quarter, date, fingerprints, every match found, and every page checked |
| `clip.faiss`, `hash.faiss` | The fast search indexes (rebuilt automatically) |
| `reports\` | Every check report, as a web page you can open again |
| `banned.txt` | Sites to skip |
| `approved.txt` | Sites licensed for one photographer |
| `sister-sites.txt` | Groups of sites owned by one company |
| `reports\summary.html` | Everything found so far (menu option 10) |
| `evidence\` | Their picture and the page, saved the moment each match was first found |
| `proofs\` | Proof of Discovery PDFs, one per match ID |
| `watch.txt` | Sites to watch (for the crawler) |
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
| Possible | CLIP similarity 0.89 to 0.92, needs a human look |

Every Edited or Possible hit is then **double-checked**: the small details (corners, edges) of the two pictures
must line up (at least 15 of them). A crop, filter, banner or resize of the same photo keeps dozens to
hundreds; a different photo of the same person or scene keeps almost none and is dropped. If your file has
moved and can't be opened, the hit is kept with a note.

The starting values live in `pixelproof\config.py` and will be tuned on your real images.

## For developers

```
pip install pytest
python -m pytest
```
