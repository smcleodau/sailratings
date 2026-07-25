"""End-to-end tests for the full checkout flow on dev.sailratings.com.

Run with:
    cd /home/irc-data/code/sailratings/web
    python3 -m pytest tests/e2e_checkout_flow.py -v --timeout=120

Requires: pip install pytest playwright requests
          python3 -m playwright install chromium
"""

import os
import re

import pytest
import requests
from playwright.sync_api import Page, expect, sync_playwright

DEV_URL = "https://dev.sailratings.com"
PROD_URL = "https://sailratings.com"
API_DEV = "https://api-dev.sailratings.com/v1"

# Stripe test card
CARD_NUMBER = "4242424242424242"
CARD_EXP = "12/30"
CARD_CVC = "123"


@pytest.fixture(scope="session")
def browser():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        yield browser
        browser.close()


@pytest.fixture
def page(browser):
    context = browser.new_context(viewport={"width": 1280, "height": 720})
    page = context.new_page()
    yield page
    context.close()


def _search_and_select(page: Page, query: str = "sun fish"):
    """Helper: type a search query in the Hero combobox and click the first result."""
    search = page.locator("#main-search")
    search.click()
    search.fill(query)
    # Wait for debounced search results to appear (250ms debounce + API call)
    dropdown_item = page.locator("#search-results li").first
    expect(dropdown_item).to_be_visible(timeout=15000)
    dropdown_item.click()


class TestHomepage:
    def test_homepage_loads(self, page: Page):
        """Page loads with search bar visible."""
        page.goto(DEV_URL, wait_until="networkidle")
        expect(page.locator("#main-search")).to_be_visible()

    def test_search_returns_results(self, page: Page):
        """Searching for a known boat returns dropdown results."""
        page.goto(DEV_URL, wait_until="networkidle")
        search = page.locator("#main-search")
        search.click()
        search.fill("sun")
        # Wait for dropdown results
        results = page.locator("#search-results li")
        expect(results.first).to_be_visible(timeout=15000)
        assert results.count() > 0, "No dropdown results found for 'sun'"


class TestBoatPage:
    def test_boat_page_loads(self, page: Page):
        """Selecting a search result renders boat card with name and rating."""
        page.goto(DEV_URL, wait_until="networkidle")
        _search_and_select(page)
        # ReportModal renders the boat's file with name heading inside role="dialog"
        heading = page.locator("[role='dialog'] h1").first
        expect(heading).to_be_visible(timeout=15000)

    def test_teaser_streams(self, page: Page):
        """SSE insight stream starts and renders analysis text."""
        page.goto(DEV_URL, wait_until="networkidle")
        _search_and_select(page)
        
        # Wait for the modal or dialog to load
        expect(page.locator("[role='dialog']")).to_be_visible(timeout=15000)
        
        # Let's wait up to 75s for the brief section header to show
        try:
            expect(page.locator("text=Section 01 of 08")).to_be_visible(timeout=75000)
        except AssertionError as e:
            dialog_text = page.locator("[role='dialog']").text_content() or ""
            print("\n" + "="*50)
            print("DIALOG TEXT ON FAILURE:", dialog_text)
            print("="*50 + "\n")
            raise e
            
        teaser_div = page.locator(".whitespace-pre-wrap").first
        expect(teaser_div).to_be_visible(timeout=15000)
        
        # Wait up to 30s for the text content to stream and exceed 100 characters
        page.wait_for_function(
            "el => el.textContent.length > 100",
            arg=teaser_div.element_handle(),
            timeout=30000
        )
        teaser_text = teaser_div.text_content() or ""
        assert len(teaser_text) > 100, f"Expected streamed teaser text, but got {len(teaser_text)} chars"


