#!/usr/bin/env python3
"""SailRatings — Playwright visual & functional tests."""

import time
from playwright.sync_api import sync_playwright

BASE = "http://localhost:3002"


def test(name, fn):
    try:
        fn()
        print(f"  PASS: {name}")
    except Exception as e:
        print(f"  FAIL: {name}")
        print(f"    {e}")


def main():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(viewport={"width": 1440, "height": 900})

        print("\n=== SailRatings Visual & Functional Tests ===\n")

        # ─── Homepage Tests ───────────────────────────────────
        print("Homepage:")
        page = context.new_page()
        page.goto(BASE, wait_until="networkidle")

        def test_title():
            title = page.title()
            assert "Sail Ratings" in title, f'Title: "{title}"'
        test("Page title contains 'Sail Ratings'", test_title)

        def test_wordmark():
            wm = page.locator("nav .brand-wordmark")
            wm.wait_for(timeout=5000)
            text = wm.text_content()
            assert text and "Sail Ratings" in text, f'Wordmark: "{text}"'
        test("Brand wordmark visible in nav", test_wordmark)

        def test_hero_height():
            h = page.locator("section").first.evaluate(
                "el => el.getBoundingClientRect().height"
            )
            assert h >= 580, f"Hero height: {h}px"
        test("Hero is full viewport height", test_hero_height)

        def test_hero_italic():
            style = page.locator("h1").first.evaluate(
                "el => window.getComputedStyle(el).fontStyle"
            )
            assert style == "italic", f"Font style: {style}"
        test("Hero headline is italic", test_hero_italic)

        def test_trust_bar():
            assert page.locator("text=IRC analysis since 2024").count() > 0
        test("Trust bar visible", test_trust_bar)

        def test_search_placeholder():
            ph = page.locator('input[type="text"]').get_attribute("placeholder")
            assert ph and "boat name or sail number" in ph, f'Placeholder: "{ph}"'
        test("Search bar placeholder correct", test_search_placeholder)

        def test_hint():
            assert page.locator("text=Chilli Pepper").count() > 0
        test("Search hint text visible", test_hint)

        def test_border_color():
            color = page.locator(".page-border").evaluate(
                "el => window.getComputedStyle(el, '::before').borderTopColor"
            )
            # Signal blue #1B7FA3 -> rgb(27, 127, 163)
            assert "27" in color and "127" in color, f"Border color: {color}"
        test("Page border brackets use signal blue", test_border_color)

        def test_bg_sand():
            bg = page.evaluate(
                "() => window.getComputedStyle(document.querySelector('main')).backgroundColor"
            )
            # #F7F3ED = rgb(247, 243, 237)
            assert "247" in bg, f"Background: {bg}"
        test("Background is warm sand", test_bg_sand)

        def test_footer_wordmark():
            assert page.locator("footer .brand-wordmark").count() > 0
        test("Footer wordmark present", test_footer_wordmark)

        page.screenshot(path="/tmp/sailratings-home.png", full_page=True)
        print("  Screenshot: /tmp/sailratings-home.png")

        # ─── Search Flow ──────────────────────────────────────
        print("\nSearch Flow:")

        def test_search():
            inp = page.locator('input[type="text"]')
            inp.fill("Chilli Pepper")
            time.sleep(1.5)
            count = page.locator("#search-results li").count()
            assert count > 0, "No search results"
        test("Search returns results", test_search)

        def test_select_boat():
            page.locator("#search-results li").first.click()
            time.sleep(2)
            h2 = page.locator("h2:has-text('CHILLI PEPPER')")
            h2.wait_for(timeout=10000)
            assert "CHILLI PEPPER" in (h2.text_content() or "")
        test("Select boat loads card", test_select_boat)

        def test_tcc_color():
            tcc = page.locator(".text-signal.data-mono").first
            tcc.wait_for(timeout=5000)
            color = tcc.evaluate("el => window.getComputedStyle(el).color")
            # #1B7FA3 -> rgb(27, 127, 163)
            assert "27" in color, f"TCC color: {color}"
        test("TCC in signal blue", test_tcc_color)

        def test_teaser_streams():
            time.sleep(6)
            el = page.locator(".body-text.text-ink-light.whitespace-pre-wrap").first
            text = el.text_content()
            assert text and len(text) > 10, f"Too short: {len(text or '')} chars"
        test("Teaser analysis streams", test_teaser_streams)

        # Wait for teaser to finish
        print("  (waiting for teaser to complete...)")
        time.sleep(10)

        def test_teaser_footer():
            assert page.locator("text=seeing the surface").count() > 0
        test("Teaser footer new copy", test_teaser_footer)

        def test_divider():
            assert page.locator(".section-divider .diamond").count() > 0
        test("Section divider with diamond", test_divider)

        def test_cta_headline():
            assert page.locator("text=Unlock the Full Analysis").count() > 0
        test("CTA headline correct", test_cta_headline)

        def test_cta_button_color():
            btn = page.locator("button:has-text('Get the Full Report')")
            btn.wait_for(timeout=5000)
            bg = btn.evaluate("el => window.getComputedStyle(el).backgroundColor")
            # Copper #C27B3E -> rgb(194, 123, 62)
            assert "194" in bg, f"Button bg: {bg}"
        test("CTA button is copper", test_cta_button_color)

        def test_price():
            els = page.locator(".heading-serif.text-4xl")
            text = els.last.text_content()
            assert text and any(c in text for c in "$£€"), f'Price: "{text}"'
        test("Localised price displayed", test_price)

        def test_bullets():
            assert page.locator("text=biggest gains first").count() > 0
        test("Updated feature bullets", test_bullets)

        page.screenshot(path="/tmp/sailratings-flow.png", full_page=True)
        print("  Screenshot: /tmp/sailratings-flow.png")

        # ─── Report Page ──────────────────────────────────────
        print("\nReport Page:")

        def test_report_page():
            rp = context.new_page()
            rp.goto(f"{BASE}/report/test-invalid-token", wait_until="networkidle")
            time.sleep(5)
            body = rp.text_content("body")
            ok = any(
                s in (body or "")
                for s in ["Preparing", "Something went wrong", "Sail Ratings"]
            )
            assert ok, "No expected content on report page"
            rp.screenshot(path="/tmp/sailratings-report.png")
            print("  Screenshot: /tmp/sailratings-report.png")
            rp.close()
        test("Report page handles invalid token", test_report_page)

        # ─── Admin Page ───────────────────────────────────────
        print("\nAdmin Page:")

        def test_admin():
            ap = context.new_page()
            ap.goto(f"{BASE}/justin", wait_until="networkidle")
            ap.locator("text=Data Admin").wait_for(timeout=5000)
            assert ap.locator("text=sailratings.com").count() > 0, "Old domain still shown"
            ap.screenshot(path="/tmp/sailratings-admin.png")
            print("  Screenshot: /tmp/sailratings-admin.png")
            ap.close()
        test("Admin page updated domain", test_admin)

        # ─── Templates ────────────────────────────────────────
        print("\nTemplates:")

        def test_pdf_template():
            tp = context.new_page()
            tp.goto("file:///home/irc-data/code/irc-frontend/templates/report.html")
            body = tp.text_content("body")
            assert body and "Sail Ratings" in body
            tp.screenshot(path="/tmp/sailratings-pdf-template.png", full_page=True)
            print("  Screenshot: /tmp/sailratings-pdf-template.png")
            tp.close()
        test("PDF template renders", test_pdf_template)

        def test_email_template():
            ep = context.new_page()
            ep.goto("file:///home/irc-data/code/irc-frontend/templates/email_report.html")
            body = ep.text_content("body")
            assert body and "SAIL RATINGS" in body
            ep.screenshot(path="/tmp/sailratings-email-template.png", full_page=True)
            print("  Screenshot: /tmp/sailratings-email-template.png")
            ep.close()
        test("Email template renders", test_email_template)

        # ─── Done ─────────────────────────────────────────────
        page.close()
        browser.close()

        print("\n=== Tests Complete ===")
        print("Screenshots: /tmp/sailratings-*.png\n")


if __name__ == "__main__":
    main()
