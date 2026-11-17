# Ad-Match tool

Weekly tool: fixed list + Raddar flyer export -> one Excel file (12 columns) comparing the
Superstore price with the cheapest competitor price. Built from the spec of 15 Nov 2026.

**No LLM anywhere.** Matching = code rules + fuzzy spelling tolerance (rapidfuzz).
Reading images = Tesseract OCR running on this computer. No API key, no paid service.

## Install (once)

1. Python 3.10 or newer.
2. `pip install -r requirements.txt`
3. Tesseract OCR (free, local). Windows installer: search "UB Mannheim tesseract". After
   installing, make sure `tesseract` is on the PATH. Without it the tool still runs but
   prints a loud warning and skips image prices (member price, text-vs-image conflict).

## Run (every week)

    python -m admatch --list master_list.xlsx --raddar raddar_EXTRACTED.xlsx

Options: `--run-date 2026-11-10` (default: today), `--out output`, `--no-images`.

The tool takes ONLY the flyer week that starts on the next Thursday (run on a Thursday =
that Thursday). Output goes to `output/week_<nn>_<start date>/`:

- `ad_match_week_<nn>.xlsx` - the file. Sheets: **Ad Match** (the 12 columns), **Flags**,
  **Review** (offers that were close but not matched, and why), **Run info**.
- `raw/` - untouched copies of the inputs. `offers_extracted.csv` - every offer read.
- `images/` - downloaded pictures (also embedded in the Excel file).

Yellow price cell = the offer has flags (hover the cell, or see the Flags sheet).
Red banner at the top = a store is missing or images could not be checked.

## Config (folder `config/`) - change these, never the code

| File | What it holds |
|---|---|
| `settings.yaml` | stores, week rule, Raddar column names, fixed-list column, image settings |
| `matching.yaml` | 2% size tolerance, variant words, colour/type words, synonyms, wording to strip |
| `store_brands.yaml` | store-brand table and aliases |
| `product_knowledge.yaml` | what a brand means (Coke = Coca-Cola = cola) |
| `brands.txt` | brand names (tells the matcher "this word is a brand, not a product") |
| `french_english.yaml`, `abbreviations.yaml` | French words, short forms |

When a list item stays EMPTY, open the **Review** sheet: it shows the nearest flyer offers
and the exact reason ("flyer has extra product word: smoked", "size differs", ...). Fix the
list wording or add the word to the right config file.

## Tests

    python -m pytest tests -q

140 tests: one file per build step. Image tests use generated flyer-style pictures.

## Known limits (see the build notes)

- Costco is not in Raddar -> always reported missing.
- Raddar sometimes drops the size of a Superstore offer. Such matches are made on the name
  and flagged "size unclear"; a match with a confirmed size always wins over them.
- Flyer PDFs are not read (no sample PDF was available when this was built).
- The image reader has only been tested on generated images, not on real flyer photos.