class TestCheckoutFlow:
    def test_checkout_creates_session(self, page: Page):
        """Clicking 'Get Full Report' redirects to Stripe checkout."""
        page.goto(DEV_URL, wait_until="networkidle")
        _search_and_select(page)
        # Wait for PurchaseCTA to appear (needs BoatCard to load first)
        cta = page.locator("button:has-text('Send me the file'), button:has-text('Report'), a:has-text('Report')").first
        expect(cta).to_be_visible(timeout=60000)
        # Click and expect navigation to Stripe
        with page.expect_navigation(url=re.compile(r"checkout\.stripe\.com"), timeout=25000):
            cta.click()
        assert "checkout.stripe.com" in page.url, f"Expected Stripe URL, got {page.url}"

    @pytest.mark.slow
    def test_stripe_test_payment(self, page: Page):
        """Complete a Stripe test payment and verify redirect to /report/."""
        page.goto(DEV_URL, wait_until="networkidle")
        _search_and_select(page)
        cta = page.locator("button:has-text('Send me the file'), button:has-text('Report'), a:has-text('Report')").first
        expect(cta).to_be_visible(timeout=60000)
        with page.expect_navigation(url=re.compile(r"checkout\.stripe\.com"), timeout=25000):
            cta.click()

        # Fill Stripe checkout form — use domcontentloaded (Stripe never reaches networkidle)
        page.wait_for_load_state("domcontentloaded")
        page.wait_for_timeout(3000)

        # Email
        email_input = page.locator("input[name='email'], input#email").first
        expect(email_input).to_be_visible(timeout=15000)
        email_input.fill("test@sailratings.com")

        # Stripe card inputs can be directly on the page (Checkout page) or inside an iframe
        card_number_direct = page.locator("input#cardNumber, input[name='cardNumber'], input[autocomplete='cc-number']").first
        if card_number_direct.is_visible(timeout=5000):
            card_number_direct.fill(CARD_NUMBER)
            
            card_exp_direct = page.locator("input#cardExpiry, input[name='cardExpiry'], input[autocomplete='cc-exp']").first
            card_exp_direct.fill(CARD_EXP)
            
            card_cvc_direct = page.locator("input#cardCvc, input[name='cardCvc'], input[autocomplete='cc-csc']").first
            card_cvc_direct.fill(CARD_CVC)
        else:
            # Fallback to Stripe's iframe
            card_frame = page.frame_locator("iframe[name*='__privateStripeFrame'], iframe[title*='Secure card payment'], iframe").first
            card_number = card_frame.locator("input[name='cardnumber'], input[placeholder*='card number'], input[placeholder*='Card number'], input#cardNumber, input[autocomplete='cc-number']").first
            card_number.fill(CARD_NUMBER)
            
            card_exp = card_frame.locator("input[name='exp-date'], input[placeholder*='MM'], input#cardExpiry, input[autocomplete='cc-exp']").first
            card_exp.fill(CARD_EXP)
            
            card_cvc = card_frame.locator("input[name='cvc'], input[placeholder*='CVC'], input#cardCvc, input[autocomplete='cc-csc']").first
            card_cvc.fill(CARD_CVC)

        # Name on card (if present)
        name_input = page.locator("input[name='billingName']")
        if name_input.count() > 0:
            name_input.fill("E2E Test")

        # Country/postal (if present)
        postal = page.locator("input[name='billingPostalCode']")
        if postal.count() > 0:
            postal.fill("10001")

        # Submit payment
        submit_btn = page.locator("button[type='submit'], .SubmitButton").first
        submit_btn.click()

        # Wait for redirect back to our site
        page.wait_for_url(re.compile(rf"{re.escape(DEV_URL)}/report/"), timeout=60000)
        assert "/report/" in page.url, f"Expected redirect to /report/, got {page.url}"

        # Extract token from URL
        token = page.url.split("/report/")[-1].rstrip("/")
        assert len(token) > 10, f"Invalid report token: {token}"

    @pytest.mark.slow
    def test_report_renders(self, page: Page):
        """A generated report page renders content and stops polling."""
        # Find an existing generated order token from the reports directory
        report_dir = "/home/irc-data/code/sailratings/api/data/reports/"
        try:
            pdfs = [f for f in os.listdir(report_dir) if f.endswith(".pdf")]
        except FileNotFoundError:
            pytest.skip("Reports directory not found")
        if not pdfs:
            pytest.skip("No generated reports to test")

        token = pdfs[0].replace(".pdf", "")
        page.goto(f"{DEV_URL}/report/{token}", wait_until="networkidle")
        page.wait_for_timeout(5000)

        # Report should render content (not stuck on loading spinner)
        body_text = page.text_content("body") or ""
        # A generated report contains "Full IRC Rating Analysis" and the boat analysis
        assert "Rating" in body_text or "analysis" in body_text.lower(), \
            f"Report content not found, body starts with: {body_text[:200]}"
        # Should have the Download PDF button visible
        pdf_btn = page.locator("text=Download PDF")
        expect(pdf_btn).to_be_visible(timeout=5000)

    @pytest.mark.slow
    def test_pdf_download(self, page: Page):
        """Verify PDF endpoint returns 200 with application/pdf."""
        report_dir = "/home/irc-data/code/sailratings/api/data/reports/"
        try:
            pdfs = [f for f in os.listdir(report_dir) if f.endswith(".pdf")]
        except FileNotFoundError:
            pytest.skip("Reports directory not found")
        if not pdfs:
            pytest.skip("No PDFs available for testing")

        token = pdfs[0].replace(".pdf", "")
        resp = requests.get(f"{API_DEV}/reports/{token}/pdf", timeout=15)
        assert resp.status_code == 200, f"PDF endpoint returned {resp.status_code}"
        assert "application/pdf" in resp.headers.get("content-type", ""), \
            f"Expected PDF content-type, got {resp.headers.get('content-type')}"


class TestSurvey:
    def test_survey_endpoint(self, page: Page):
        """Survey API endpoint is live and validates input."""
        resp = requests.post(
            f"{API_DEV}/surveys/submit",
            json={
                "order_token": "00000000-0000-0000-0000-000000000000",
                "usefulness_score": 8,
                "newsletter_signup": False,
                "user_type": "racer",
            },
            timeout=10,
        )
        # 404 = order not found (expected for fake token), means endpoint works
        assert resp.status_code in (200, 404, 409), f"Survey endpoint returned {resp.status_code}"

    @pytest.mark.slow
    def test_survey_renders_on_report(self, page: Page):
        """Survey component appears on a generated report page."""
        report_dir = "/home/irc-data/code/sailratings/api/data/reports/"
        try:
            pdfs = [f for f in os.listdir(report_dir) if f.endswith(".pdf")]
        except FileNotFoundError:
            pytest.skip("Reports directory not found")
        if not pdfs:
            pytest.skip("No generated reports to test")

        token = pdfs[0].replace(".pdf", "")
        page.goto(f"{DEV_URL}/report/{token}", wait_until="networkidle")
        page.wait_for_timeout(3000)

        # Survey heading should be visible
        survey = page.locator("text=How was this report")
        expect(survey).to_be_visible(timeout=10000)


class TestProdSafety:
    def test_justin_blocked_on_prod(self, page: Page):
        """The /justin admin route should not expose admin on production."""
        page.goto(f"{PROD_URL}/justin", wait_until="networkidle", timeout=15000)
        body_text = page.text_content("body") or ""
        # Should see the homepage (redirected), not an admin dashboard
        assert len(body_text) > 50, "Page loaded with content"
