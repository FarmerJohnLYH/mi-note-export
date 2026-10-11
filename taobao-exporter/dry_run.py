# -*- coding: utf-8 -*-
"""Dry-run: launch headed browser (system Chrome via Playwright), open the
Taobao buyer order page (will show login), screenshot, close. No login, no data."""
import os
import sys
import time

from playwright.sync_api import sync_playwright

BASE = os.path.dirname(os.path.abspath(__file__))
PROFILE = os.path.join(BASE, "browser_profile")
SHOT = r"C:\Users\Administrator\Desktop\WorkDir\mi-note-export\dry_run_screenshot.png"
URL = "https://buyertrade.taobao.com/trade/itemlist/list_bought_items.htm"

os.makedirs(PROFILE, exist_ok=True)

with sync_playwright() as p:
    kwargs = dict(
        user_data_dir=PROFILE,
        headless=False,
        viewport={"width": 1280, "height": 900},
        args=["--disable-blink-features=AutomationControlled"],
    )
    try:
        ctx = p.chromium.launch_persistent_context(channel="chrome", **kwargs)
        browser = "system Google Chrome (channel=chrome)"
    except Exception as e:
        print(f"system Chrome failed: {e}; falling back to bundled Chromium", flush=True)
        ctx = p.chromium.launch_persistent_context(**kwargs)
        browser = "bundled Chromium"
    print(f"BROWSER={browser}", flush=True)

    page = ctx.pages[0] if ctx.pages else ctx.new_page()
    try:
        page.goto(URL, wait_until="domcontentloaded", timeout=60000)
    except Exception as e:
        print(f"navigation note: {e}", flush=True)
    time.sleep(6)  # let redirects / login page render
    try:
        page.wait_for_load_state("networkidle", timeout=15000)
    except Exception:
        pass
    print(f"TITLE={page.title()}", flush=True)
    print(f"URL_NOW={page.url}", flush=True)
    page.screenshot(path=SHOT, full_page=False)
    print(f"SCREENSHOT={SHOT}", flush=True)
    ctx.close()

print("DRY_RUN_OK", flush=True)
