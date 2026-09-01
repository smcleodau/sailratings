# IRC Certificate PDF Source — Recon Findings

**Date:** 2026-09-01  
**Task:** DP-00-05  
**Policy:** interim-v0  
**Researcher:** builder-agent

---

## Source Overview

**URL:** https://ircrating.org/boat-data-for-valid-irc-certificates/  
**Description:** The IRC Rating Organisation's public search widget for valid IRC
certificate PDFs. Allows users to search by certificate number, boat name, or
sail number and download a PDF of the boat's measurement data and drawing.

---

## How the Widget Works

### Search Mechanism

The page contains a plain HTML form with `method="POST"` and `action=""` (posts
to itself):

```html
<form method="post" action="">
    <input type="text" id="pdf-search" name="pdf_search" placeholder="...">
    <input class="button" type="submit" value="Search">
</form>
<div id="pdf-results">
    <!-- results inserted here -->
</div>
```

**No JavaScript is required** for the search or the download. The form posts
server-side, results are rendered in the HTML response, and the download link
is a standard `<a href>` pointing to a signed URL.

### Search Parameters

- **Method:** `POST`
- **URL:** `https://ircrating.org/boat-data-for-valid-irc-certificates/`
- **Content-Type:** `application/x-www-form-urlencoded`
- **Field name:** `pdf_search`
- **Field value:** certificate number, boat name, or sail number (partial
  matches supported — e.g. `AUS521` returns multiple boats)

### Response Format

The response is a full HTML page. The results are inside:

```html
<div id="pdf-results">
    <p>14163_KOA_AUS52152.pdf
       <a href="https://ircrating.org/?irc_dl=14163_KOA_AUS52152.pdf&#038;tk=..." 
          rel="nofollow noopener">Download</a>
    </p>
</div>
```

For no results:
```html
<div id="pdf-results">
    <p>No files found.</p>
</div>
```

### PDF Download URL Structure

The download URL format is:

```
https://ircrating.org/?irc_dl={filename}&tk={timestamp}.{hmac_hash}
```

- `irc_dl` — the filename: `{cert_no}_{boat_name}_{sail_no}.pdf`
- `tk` — a signed token: `{unix_timestamp}.{sha256_hmac}` (server-generated)

The token is **time-based but long-lived** (confirmed still valid after ~2
minutes; likely expires in hours or days). The direct `/pdfdirectory/` path
returns HTTP 403 — PDFs must be downloaded through the signed token URL.

**Tested fetch (cert 14163, KOA, AUS52152):**
- POST with `pdf_search=14163` → HTML with download link
- GET of signed URL → `application/pdf`, 200 OK, 72,972 bytes, starts `%PDF`
- No cookies required, no JavaScript required

### robots.txt

```
User-agent: *
Disallow:
```

**All paths are allowed.** The `Disallow:` line is empty, meaning no paths are
disallowed for any user-agent.

---

## Plain HTTP vs JavaScript

**Plain HTTP is sufficient.** Both the search (POST) and the download (GET of
signed URL) work with a standard HTTP client. The page uses JavaScript for
analytics and spam-protection widgets (CleanTalk bot detector) but these do
not gate the form submission or file download.

The CleanTalk bot detector script injects some form-validation logic, but
empirical testing confirms the form POST works without JavaScript execution —
the WordPress plugin's server-side handler processes the `pdf_search` field
directly in PHP.

**No Playwright/browser automation is needed for this source.**

---

## Enumeration Strategy

To enumerate all certificate PDFs, we use cert numbers already known to the
platform (from `boats.cert_number`, `irc_certificates.cert_number`):

1. Query the DB for all distinct `cert_number` values from `boats` and
   `irc_certificates` (approx. 6,500–9,500 certs).
2. For each cert number, POST to the search endpoint.
3. Parse the `#pdf-results` div for download links.
4. Extract the signed URL, download the PDF, and store as a `RawArtifactV1`.

Alternatively, the TCC listing CSVs (from `data/raw/tcc_listings/`) contain
cert numbers from historical snapshots — the `cert_index` module already
builds a consolidated list.

The search supports partial matches, so searching by cert number (exact
integer) gives reliable 1-to-1 or 1-to-many results (e.g. a boat may have
multiple cert versions: original + SEC endorsement).

---

## Filename Convention

PDF filenames follow this pattern:

```
{cert_no}_{boat_name}_{sail_no}.pdf
```

Examples:
- `14163_KOA_AUS52152.pdf`
- `48182_KOA - SEC_AUS52152.pdf`  (SEC = secondary endorsement)
- `50614_KARAKOA - SEC_PHI8088.pdf`

Some boat names contain spaces and special characters. The filename is
URL-encoded in the `irc_dl` query parameter.

---

## Rate Limits and Politeness

- No explicit rate-limit headers observed.
- Policy mandates: 1 request / 2s minimum delay (per `CollectionRules` defaults).
- Night window: 01:00–06:00 UK (UTC+1 BST / UTC in winter).
- Max 5,000 fetches per night.
- Each cert number requires 2 HTTP requests: 1 POST (search) + 1 GET (PDF).
  So ~2,500 certs per night at the cap.

---

## Data Freshness

IRC certificates are reissued when owners update measurements or when the
formula changes. A certificate with the same number may be reissued with
different content. The SHA-256 hash deduplication in `RawObjectStore` handles
this: same hash → no new artifact; different hash → new artifact stored.

Monthly re-check of all known cert numbers catches reissued certificates.

---

## Constraints

- **Download only published PDFs.** No "Copy Certificate" purchases.
- **Only `irc_dl` URLs.** Direct `/pdfdirectory/` access returns 403.
- **Store raw bytes only** — no parsing in this step (DP-00-05).
- Content is public — no authentication required.
