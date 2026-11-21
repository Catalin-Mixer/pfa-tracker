# PFA tax tracker

A small app that runs only on this computer, with no internet needed. It tracks the income,
deductible expenses, social contributions (CAS, CASS) and income tax of a Romanian PFA taxed
in the real system (sistem real), year by year.

It runs in two ways with the same features, the same results and the same files:

- **in the browser** (a local web page), and
- **as a desktop app** (a window of its own, made with Qt for Python), started from the
  `PFA-Tax-Tracker` executable.

Both read and write the same settings, invoices, expenses and proofs, so you can switch between
them at any time, or have both open at once.

It keeps **profiles**: each profile is one firm with its own settings, invoices, expenses and
proofs. Each app also has its own colour theme (light, dark or custom). See
[Profiles and themes](#profiles-and-themes).

## Contents

1. [Running the app](#running-the-app)
2. [The tabs](#the-tabs)
3. [How the taxes are calculated](#how-the-taxes-are-calculated)
4. [Files you can edit](#files-you-can-edit)
5. [Project structure](#project-structure)
6. [Profiles and themes](#profiles-and-themes)
7. [Tests](#tests)
8. [Your data, backups and moving to another computer](#your-data-backups-and-moving-to-another-computer)

## Running the app

### In the browser

```sh
cd ~/pfa-tracker
sh run.sh
```

Then open <http://127.0.0.1:8765>.

- `run.sh` does nothing if the app is already running.
- The desktop starts it when it boots.
- The **Shortcuts** menu has **Open PFA tax tracker**.
- To stop it: `pkill -f "port 8765"`.
- The log is in `data/server.log`.

### As a desktop app

- Double-click `PFA-Tax-Tracker` in the `pfa-tracker` folder, or use **PFA tax tracker
  (desktop)** in the **Shortcuts** menu, or run `~/pfa-tracker/PFA-Tax-Tracker`.
- From the source code instead of the executable: `cd ~/pfa-tracker && python3 launcher.py`.
- The window has the same four tabs. The year picker is at the top right. File choices (CSV
  import, proofs, backups) open the usual file dialogs, and a proof opens in the program your
  computer uses for that kind of file.
- When you come back to the window it reloads, so changes made meanwhile in the browser show
  up.

The executable can also run the browser version: `PFA-Tax-Tracker --web` (optionally
`--port 8766`, `--no-browser`). If the browser version is already running it just opens the page.

**Where the files are:** the executable keeps its data next to itself: `profiles.json`,
`profiles/`, `rates.csv` and `caen_codes.xlsx` in the folder it sits in. In `~/pfa-tracker` those
are exactly the browser version's files. Copied to an empty folder, it creates fresh copies of
those files there on first start and never overwrites existing ones. The `PFA_HOME`
environment variable points it at another folder.

### The Windows .exe

A Windows program can only be built on Windows; this computer runs Linux, so the executable
here is the Linux one. To make `PFA-Tax-Tracker.exe`:

1. Copy the `pfa-tracker` folder to the Windows computer.
2. Install Python 3.12 from python.org.
3. Double-click `packaging\build_windows.bat`. It sets up what it needs and builds
   `dist\PFA-Tax-Tracker.exe`.
4. Move the .exe into the `pfa-tracker` folder, so it uses the same files as the browser
   version.

To move your data there as well, use a backup (see the last section).

### Rebuilding the Linux executable

After changing the code: `cd ~/pfa-tracker && sh packaging/build_linux.sh` (about a minute and
a half). It replaces `PFA-Tax-Tracker`.

### What it needs

Python 3 with FastAPI, Uvicorn, Jinja2, python-multipart, openpyxl, Pillow, pypdf, defusedxml,
ReportLab (the invoice PDFs) and PySide6 (the desktop app), plus pytest and httpx for the tests
and PyInstaller for the executable. Elsewhere: `pip install -r requirements.txt` installs all of
them. The executable needs none of them; everything is inside it.

On this Linux machine Qt needs a small system library (`libxcb-cursor`) that isn't installed
and can't be downloaded here. A stand-in for it is in `desktop_support/`, and the launcher and
the executable load it automatically. It only means the window uses the plain X mouse cursors.

## The tabs

Every tab except Invoicing and Settings has a year picker at the top right: buttons while there are a few
years, a drop-down once there are more than six. Only the current year and the years that have
invoices or expenses are offered, never a future year; a year disappears from the picker when
its last record is deleted. The year you pick stays selected when you switch tabs.

Both versions have these tabs with the same contents; in the desktop app the year picker is a
drop-down at the top right of the window.

In the browser version, the web addresses never contain a year: every page has one fixed address (`/`, `/income`,
`/expenses`, `/invoicing`, `/settings`). The picker stores the chosen year in a cookie in the browser, and
the app checks it against the offered years every time. Anything added to an address
(`?year=1999`, `?anything`) is dropped, and you're sent to the plain page with a note, so a year
can't be reached by editing the address.

Nothing can be dated after today (except an invoice's due date): the date fields stop at today, and a CSV row, invoice or
expense with a later date is refused. Dates before 2000 are refused as well.

### Overview

The summary for the selected year:

- Gross income, deductible expenses and net income.
- The **total to pay**.
- A step-by-step breakdown: net income → CAS → CASS → taxable income → income tax. Each line
  says which rule applied, for example "net income is at least 24 minimum wages, so the base
  is 24 × 4.050,00".
- The values used for that year (minimum wage, thresholds, salaried job or not), with a
  warning if they are not yet marked as checked.
- A table comparing all years.

### Income

Import invoices from a CSV file, add one by hand, and see the invoices of the selected year,
each with its exchange rate and its value in RON.

**Adding an invoice by hand:** fill in the invoice date, the date of payment (optional), the
client, the invoice number (optional), the amount, the currency and, if you like, a proof
(PDF or image). The proof is stored in `data/attachments/invoices/`. It's named after the
invoice number and the client, as `<number>_<client>`, for example
`PFA-2026-001_Nordwind_Software_GmbH.pdf`. Diacritics and symbols are simplified, an invoice
without a number gives `no-number_<client>`, and `_2`, `_3`… is added if the name is already
taken. It downloads from the 📎 link in the list. An invoice identical to one already stored (same date, client, number, amount and
currency) is refused, unless it only adds the missing date of payment (see below).

**Date of payment:** for a PFA in the real system, income counts when the money is received, at
that day's BNR rate. So when the date of payment is given, it decides both the year the invoice
counts in and the exchange rate; without it, the invoice date is used. The date of payment can't
be before the invoice date (the form won't let you pick one, and the app refuses it anyway). The
CSV import accepts an optional `payment_date` column (also `paid_on`, `data_plata`,
`data_incasare`) with the same rules. Example: an invoice dated 20 Dec 2025 and paid on 8 Jan
2026 is 2026 income, converted at the 8 Jan 2026 rate.

**Editing an invoice:** every row has an *Edit* and a *Delete* button. *Edit* loads the invoice
into the form, which becomes *Edit invoice*, with *Save changes* and *Cancel*.

- The edited invoice is checked and converted again exactly like a new one: at the rate of its
  date of payment, or of its invoice date. Adding a payment date moves it to the payment year.
- It can't be made identical to another stored invoice.
- The proof stays, and is renamed if the number or client changed. You can also replace it with
  another file, or tick *Remove the current proof*.
- A wrong value is refused with the reason, and nothing is changed.

Amounts are stored in one form, so `100`, `100,00` and `100.0` are the same amount when
looking for duplicates.

**Recording a payment later:** an invoice stored without a date of payment gets one when you add
it again by hand, or import it again, with the same invoice date, client, number, amount and
currency and the date of payment filled in. It then moves to the payment year and is converted
again at the payment day's rate. A date of payment that is already recorded is never overwritten;
to change it, delete the invoice and add it again.

Required CSV columns:

- `date`
- `client`
- `amount`
- `currency`

`number` and `payment_date` columns are optional, and any other columns are ignored. Romanian column
names work too (`data`, `suma`, `valoare`, `moneda`, `valuta`, `nr`, `numar`,
`beneficiar`…). Example: [`samples/sample_invoices.csv`](samples/sample_invoices.csv).

Accepted formats:

- The file can be comma-, semicolon- or tab-separated, in UTF-8 or Windows-1250 encoding.
- Dates can be `2026-03-15`, `2026/03/15`, `2026.03.15`, `15.03.2026`, `15/03/2026` or
  `15-03-2026`. When the year comes last, the day always comes before the month.
- Amounts can be `1234.56`, `1234,56`, `1.234,56` or `1,234.56`. A single separator
  followed by exactly three digits counts as a thousands separator, so `1.500` is 1500.
  Ambiguous forms such as `12.34.56` and amounts of a million billion or more are refused.
- `LEI` is treated as `RON`.

How the import works:

- **Conversion:** each invoice is converted with the rate for its date. If that day has no rate
  (a weekend or holiday), the most recent earlier rate is used, going back at most 10 days.
  Those invoices show the rate date in amber.
- **Problems:** rows with a bad or future date, a bad amount, a missing client or currency,
  or no rate for the currency are listed and not imported. The other rows are imported.
- **Duplicates:** you can fix the file and import it again. An invoice with the same date,
  client, number, amount and currency as one already stored is skipped, never counted twice.
- **Fixing an invoice:** click its *Edit* button.

### Expenses

Expenses have the same *Edit* and *Delete* buttons as invoices, with the same rules for the
proof.

Add a deductible expense with its date (today at the latest), amount, currency, description,
an optional category and an optional proof (PDF or image). Expenses in another currency are
converted the same way as invoices. The tab lists the expenses of the selected year, each with
*Edit* and *Delete* buttons and, when there is a proof, a 📎 link that downloads it.

Proofs are kept apart from invoice proofs, in `data/attachments/expenses/`, named
`<id>_<file name>`. They go through exactly the same checks as invoice proofs. Allowed types:
PDF, PNG, JPG, GIF, WebP, TXT and XML, up to 20 MB each; one proof per invoice or expense.
Deleting the invoice or expense deletes its proof.

Every proof is checked for what it really contains, not just its name: a PDF must open and have
at least one page, an image must decode completely as the format its name says (a PNG renamed to .jpg, a photo
cut short, or an image over 40 million pixels is refused), an XML file must be well-formed (and may not use entity tricks), and a text file must
be UTF-8 text. Empty, damaged or renamed files are refused with the reason, and the invoice or
expense is not saved until you choose a proper file or leave the proof out.

### Invoicing

Make invoices from your own lists of clients and products or services. An invoice you issue is
numbered from its series, saved as a PDF, and **added to Income at once**, with the PDF as its
proof. Everything is filled in directly in the app; nothing needs importing. Each profile has its
own lists and its own numbering.

The tab has five parts:

- **New invoice**: choose the series, the client, the invoice date (today at the latest), a due
  date (filled in from the payment term) and, if the money is already in, the date of payment.
  Then add lines: a product or service, the quantity (empty = 1, up to 3 decimals; in a quantity
  a single comma is always the decimal mark, so 1,250 hours is one and a quarter) and,
  if this invoice needs it, a price other than the list price. Notes are printed at the bottom.
  The totals update as you type. **Issue invoice and add to Income** does everything in one
  go, and then offers the PDF and a link to Income.
- **Issued invoices**: number, date, client, total, whether it is still in Income, the credit
  notes (an invoice says "Partly credited by …" or "Fully credited by …", a credit note says
  which invoice it reverses), the PDF, and a **Credit note** button to reverse all or part of an
  invoice (see [Credit notes](#credit-notes-storno) below).
- **Clients**: name, CUI / tax ID, Trade register no. (optional), address, city, country, email,
  phone (optional). Name, tax ID, address, city, country and email are required, and checked:
  - a Romanian client's CUI must have a matching check digit (with or without `RO`); for a person,
    the CNP (13 digits, its check digit is checked too);
  - a client abroad needs a VAT or tax number of 4–20 letters and digits; an EU VAT number's
    country prefix must be the client's country (`DE…` for Germany, `EL…` for Greece);
  - the email and phone must be valid; one firm can't be in the list twice (`RO14399840` and
    `14399840` are the same firm);
  - characters the invoice can't print, such as emoji, are refused (here and in products and notes).
- **Products & services**: name (each name once), description (optional, printed under the
  name), unit (hour, day, week, month, piece, service, project, page, word, km, license,
  subscription), price per unit (a plain number more than 0, up to 4 decimals, no currency sign)
  and currency (RON or a currency in `rates.csv`).
- **Series & VAT**: the series, for example `PFA`: 1–10 letters and digits starting with a
  letter, and the next number. Invoices are numbered `<series>-<number>`: `PFA-0001`,
  `PFA-0002`… Each invoice issued moves the next number up by one. You can set the next number,
  for example to carry on from another program, but never back to a number already issued in
  that series. Numbers follow the dates: a new invoice can't be dated before the latest invoice
  already issued in its series. One series is the default. Here are also the **VAT settings**: VAT payer or not
  (not, at first: the invoice then says "Neplătitor de TVA — regim special de scutire pentru
  întreprinderile mici, art. 310 Cod fiscal"), the VAT
  rate (21% at first) and the usual payment term in days (15 at first; 0 = no due date).

Before the first invoice, fill in your firm's name, CUI and address in Settings: the invoice
shows them as the supplier, with the Trade Register number, IBAN, email and phone if you have
filled them in. The tab says so while they are missing.

**What issuing does**, all or nothing (if one step fails, no number is used and nothing is added;
and a form sent twice, by a double click or a page reload, issues only one invoice):

1. the invoice gets the next number of its series;
2. the PDF is made: A4, labels in Romanian and English, supplier, client, lines, totals, and for
   an invoice in another currency the BNR rate of the invoice date and the VAT (if any) and total in RON;
3. the invoice is added to Income with the client's name, the number, the currency and the
   amount **without VAT** (VAT collected is not income). Like any invoice, it counts in the year
   of the date of payment if you gave one, otherwise of the invoice date, converted at that
   day's rate;
4. the PDF becomes its proof, named like other invoice proofs: `PFA-0001_Client_SRL.pdf`.

In Income these invoices have an **issued** tag. You can edit them there like any invoice (for
example to add the date of payment later), but that changes only the Income record: the PDF
stays as it was issued, and the edit form says so. Deleting one from Income keeps it in Issued
invoices, marked as deleted from Income (its PDF can still be downloaded there), and its number is
never given out again. An invoice that was sent is cancelled or corrected with a credit note
rather than deleted.
Changing or deleting a client or product later does not change invoices already issued: the
PDF in *Issued invoices* is printed again from what was recorded when it was issued.

#### Credit notes (storno)

A credit note (factură storno) reverses all of an issued invoice or part of it: some of its lines,
each up to the quantity that is still left to credit. It always credits at the invoice's own
prices and with the invoice's VAT (its VAT rate, even if the VAT settings changed since). It can
never credit more than is left: crediting a line in several pieces adds up exactly to the
invoice's value and VAT, to the ban.

**Issuing one:** in *Issued invoices*, click **Credit note** on the invoice's row. The button is
not there for a credit note, for an invoice deleted from Income, or for an invoice that is already
fully credited. The form shows:

- each line of the invoice with its price, the quantity invoiced, the quantity already credited,
  what is left, and a *Quantity to credit* field (empty = none of that line; a comma is the
  decimal mark). A fully credited line says so. **Credit all** fills in everything that is left;
- the **series**: the invoice's own series at first. Credit notes share the numbers of their
  series with invoices (`PFA-0001` invoice, `PFA-0002` credit note, `PFA-0003` next invoice),
  and follow the same date order;
- the **date** (not before the invoice, not after today), an optional **date of the refund**
  and an optional **reason**, printed on the credit note (at most 500 characters).

The totals and "Still needed before issuing: …" update as you type, like on *New invoice*. Input
that is refused stays in the form with the reason, and nothing is issued. **Issue credit note
and add to Income** does everything in one go, all or nothing (a form sent twice issues one
credit note), and then offers the PDF and a link to Income:

1. the credit note gets the next number of its series;
2. its PDF is titled **FACTURĂ STORNO / CREDIT NOTE** and says *Stornează factura / Reverses
   invoice* with the invoice's number and date. Its quantities and totals are negative; it has no
   due date and no *Plata în contul / Payment to* line; the reason is printed when one was given.
   In another currency it shows the reversed invoice's BNR rate, with the VAT and total in RON
   worked out at that rate;
3. it is added to Income as a **negative** amount (see below), with its PDF as the proof
   (`PFA-0002_Client_SRL.pdf`).

**How a credit note counts in Income and in the taxes:** its Income row has the client, the
credit note's number, the currency and the credited amount **without VAT, as a negative number**
(VAT is never income). It counts like any invoice: in the year of the date of the refund if you
gave one, otherwise of the credit note's own date, converted at that day's BNR rate. So it lowers
the gross income of that year, and the net income, CAS, CASS and income tax of that year are
worked out from the lower amount, as usual (see [How the taxes are calculated](#how-the-taxes-are-calculated)).
For example, an invoice of 1.000 RON and a credit note of −300 RON in the same year make a gross
income of 700 RON. Two things follow from counting it on its own date:

- a credit note issued in a later year (invoice in December 2025, credit note in January 2026)
  lowers the later year's income; the invoice's year stays as it was;
- in another currency, a full credit need not come to exactly 0 RON when the rate changed: an
  invoice of 100 EUR counted at 4,50 (450 RON) and a full credit note on a day when EUR is 5,00
  (−500 RON) together come to −50 RON.

Confirm the treatment of credit notes across years and currencies with your accountant.

In Income a credit note has the **issued** tag. It can be edited like any issued invoice (for
example to add the date of the refund later), but **its amount must stay negative**. Deleting a
credit note from Income keeps it in Issued invoices, and what it credited still counts as
credited on its invoice. Deleting an invoice that has credit notes is allowed, but the
confirmation warns that its credit notes stay in Income, where their negative amounts will then
count without the invoice.

What it does not do (see the intended limits in the tests):

- **No e-Factura.** The tracker makes the PDF only, not the XML file for ANAF's e-Factura
  system (SPV), so invoices and credit notes that must be reported there still have to be sent
  through SPV or another tool.
- One currency per invoice.
- No invoices or credit notes dated after today.
- A credit note credits quantities at the invoice's prices; a reduction of the price or value
  alone (a discount given afterwards) cannot be credited.
- For a VAT payer the VAT rate is always added; reverse charge for business clients in other EU
  countries is not applied automatically.

### Settings

- **Firm:** all of these are optional, but whatever you fill in must be in a valid format.
  - Name of the firm.
  - Identification code (CUI). The check digit is verified.
  - Trade Register number (ONRC), in the form `F<county>/<number>/<year>`, for example
    `F40/1234/2019`. The last part can also be the full registration date, `F40/1234/15.03.2019`.
    The county code must be 1–40, 51 or 52, and the year between 1990 and now. The app reads the
    PFA's registration year from this number.
  - Phone: a Romanian number (`0721 234 567`, `021 123 45 67`, `+40…`, `0040…`) or any
    international `+country` number. It's stored as `+40721234567`.
  - Email address.
  - Bank account (IBAN). The check digits are verified (mod 97). A Romanian IBAN must be RO + 2
    digits + a 4-letter bank code + 16 characters. It's stored in groups of four.
  - Address: 5 to 200 characters.
- **Activity:**
  - The main CAEN code and its version (Rev.3 or Rev.2, as on the registration certificate).
    The page shows the activity name and whether the code is marked as excluded from income tax.
  - Secondary activities: optional extra CAEN codes separated by commas. Each is checked against
    the list. Only the main activity counts for the income tax exclusion.
- **Salaried job:**
  - **I have a salaried job:** a single checkbox with no year. It records that you are employed
    *this year*, so the CASS floor doesn't apply to this year's PFA income. It never changes past
    years. When a new year starts the box is unticked again, because it's a new year, and last
    year keeps what you recorded.
  - **Earlier years with a salaried job:** opens to a list of the past years in which the PFA
    existed, from the registration year in the Trade Register number to last year. Tick the years
    that had a salaried job. Without a Trade Register number no earlier years can be chosen. If
    the PFA was registered this year, there are none. A year marked earlier that falls before the
    registration year stays in the list, labelled "outside the PFA's lifetime — ignored". It is
    left out of the tax calculation, and you can untick it. Years not shown in the list are never
    changed.
- **Tax values per year:** a read-only table of what `settings.json` contains for each offered
  year.
- **CAEN list** and **Exchange rates:** which files are used and what they contain.

## How the taxes are calculated

For each calendar year, with MW = the minimum wage for that year:

| Item | Rule |
|------|------|
| Gross income | sum of the invoices received in the year, in RON: by date of payment and at that day's rate when given, otherwise by invoice date. Credit notes count the same way, as negative amounts: by the date of the refund, otherwise by their own date |
| Net income | gross income − deductible expenses |
| CAS (pension), 25% | net income under 12 × MW: **0**; from 12 × MW up to just under 24 × MW: 25% of **12 × MW**; from 24 × MW: 25% of **24 × MW** |
| CASS (health), 10% | 10% of net income, with the base kept between a floor (6 × MW) and a ceiling (60 × MW for 2025, 72 × MW for 2026). Without a salaried job, a low or negative net income still pays CASS on the floor. With a salaried job that year, the floor is dropped. |
| Taxable income | net income − CAS − CASS, not below 0 |
| Income tax, 10% | 10% of taxable income; 0 if your CAEN code is marked as excluded for that year in `caen_codes.xlsx` |
| Total to pay | CAS + CASS + income tax |

Amounts are rounded to the nearest ban at each step.

**This is an estimate.** Special cases are not handled: pensioners, people insured in another
way, opting in to CAS below the threshold, and personal exemptions such as those for people
with disabilities. Check the final figures for Declarația unică with your accountant.

The tax rules count foreign-currency income at the BNR rate of the day the money was received,
which is why the date of payment, when given, decides both the year and the rate.

## Files you can edit

The app reads these files every time a page loads or an import runs, so you don't need to
restart it after editing them.

### `settings.json` — firm details and tax values per year

Each profile has its own, in `profiles/<id>/settings.json` (the Settings tab shows the exact
path of the profile in use). If the file is missing (for example when `PFA_DB` or `PFA_SETTINGS`
points the tracker at a `settings.json` that doesn't exist yet), the pages say "settings file not
found", and saving the Settings form makes it, starting like a new profile: your firm details and
the default tax values per year, with no salaried job marked. A file that exists but can't be read
is never replaced; the error says what is wrong with it.

```json
{
  "firm": { "name": "…", "cui": "RO…", "caen_code": "6210", "caen_revision": "3" },
  "years": {
    "2026": {
      "minimum_wage": 4050,
      "cas_rate": 0.25, "cas_low_wages": 12, "cas_high_wages": 24,
      "cass_rate": 0.10, "cass_floor_wages": 6, "cass_ceiling_wages": 72,
      "income_tax_rate": 0.10,
      "has_salary": false,
      "checked": false,
      "note": "…"
    }
  }
}
```

| Key | Meaning |
|-----|---------|
| `firm` | set on the Settings page: `name`, `cui`, `trade_register`, `phone`, `email`, `iban`, `address`, `caen_code`, `caen_revision` (`"3"` or `"2"`), `secondary_caen` (a list) |
| `minimum_wage` | gross national minimum wage (salariul minim brut pe țară) in RON used for CAS/CASS: the one in force at the deadline of the Declarația unică for that year (25 May) |
| `cas_rate`, `cas_low_wages`, `cas_high_wages` | CAS rate and the two thresholds, in minimum wages (12 and 24) |
| `cass_rate`, `cass_floor_wages`, `cass_ceiling_wages` | CASS rate, floor and ceiling, in minimum wages |
| `income_tax_rate` | income tax rate applied to taxable income |
| `has_salary` | salaried job that year: set for the current year by the *I have a salaried job* checkbox, and for earlier years by the *Earlier years* list; never copied from another year |
| `checked` | set to `true` once you have confirmed the values against an official source; until then the Overview shows a warning |
| `note` | free text shown on the Overview |

To add a year, copy a year block and change the values. A year that isn't in the file uses the
most recent earlier year's values, and the Overview warns about it.

The 2025 and 2026 values were entered from memory, without an official source to check against,
and are marked `"checked": false`. The 2026 minimum wage (4050, assuming the rise to 4325 only
starts on 1 July 2026) and the 2026 CASS ceiling (72 minimum wages) especially need confirming.

### `rates.csv` — exchange rates

The rates are in RON per 1 unit of currency, with one row per day. The file delivered with the
app holds **made-up** EUR and USD rates for 2025–2026, so replace them with real BNR rates.

Two layouts are accepted. Either one column per currency:

```
date,EUR,USD
2026-03-16,4.9712,4.5531
```

or one row per day and currency:

```
date,currency,rate
2026-03-16,EUR,4.9712
```

Invoices and expenses already stored keep the rate they were converted with. To re-convert
one, delete it and add it again.

### `caen_codes.xlsx` — CAEN codes and income tax exclusions

Open it in LibreOffice or Excel. It has three sheets:

- **CAEN Rev.3:** 651 codes, in Romanian and English.
- **CAEN Rev.2:** 615 codes, in English, each with its Rev.3 equivalents.
- **Read me:** where the lists come from. They were taken from open-source software packages
  that contain the EU NACE classification, which CAEN is based on.

To mark a code, write `yes` in its **Excluded from income tax** column. You can also fill in
**Valid from** and **Valid to** (years) and the legal basis. No code is marked as delivered:
no CAEN-based exemption from income tax is known for PFA income in the real system. Confirm
this with your accountant.

## Project structure

```
pfa-tracker/
├── README.md             this file
├── run.sh                starts the app on port 8765 (does nothing if it is already running)
├── app.py                entry point for uvicorn (browser version)
├── launcher.py           starts the desktop app, or the browser version with --web
├── PFA-Tax-Tracker       the executable (Linux) — desktop app, or --web
├── packaging/            how the executable is built (PyInstaller spec, Linux and Windows build scripts)
├── desktop_support/      the stand-in Qt library for this Linux machine (see above)
├── requirements.txt      Python packages needed
├── profiles.json         the list of profiles
├── profiles/<id>/        one folder per profile:
│   ├── settings.json     its firm details, tax values per year and theme   (you edit)
│   └── data/             its pfa.sqlite, attachments/ (proofs) and backups/
├── deleted-profiles/     backups of deleted profiles
├── rates.csv             daily exchange rates                          (you edit)
├── caen_codes.xlsx       CAEN lists and income tax exclusions          (you edit)
├── fonts/                DejaVu Sans, used in the invoice PDFs (free licence in LICENSE-DejaVu.txt)
├── samples/
│   └── sample_invoices.csv   made-up invoices to try the import
├── data/
│   ├── pfa.sqlite        your invoices and expenses
│   ├── attachments/      proofs: invoices/ (<number>_<client>) and expenses/ (<id>_<file name>)
│   ├── backups/          safety copies made before a restore
│   └── server.log        the app's log
├── tests/                the automated tests and the report maker (see below)
├── test-report.html      the latest full test report
├── tracker/              the application code
│   ├── config.py         file locations and constants
│   ├── db.py             SQLite tables and queries
│   ├── parsing.py        reading dates, amounts and CSV files
│   ├── rates.py          exchange rates and conversion to RON
│   ├── invoices.py       invoices: CSV import and adding by hand
│   ├── invoicing.py      clients, products/services, series; issuing an invoice or a credit note (PDF) into Income
│   ├── attachments.py    storing and serving proofs
│   ├── backup.py         the backup zip: making, checking and restoring it
│   ├── settings.py       settings.json, firm details, CUI check
│   ├── caen.py           reading caen_codes.xlsx, income tax exclusion
│   ├── taxes.py          CAS, CASS and income tax calculation
│   ├── formatting.py     number formatting for the pages (12.345,67)
│   ├── services.py       everything the app can do, shared by both front ends
│   ├── web.py            the browser front end (FastAPI routes and pages)
│   └── desktop/          the desktop front end (Qt): one file per tab
├── templates/            HTML pages: base (layout and tabs), overview, income, expenses, invoicing_*, settings
├── static/style.css      the look of the pages
└── static/invoicing.js   live totals and extra lines on the New invoice page, and the credit note form
```

The app can use other file locations through environment variables. This is useful for trying
things on a copy:

| Variable | Default |
|----------|---------|
| `PFA_HOME` | the project folder (or the executable's folder) |
| `PFA_DB` | the profile's `data/pfa.sqlite` |
| `PFA_SETTINGS` | the profile's `settings.json` |
| `PFA_RATES` | `rates.csv` |
| `PFA_CAEN` | `caen_codes.xlsx` |

Setting `PFA_DB` or `PFA_SETTINGS` switches profiles off, and the app then uses just those files.
The tests do this. With only `PFA_DB` set, the settings are `settings.json` in the home folder;
if there is none yet, the pages say "settings file not found" until you save the Settings form
once, which makes it (see [`settings.json`](#settingsjson--firm-details-and-tax-values-per-year)).

Example: `PFA_DB=/tmp/test.sqlite python3 -m uvicorn app:app --port 8766`.

### How the code is organised

All the work is done in `tracker/services.py` and the modules under it: checking and storing
invoices and expenses, proofs, the tax calculation, the settings, backups. The browser front end
(`tracker/web.py` with `templates/`) and the desktop front end (`tracker/desktop/`) only show
things and pass on what you enter. Both call the same functions and get the same results and
messages, so they cannot behave differently. A test checks that neither front end goes around
the shared core.

## Profiles and themes

### Profiles

A profile is one firm with everything that belongs to it: its settings (firm details, salaried
job, tax values per year), its invoices and expenses, its proofs and its backups. Profiles never
see each other's data. The exchange rates file, the CAEN list and the themes are shared by all
profiles.

- **Switching:** only in *Settings → Profiles*: the *Switch* button of a profile (desktop: select
  it and click *Switch to this profile*, or double-click it). The header of every page just shows
  which profile you are in. Each browser and each desktop window keeps its own choice, so two
  windows can show two different profiles. Without a choice, the last profile used is shown.
- **Creating, renaming, deleting:** *Settings → Profiles*, at the top of the Settings page, so
  you can see and add profiles while filling in the firm. A new profile starts with an empty
  firm and the tax values of the profile in use, without salaried-job marks.
- **Deleting:** a confirmation pop-up names the profile and how many invoices and expenses it
  holds. A backup of the profile is then saved in the `deleted-profiles/` folder before it is
  removed. The last profile can't be deleted.
- **One firm per profile:** a firm's CUI can belong to only one profile. Saving a CUI that
  another profile already uses is refused and names that profile.
- **Backups** are per profile: *Download a backup* saves the profile in use, and *Restore* puts
  a backup into the profile in use, without touching the other profiles.
- **Where:** `profiles.json` lists the profiles, and each one lives in `profiles/<id>/` with its
  `settings.json` and `data/` (`pfa.sqlite`, `attachments/`, `backups/`). If
  `profiles.json` is lost or damaged, it is rebuilt from the folders. The first time the new
  version started, the existing settings and data became the first profile.

### Themes

*Settings → Theme* offers **Light**, **Dark** and **Custom**.

- **Each app has its own theme.** The browser version and the desktop app are set separately and
  never change each other. The theme is also the same whichever profile you are in.
- **Custom:** the colour choices (accent, background, cards and boxes, text) appear only when
  Custom is selected. The other shades (borders, muted text, table headers, message boxes) are
  worked out from them. Colours that would make text hard to read are refused with an
  explanation.
- **Saving a custom theme:** type a name and click *Save as a theme* (desktop: *Save as…*).
  - Saved themes are listed under the Custom choice with *Use* and *delete*.
  - Saving again under the same name updates that theme. Up to 30 can be saved.
  - The saved themes are offered in both apps, but using one in an app doesn't change the
    other app.
- **Where:** `ui-settings.json` in the home folder keeps each app's theme and the saved themes.

## Tests

```sh
cd ~/pfa-tracker
python3 -m pytest tests -q          # quick run
python3 tests/make_report.py        # run everything and write test-report.html
```

The tests run against scratch files in a temporary folder, never against your data. Expected
result: all tests pass and the ones in `test_intended_limits.py` are reported as `xfailed`
(expected failures). `test-report.html` lists every test with its result, a summary by area and
the list of intended limits.

| File | What it covers |
|------|----------------|
| `tests/conftest.py` | the mock-up data every test starts from: round tax values (minimum wage 4.000), fixed rates (EUR 5, USD 4, one odd Friday for the weekend rule), a small CAEN list, an empty database |
| `tests/test_parsing.py` | dates and amounts in every accepted form, and absurd input that must be refused (future dates, `NaN`, `1e5`, emoji, SQL, non-Latin digits…) |
| `tests/test_rates.py` | both rates layouts, the weekend fallback, the 10-day limit, rounding, broken and missing files |
| `tests/test_taxes.py` | CAS and CASS at every band edge to the cent, salaried job, settings fallback, CAEN exclusion windows, damaged settings, absurd values, the CUI check |
| `tests/test_import.py` | CSV layouts and Romanian headers, duplicates, payment dates, every kind of bad row, binary junk, 500 columns, 10.000 rows, upgrading an old database |
| `tests/test_web.py` | every tab; the year picker, the cookie and the fixed addresses (made-up years, tampered cookies, anything added to an address); invoices by hand and the payment-date rule; expenses; proofs (every type accepted when genuine, renamed/damaged/empty files refused, separate folders, unsafe names, 20 MB, path tricks); settings |
| `tests/test_firm_and_proofs.py` | the Settings fields (Trade Register number, phone, email, IBAN, address, secondary CAEN codes) with valid and invalid values; the salaried-job checkbox and earlier years within the PFA's lifetime; invoice proof names; the same proof checks on both tabs using a real PDF (`tests/fixtures/invoice.pdf`) and damaged copies of it |
| `tests/test_settings_edges.py` | earlier-year salaried marks survive a Trade Register typo and are never applied without being visible; a missing `settings.json` (and its folder) is made by the first save, a damaged one is never replaced |
| `tests/test_persistence_and_backup.py` | data and settings survive a restart; the backup zip's contents; restore on an empty tracker gives the same data and proofs; the safety copy; the confirmation box; damaged, foreign, path-tricking and oversized backups are refused and change nothing |
| `tests/test_backup_edges.py` | backups whose database the tracker couldn't read, two restores in one minute, duplicate zip entries, odd manifests, proofs that are not real files, a restore that fails halfway and is rolled back |
| `tests/test_damaged_images.py` | cut-short and garbage images, and images too large to decode safely |
| `tests/test_desktop.py` | the desktop app driven through its own widgets, without a screen: the tabs, the year picker, adding/deleting invoices and expenses with proofs, CSV import, settings, backup and restore; and that it shares the browser version's files and gives the same numbers and messages |
| `tests/test_profiles_and_theme.py` | profiles: the first profile made from existing data, creating/renaming/deleting (with backup and confirmation), each profile seeing only its own data and proofs, one firm per profile, two browsers on two profiles, tampered profile cookies, a lost profile list rebuilt; themes: light/dark/custom per profile, unreadable or malformed colours refused, broken theme settings falling back |
| `tests/test_desktop_profiles_theme.py` | the desktop app's theme (light/dark/custom restyles the whole window, unreadable colours refused, no fixed colours left in the code) and profiles (switcher, create/rename/delete, each profile's own data, the window keeping its profile when the browser switches) |
| `tests/test_edit_and_buttons.py` | editing invoices and expenses in the browser (re-conversion, moving years, duplicates, proofs kept/renamed/replaced/removed, wrong edits refused), the row buttons, amounts written differently recognised as duplicates |
| `tests/test_desktop_parity.py` | details where the desktop app must match the browser version: error colours, escaped text, CAEN exclusion details, typed settings kept, messages shown once |
| `tests/test_architecture.py` | both front ends go only through the shared core |
| `tests/test_invoicing.py` | invoicing in the core: every client, product, series and VAT field with valid and invalid values (CUI and CNP check digits, foreign VAT numbers, duplicates, emoji, prices and quantities), numbering per series and in date order, numbers never reused, issuing into Income (amount without VAT, year and rate of the payment date), the PDF's contents, all-or-nothing on failures, a form sent twice, six invoices issued at once, backups and profiles |
| `tests/test_web_invoicing.py` | the Invoicing tab of the browser version: every page and form, empty states, issuing and the PDF download, live totals, refused input kept in the form, tampered ids |
| `tests/test_desktop_invoicing.py` | the Invoicing tab of the desktop app driven through its widgets: lists, lines, live totals, issuing, opening and saving PDFs, deleted entries while chosen, profiles |
| `tests/test_credit_notes.py` | credit notes in the core: crediting all or some lines and quantities but never more than is left, pieces adding up to the invoice's value and VAT to the ban, the invoice's VAT and exchange rate, the negative Income row (year and rate of the refund date or its own date) and the year's taxes, shared numbering and date order, refusals that change nothing (deleted from Income, a credit note, tampered ids, damaged records), all or nothing, a form sent twice, two at the same moment, old databases, backups; the PDF (title, reversed invoice, negative amounts, no due date or payment line, reason, rate) and invoices printing exactly as before (`tests/fixtures/invoice_pdf_text.json`) |
| `tests/test_web_credit_notes.py` | credit notes in the browser version: the Credit note button and the Credit column of Issued invoices, the form with what is left per line, live totals, Credit all and Update totals without JavaScript, refused input kept, issuing with the PDF and a way to Income, a form sent twice, tampered ids; in Income the warning before deleting a credited invoice and the amount that stays negative |
| `tests/test_desktop_credit_notes.py` | the same for the desktop app driven through its widgets, with the same messages, plus what the browser version credits meanwhile, a double click, the reason's length and switching profiles |
| `tests/test_packaging.py` | where the files are looked for from source and from the executable, first-start copies that never overwrite, and the launcher's options |
| `tests/files.py` | real sample files (PDF, PNG, JPEG, GIF, WebP, XML) made on the fly for the proof checks |
| `tests/test_edges.py` | rough edges found by attacking the app: tampered hidden form fields, ids beyond what SQLite can hold, dates before 2000, zero-byte proofs, non-ASCII year keys |
| `tests/test_intended_limits.py` | **tests that fail on purpose.** Each one describes something the tracker deliberately does not do, written as if it did — US date order, advance invoices, editing in place, CAS opt-in… They are marked `xfail(strict=True)`, so the suite reports them as expected failures and would complain if one of them ever started passing. Run `python3 -m pytest tests/test_intended_limits.py -rx` to read the list with the reasons. |

## Your data, backups and moving to another computer

Everything you enter is saved on this computer the moment you save it, and is there again the
next time the app starts:

- each profile's settings (firm details, salaried job, tax values, theme):
  `profiles/<id>/settings.json`
- its invoices and expenses: `profiles/<id>/data/pfa.sqlite`
- its proofs: `profiles/<id>/data/attachments/`
- exchange rates and the CAEN list, shared by all profiles: `rates.csv` and `caen_codes.xlsx`

A profile's files belong together as one set: the invoices and expenses are those of the firm in
its `settings.json`. A backup carries one profile, the one in use.

**Download a backup:** on the Settings tab, *Download a backup* gives you one zip file named like
`pfa-backup-RO12345678-2026-11-02-1030.zip`. It holds the settings, all invoices and expenses,
the proofs, the rates file, the CAEN list and a small `manifest.json` that says which firm and
when. Keep it somewhere safe (a USB stick, another disk).

**Moving to another computer:** install the tracker there (copy the `pfa-tracker` folder and run
`sh run.sh`), open its Settings tab, choose the backup file under *Restore a backup*, tick the
confirmation box and click *Restore*. The other computer then has exactly the same settings,
invoices, expenses and proofs. Restoring replaces everything on that computer; a copy of what was
there is saved first in `data/backups/before-restore-….zip`, which can itself be restored.

A backup is checked before anything is replaced: it must be a zip made by the tracker, hold a
sound database and a valid `settings.json`, contain nothing but the expected files, and not be
larger than 1 GB unpacked. A damaged or foreign file is refused and nothing changes.

You can also simply copy the whole `pfa-tracker` folder; the backup file is the safer way
because it is checked on the way in.
