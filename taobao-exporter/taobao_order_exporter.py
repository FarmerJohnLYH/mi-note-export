#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
taobao_order_exporter.py
========================

A LOCAL, MANUAL-LOGIN tool to export *your own* Taobao buyer order history
from the visible orders page into a CSV/XLSX file.

Design principles (please read before editing):
------------------------------------------------
* This tool NEVER bypasses CAPTCHA, sliders, anti-bot systems, login
  protections, or rate limits. If any verification / risk-control / login
  page is detected, the script STOPS and asks you to handle it manually.
* It does NOT reverse-engineer private APIs. It only reads what is already
  rendered on the page you navigated to yourself.
* It does NOT ask for or store passwords, cookies, tokens, or storage values.
  You log in manually in a real browser window.
* It uses a persistent browser profile (./browser_profile) so you stay
  logged in across runs.
* Default flow is fully manual: you navigate, you press Enter, the script
  reads the current page only. You move to the next page yourself.
* All data is saved locally only. Nothing is uploaded anywhere.

This is intended ONLY for exporting your own account's own order data.
"""

from __future__ import annotations

import argparse
import csv
import os
import random
import re
import sys
import time
import traceback
from dataclasses import dataclass, fields, asdict
from datetime import datetime
from typing import Iterable, Optional

# Playwright (sync API — easier to follow for a manual, step-by-step flow).
try:
    from playwright.sync_api import sync_playwright, Page, TimeoutError as PWTimeout
except ImportError:  # pragma: no cover - friendly message if dependency missing
    print(
        "ERROR: Playwright is not installed.\n"
        "Run:\n"
        "    pip install -r requirements.txt\n"
        "    playwright install chromium\n"
    )
    sys.exit(1)


# ---------------------------------------------------------------------------
# Configuration you may want to tweak
# ---------------------------------------------------------------------------

# Persistent profile directory: keeps you logged in across runs.
PROFILE_DIR = "browser_profile"

# Where debug snapshots go when --debug is on.
DEBUG_DIR = "debug_snapshots"

# The buyer order list page. You may still need to log in / navigate manually.
DEFAULT_START_URL = "https://buyertrade.taobao.com/trade/itemlist/list_bought_items.htm"

# Random human-like delay range (seconds) between non-trivial actions.
DELAY_MIN = 2.0
DELAY_MAX = 5.0

# -- Selector strategies -----------------------------------------------------
# Taobao's DOM changes often, so we keep MULTIPLE candidate selectors and try
# each in turn. Edit these freely if the layout changes. The first selector
# that returns elements "wins" for that field.
#
# These are intentionally broad / defensive. The text+regex fallback below
# handles cases where none of these match.

# Candidate selectors for the container of ONE REAL ORDER (a "card").
# IMPORTANT: these must select the order-level card, NOT the page header row and
# NOT inner item/price nodes — otherwise nested elements get parsed as separate
# partial "orders". The `trade-bought-list-order-container` class is the stable
# semantic class Taobao uses for one order; hashed classes (e.g. --Z1dh0l0O) are
# unstable and deliberately avoided here.
ORDER_CONTAINER_SELECTORS = [
    "div.trade-bought-list-order-container",   # current PC layout (verified)
    "div.trade-container-shopOrderContainer",  # sibling stable class
    "div.js-order-container",                  # older layout
    "tr.order-row",                            # table-based layout
    "div[class*='bought-list-order-container']",
]

# Within ONE order container, selectors for each ITEM sub-block. An order may
# contain several items; we emit one row per item and inherit order-level fields.
# If none match, the whole order container is treated as a single item block.
ITEM_BLOCK_SELECTORS = [
    "div.trade-bought-list-order-info",        # per-item info block (verified)
    "div[class*='itemInfo']",
    "div[class*='bought-list-order-info']",
]

# Within an order container, candidate selectors per field.
# Each field maps to a list of CSS selectors tried in order.
# ORDER-LEVEL selectors (resolved once per order container). order_id is NEVER
# taken from free text via these — it is validated by regex (see below).
FIELD_SELECTORS = {
    # Shop link: anchor pointing at a seller shop page.
    "shop_name": [
        "a[href*='.taobao.com/shop']",
        "a[href*='shop'][href*='taobao']",
        "a[href*='//shop']",
        "[class*='shopName'] a",
        "[class*='seller'] a",
    ],
    # Detail link carries bizOrderId — also our most reliable order_id source.
    "order_detail_url": [
        "a[href*='bizOrderId']",
        "a[href*='biz_order_id']",
        "a[href*='orderDetail']",
        "a[href*='trade_detail']",
    ],
    # ITEM-LEVEL selectors (resolved per item block).
    "item_title": [
        "a[href*='item.taobao.com']",
        "a[href*='detail.tmall.com']",
        "a[href*='item.htm']",
        "[class*='titleText']",
        "[class*='item-title']",
    ],
    "item_url": [
        "a[href*='item.taobao.com']",
        "a[href*='detail.tmall.com']",
        "a[href*='item.htm']",
    ],
    "item_sku": [
        "[class*='sku']",
        "[class*='spec']",
        "[class*='subInfo']",
        "[class*='promotion']",
    ],
    # quantity / prices are extracted by validated regex from block text, with
    # these selectors only as hints. See extraction logic.
    "quantity": [
        "[class*='quantity']",
        "[class*='count']",
    ],
    "order_status": [
        "[class*='statusText']",
        "[class*='status']",
        "[class*='state']",
    ],
}

# Candidate selectors for the "next page" control (used only in --auto-next).
# We try several and only ever click one that clearly says "下一页"/next AND is
# visible AND not disabled. On the current PC layout the control is a <div> with
# class `headerPageRight` (disabled state shown via a `...disabled` class), so
# class-based disabled detection — not just is_enabled() — is required.
NEXT_PAGE_SELECTORS = [
    "[class*='headerPageRight']",                      # current PC layout
    "button:has-text('下一页')",
    "a:has-text('下一页')",
    "div[class*='trade-button']:has-text('下一页')",
    "[aria-label*='下一页']",
    "[aria-label*='next' i]",
    "li[class*='next'] a",
    "[class*='pagination'] [class*='next']",
    "[class*='pager'] [class*='next']",
]

# Phrases that indicate a verification / risk-control / login page.
# If ANY of these is found in the page, we STOP and let the user handle it.
RISK_INDICATORS = [
    "验证",          # verification
    "滑块",          # slider
    "滑动",          # slide
    "拖动",          # drag
    "captcha",
    "亲，请登录",     # please log in
    "请登录",        # please log in
    "登录页面",       # login page
    "login",
    "安全验证",       # security verification
    "风险",          # risk
    "punish",        # taobao risk-control path keyword
    "_____tmd_____", # taobao anti-bot marker page
    "behavior",      # nocaptcha behavior page
    "二维码登录",     # qr login
    "扫码登录",       # scan to log in
]


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass
class OrderItem:
    """One exported order line. Sensitive fields are excluded by default."""
    order_date: str = ""
    order_time: str = ""
    order_id: str = ""
    shop_name: str = ""
    item_title: str = ""
    item_url: str = ""
    item_sku: str = ""
    quantity: str = ""
    unit_price: str = ""
    actual_paid: str = ""
    order_status: str = ""
    order_detail_url: str = ""
    page_number_collected: int = 0
    collected_at: str = ""
    # Sensitive (only filled when --include-sensitive AND visible on page).
    receiver_name: str = ""
    receiver_phone: str = ""
    receiver_address: str = ""

    def dedupe_key(self) -> tuple:
        """Dedup by order_id + item_title + actual_paid as requested."""
        return (self.order_id.strip(), self.item_title.strip(), self.actual_paid.strip())


# Non-sensitive field order used for normal export.
BASE_FIELDS = [
    "order_date", "order_time", "order_id", "shop_name", "item_title",
    "item_url", "item_sku", "quantity", "unit_price", "actual_paid",
    "order_status", "order_detail_url", "page_number_collected", "collected_at",
]
SENSITIVE_FIELDS = ["receiver_name", "receiver_phone", "receiver_address"]


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------

def human_delay(min_s: Optional[float] = None, max_s: Optional[float] = None,
                label: str = "") -> None:
    """Sleep a randomized, human-like interval. Keeps frequency low.

    Bounds default to the module DELAY_MIN/DELAY_MAX but callers (e.g. auto-next
    pacing) can pass explicit, larger bounds."""
    lo = DELAY_MIN if min_s is None else float(min_s)
    hi = DELAY_MAX if max_s is None else float(max_s)
    if hi < lo:
        hi = lo
    secs = random.uniform(lo, hi)
    if label:
        print(f"  (waiting {secs:.1f}s — {label})")
    time.sleep(secs)


def now_iso() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


# --- Regex patterns -------------------------------------------------------
# Order id: a long digit run that appears AFTER an explicit "订单号" label, or in
# a bizOrderId URL param. Length is validated separately (16-24 digits).
RE_ORDER_ID_LABELLED = re.compile(r"订单(?:号|编号)\s*[:：]?\s*(\d{16,24})")
RE_ORDER_ID_URL = re.compile(r"biz[_]?[Oo]rder[_]?[Ii]d=(\d{16,24})")
RE_DATE = re.compile(r"(\d{4})[-/.年](\d{1,2})[-/.月](\d{1,2})")
RE_TIME = re.compile(r"(\d{1,2}:\d{2}(?::\d{2})?)")
# A currency amount: optional symbol, 1-6 integer digits, optional 1-2 decimals.
# The {1,6} integer cap means a 16+ digit order id can NEVER match as a price.
RE_CURRENCY = re.compile(r"(?:￥|¥|RMB|CNY)?\s*(\d{1,6}(?:\.\d{1,2})?)")
# Stricter price pattern that REQUIRES a currency symbol — used for unit price so
# stray digits (e.g. part of an order id in fallback text) are never mistaken
# for a price.
RE_PRICE_SYMBOL = re.compile(r"(?:￥|¥|RMB|CNY)\s*(\d{1,6}(?:\.\d{1,2})?)")
# Actual-paid: an approved label immediately followed by an amount.
PAID_LABELS = ["实付款", "实付", "付款金额", "合计", "actual paid", "actual_paid"]
RE_PAID = re.compile(
    r"(?:实付款|实付|付款金额|合计|actual[ _]?paid)\s*[:：]?\s*"
    r"(?:￥|¥|RMB|CNY)?\s*(\d{1,6}(?:\.\d{1,2})?)",
    re.IGNORECASE,
)
# Quantity: x1 / ×1 / 数量: 1 / 共1件
RE_QTY = re.compile(r"(?:[x×]\s*(\d{1,4}))|(?:数量\s*[:：]?\s*(\d{1,4}))|(?:共\s*(\d{1,4})\s*件)",
                    re.IGNORECASE)

STATUS_KEYWORDS = [
    "交易成功", "交易关闭", "等待付款", "等待发货", "等待买家收货",
    "买家已付款", "卖家已发货", "退款成功", "退款中", "退款关闭", "待评价",
    "已发货", "已收货", "已完成", "未付款", "待收货", "待发货", "交易进行中",
]


# --- Field validators ------------------------------------------------------

def valid_order_id(value: str) -> bool:
    """A Taobao order id is purely digits, 16-24 long."""
    return bool(value) and value.isdigit() and 16 <= len(value) <= 24


def valid_amount(value: str) -> bool:
    """A normal currency amount: 0.01 .. 999999.99, and never 7+ integer digits
    (so 16+ digit order ids are rejected)."""
    if not value:
        return False
    if not re.fullmatch(r"\d{1,6}(?:\.\d{1,2})?", value):
        return False
    try:
        amount = float(value)
    except ValueError:
        return False
    return 0.01 <= amount <= 999999.99


def extract_order_id(text: str, detail_url: str = "") -> str:
    """Return a VALID order id or '' — never free-form card text."""
    if detail_url:
        m = RE_ORDER_ID_URL.search(detail_url)
        if m and valid_order_id(m.group(1)):
            return m.group(1)
    m = RE_ORDER_ID_LABELLED.search(text)
    if m and valid_order_id(m.group(1)):
        return m.group(1)
    return ""


def extract_actual_paid(text: str) -> str:
    """Return a VALID actual-paid amount from an approved label, else ''."""
    m = RE_PAID.search(text)
    if m and valid_amount(m.group(1)):
        return m.group(1)
    return ""


def extract_quantity(text: str) -> str:
    m = RE_QTY.search(text)
    if not m:
        return ""
    for g in m.groups():
        if g:
            return g
    return ""


def extract_unit_price(text: str) -> str:
    """
    Goods (unit) price. Requires a ￥/¥ symbol so stray digits (e.g. order-id
    fragments) are never treated as a price.

    The goods price renders just BEFORE the 实付款 label, while recommendation
    blocks ("常买常逛"/"猜你喜欢") render elsewhere. So we take the LAST valid
    symbol-price that appears BEFORE the paid label; that reliably skips
    recommendation prices that bracket the order in fallback text.
    """
    paid = RE_PAID.search(text)
    region = text[:paid.start()] if paid else text
    before = [m.group(1) for m in RE_PRICE_SYMBOL.finditer(region)
              if valid_amount(m.group(1))]
    if before:
        return before[-1]
    # No paid label or nothing before it: first valid symbol-price anywhere.
    for m in RE_PRICE_SYMBOL.finditer(text):
        if valid_amount(m.group(1)):
            return m.group(1)
    return ""


def parse_order_level(block_text: str, detail_url: str = "") -> dict:
    """
    Parse ORDER-LEVEL fields from an order card's visible text, using validated
    extractors only. Returns a dict with whatever could be confidently found.
    These fields are shared by every item row in the order.
    """
    out: dict = {}

    oid = extract_order_id(block_text, detail_url)
    if oid:
        out["order_id"] = oid

    md = RE_DATE.search(block_text)
    if md:
        y, mo, d = md.groups()
        out["order_date"] = f"{int(y):04d}-{int(mo):02d}-{int(d):02d}"
        mt = RE_TIME.search(block_text)
        if mt:
            out["order_time"] = mt.group(1)

    for kw in STATUS_KEYWORDS:
        if kw in block_text:
            out["order_status"] = kw
            break

    paid = extract_actual_paid(block_text)
    if paid:
        out["actual_paid"] = paid

    return out


def first_matching_text(scope, selectors: Iterable[str]) -> str:
    """Return the trimmed text of the first selector that matches in scope."""
    for sel in selectors:
        try:
            el = scope.query_selector(sel)
        except Exception:
            continue
        if el:
            try:
                txt = (el.inner_text() or "").strip()
            except Exception:
                txt = ""
            if txt:
                return txt
    return ""


def first_matching_attr(scope, selectors: Iterable[str], attr: str) -> str:
    """Return the attribute value of the first selector that matches in scope."""
    for sel in selectors:
        try:
            el = scope.query_selector(sel)
        except Exception:
            continue
        if el:
            try:
                val = el.get_attribute(attr)
            except Exception:
                val = None
            if val:
                return val.strip()
    return ""


# ---------------------------------------------------------------------------
# Risk / verification detection — we STOP, never bypass
# ---------------------------------------------------------------------------

def page_looks_blocked(page: Page) -> Optional[str]:
    """
    Return a human-readable reason string if the page looks like a login /
    verification / risk-control page, else None. We NEVER try to solve these.
    """
    url = (page.url or "").lower()
    for kw in RISK_INDICATORS:
        if kw.lower() in url:
            return f"URL contains '{kw}' (looks like a verification/login page)"

    try:
        body_text = (page.inner_text("body") or "")
    except Exception:
        body_text = ""

    low = body_text.lower()
    # Only flag short-ish pages or clear markers, to avoid false positives on
    # normal order pages that may casually mention "验证" somewhere.
    strong_markers = ["安全验证", "滑块", "请拖动", "captcha", "扫码登录", "二维码登录"]
    for kw in strong_markers:
        if kw.lower() in low:
            return f"Page text contains '{kw}' (verification/login required)"

    # If the page is very short AND mentions login, treat as login page.
    if len(body_text.strip()) < 400:
        for kw in ["请登录", "亲，请登录", "login"]:
            if kw.lower() in low:
                return f"Short page mentioning '{kw}' (login required)"

    return None


# ---------------------------------------------------------------------------
# Extraction
# ---------------------------------------------------------------------------

def find_order_containers(page: Page):
    """Return (containers, selector_used). A valid order container MUST contain
    an order-number label so we never pick up the page header or unrelated divs."""
    for sel in ORDER_CONTAINER_SELECTORS:
        try:
            found = page.query_selector_all(sel)
        except Exception:
            found = []
        good = []
        for el in found:
            try:
                txt = el.inner_text() or ""
            except Exception:
                txt = ""
            # Real order cards carry an order number; the header row does not.
            if "订单号" in txt or "订单编号" in txt or RE_ORDER_ID_URL.search(
                _safe_attr(el, "outerHTML") or ""
            ):
                good.append(el)
        if good:
            return good, sel
    return [], None


def _safe_attr(el, attr: str) -> str:
    try:
        return el.get_attribute(attr) or ""
    except Exception:
        return ""


def build_item_row(item_block, order_fields: dict, page_number: int,
                   collected_ts: str, include_sensitive: bool,
                   unit_price_fallback: str = "", quantity_fallback: str = "") -> Optional[OrderItem]:
    """Build one OrderItem for a single item block, inheriting order-level fields.

    Price/quantity may render in sibling columns outside the item-info block, so
    we fall back to the order-level value when the item block has none."""
    try:
        block_text = (item_block.inner_text() or "").strip()
    except Exception:
        block_text = ""

    item = OrderItem(page_number_collected=page_number, collected_at=collected_ts)

    # Inherit order-level fields (date/id/shop/paid/status/detail-url).
    for k, v in order_fields.items():
        setattr(item, k, v)

    # Item-level structured extraction.
    item.item_title = first_matching_text(item_block, FIELD_SELECTORS["item_title"])
    item.item_url = first_matching_attr(item_block, FIELD_SELECTORS["item_url"], "href")
    item.item_sku = first_matching_text(item_block, FIELD_SELECTORS["item_sku"])

    # Item-level numeric fields via validated regex; fall back to order level.
    item.quantity = extract_quantity(block_text) or quantity_fallback
    item.unit_price = extract_unit_price(block_text) or unit_price_fallback

    if include_sensitive:
        _maybe_fill_sensitive(item_block, item)

    # Keep only rows that identify an order/item.
    if item.order_id or item.item_title:
        return item
    return None


def extract_orders_from_page(page: Page, page_number: int, include_sensitive: bool) -> list[OrderItem]:
    """
    Extract order rows from the CURRENT page only, using an order->item model:

      1. Find ONE container per real order (validated by presence of 订单号).
      2. Parse ORDER-LEVEL fields once per order: order_id (validated 16-24
         digits), order_date, shop_name, actual_paid (only from approved
         labels, validated as currency), order_status, order_detail_url.
      3. Find ITEM sub-blocks inside the order; emit ONE ROW PER ITEM, each
         inheriting the order-level fields. (Single-item orders -> one row.)
      4. If no containers match, fall back to splitting page text on 订单号
         markers and parsing each block defensively.

    All numeric fields are validated; an order id is NEVER set to card text.
    """
    collected_ts = now_iso()
    items: list[OrderItem] = []

    containers, used_selector = find_order_containers(page)

    if containers:
        print(f"  Found {len(containers)} order card(s) using selector: {used_selector}")
        skipped_no_id = 0
        for c in containers:
            try:
                order_text = (c.inner_text() or "").strip()
            except Exception:
                order_text = ""
            if not order_text:
                continue

            # Order-level fields (resolved once).
            detail_url = first_matching_attr(c, FIELD_SELECTORS["order_detail_url"], "href")
            order_fields = parse_order_level(order_text, detail_url)
            order_fields["shop_name"] = first_matching_text(c, FIELD_SELECTORS["shop_name"])
            if detail_url:
                order_fields["order_detail_url"] = detail_url
            if not order_fields.get("order_status"):
                order_fields["order_status"] = first_matching_text(c, FIELD_SELECTORS["order_status"])

            # Validity warnings (counts/flags only — no sensitive data).
            if not order_fields.get("order_id"):
                skipped_no_id += 1
                print("    [warn] order card without a valid 16-24 digit order id "
                      "(order_id left blank).")
            if not order_fields.get("actual_paid"):
                print("    [warn] no labelled 实付款/实付/合计 amount found for an order "
                      "(actual_paid left blank).")

            # Item sub-blocks -> one row each.
            item_blocks = []
            for sel in ITEM_BLOCK_SELECTORS:
                try:
                    found = c.query_selector_all(sel)
                except Exception:
                    found = []
                if found:
                    item_blocks = found
                    break
            if not item_blocks:
                item_blocks = [c]  # treat whole order as a single item

            # Order-level price/qty hints (used when an item block lacks its own,
            # e.g. single-item orders where price sits in a sibling column).
            unit_hint = extract_unit_price(order_text)
            qty_hint = extract_quantity(order_text)

            for ib in item_blocks:
                row = build_item_row(ib, order_fields, page_number,
                                     collected_ts, include_sensitive,
                                     unit_price_fallback=unit_hint,
                                     quantity_fallback=qty_hint)
                if row:
                    items.append(row)

        if skipped_no_id:
            print(f"    [info] {skipped_no_id} card(s) had no valid order id.")
    else:
        # No containers matched — defensive full-page text fallback.
        print("  No order cards matched. Using text fallback (windowed on 订单号).")
        try:
            full = page.inner_text("body")
        except Exception:
            full = ""
        items = parse_text_fallback(full, page_number, collected_ts)

    return items


def parse_text_fallback(full: str, page_number: int = 0, collected_ts: str = "") -> list[OrderItem]:
    """
    Defensive fallback used only when no DOM order cards are found.

    The order DATE renders just ABOVE the 订单号 label, so we window each order
    from a little BEFORE its 订单号 up to just before the NEXT order's date.
    LOOKBACK captures this order's preceding date without stealing the next
    order's date. Each window is parsed with the same validated extractors.
    """
    items: list[OrderItem] = []
    LOOKBACK = 40
    positions = [m.start() for m in re.finditer(r"订单号|订单编号", full)]
    for i, pos in enumerate(positions):
        start = max(0, pos - LOOKBACK)
        end = (positions[i + 1] - LOOKBACK) if i + 1 < len(positions) else len(full)
        if end <= start:
            end = positions[i + 1] if i + 1 < len(positions) else len(full)
        ch = full[start:end]
        order_fields = parse_order_level(ch)
        if not order_fields.get("order_id"):
            continue  # require a valid id in fallback to avoid junk rows
        item = OrderItem(
            page_number_collected=page_number,
            collected_at=collected_ts,
            quantity=extract_quantity(ch),
            unit_price=extract_unit_price(ch),
            **{k: v for k, v in order_fields.items()
               if k in OrderItem.__annotations__},
        )
        items.append(item)
    return items


# ---------------------------------------------------------------------------
# Pagination (auto-next mode only) — safe, conservative, never bypasses anything
# ---------------------------------------------------------------------------

def _element_is_disabled(el) -> bool:
    """Detect a disabled next-page control across the ways Taobao marks it."""
    cls = (_safe_attr(el, "class") or "").lower()
    if "disabled" in cls:
        return True
    if (_safe_attr(el, "aria-disabled") or "").lower() == "true":
        return True
    try:
        if el.get_attribute("disabled") is not None:  # boolean HTML attribute
            return True
    except Exception:
        pass
    try:
        if not el.is_enabled():  # meaningful for real <button>/<a> elements
            return True
    except Exception:
        pass
    return False


def find_next_button(page):
    """
    Return (handle_or_None, reason). Only returns a handle that is VISIBLE,
    NOT disabled, and whose text/aria clearly indicates "next page". If a next
    control exists but is disabled, we report that (end reached) rather than
    clicking anything ambiguous.
    """
    found_disabled = False
    for sel in NEXT_PAGE_SELECTORS:
        try:
            cands = page.query_selector_all(sel)
        except Exception:
            continue
        for el in cands:
            try:
                txt = (el.inner_text() or "").strip()
            except Exception:
                txt = ""
            aria = _safe_attr(el, "aria-label")
            indicates_next = (
                "下一页" in txt or "下一页" in aria or "next" in aria.lower()
            )
            if not indicates_next:
                continue
            try:
                visible = el.is_visible()
            except Exception:
                visible = False
            if not visible:
                continue
            if _element_is_disabled(el):
                found_disabled = True
                continue
            return el, f"next-page control found via selector: {sel}"
    if found_disabled:
        return None, "next-page button is present but DISABLED (last page reached)"
    return None, "no next-page button found (last page reached)"


def current_first_order_id(page) -> str:
    """First order id currently on the page (no printing). '' if none/unknown."""
    containers, _ = find_order_containers(page)
    if not containers:
        return ""
    try:
        txt = containers[0].inner_text() or ""
    except Exception:
        return ""
    return extract_order_id(txt, "")


def page_change_signature(page, items: list) -> tuple:
    """A lightweight signature to detect whether the page actually changed.
    Uses the extracted order ids plus the URL (no sensitive data)."""
    ids = tuple(it.order_id for it in items if it.order_id)
    try:
        url = page.url or ""
    except Exception:
        url = ""
    return (ids, url)


def wait_for_next_page(page, before_first_id: str, timeout_ms: int = 20000) -> bool:
    """
    After clicking next, wait for the page to settle AND for content to change.
    Returns True if the first order id changed (page advanced), else False.
    """
    try:
        page.wait_for_load_state("networkidle", timeout=timeout_ms)
    except Exception:
        pass  # networkidle is best-effort; we still verify by content below
    deadline = time.time() + timeout_ms / 1000.0
    while time.time() < deadline:
        cur = current_first_order_id(page)
        if cur and cur != before_first_id:
            return True
        time.sleep(0.5)
    return False


# ---------------------------------------------------------------------------
# Shell-driveable modes: --login-only / --non-interactive / --start-page
# ---------------------------------------------------------------------------

LOGIN_WAIT_MAX_S = 900       # --login-only: max 15 minutes waiting for login
LOGIN_POLL_INTERVAL_S = 2.5  # poll every 2-3 seconds
NI_STABILIZE_WAIT_S = 60     # --non-interactive: wait for order list to render


class CaptchaStop(Exception):
    """Raised in --non-interactive mode when a login/verification/risk page is
    detected. Mapped to exit code 3 in run(). Never solved, never bypassed."""


class BrowserClosed(Exception):
    """Raised when the browser window / connection is gone mid-run (closed
    manually by the user, or crashed). Mapped to exit code 4 in run()."""


def browser_alive(page) -> bool:
    """Liveness probe: a real protocol round-trip. Returns False if the page /
    context / browser is gone (window closed, crash, killed)."""
    try:
        page.evaluate("() => 1")
        return True
    except Exception:
        return False


def looks_like_browser_closed(exc: Exception) -> bool:
    """Heuristic: does this exception mean the browser/page was closed or the
    connection to it was lost?"""
    msg = str(exc).lower()
    return any(m in msg for m in (
        "has been closed", "target closed", "targetclosed",
        "browser has been closed", "connection closed", "browser closed",
        "pipe", "crashed", "disconnected",
    ))


def order_list_ready(page) -> bool:
    """Login success criterion: URL is back on buyertrade.taobao.com AND at
    least one validated order container is rendered on the page."""
    try:
        url = page.url or ""
    except Exception:
        return False
    if "buyertrade.taobao.com" not in url:
        return False
    try:
        containers, _ = find_order_containers(page)
    except Exception:
        return False
    return bool(containers)


def wait_for_login(page, timeout_s: float = LOGIN_WAIT_MAX_S,
                   interval_s: float = LOGIN_POLL_INTERVAL_S) -> bool:
    """Poll until the user has logged in and the buyer order list is visible.
    Returns True on success, False on timeout. Pure polling — no interaction.
    Raises BrowserClosed immediately if the window is closed while waiting."""
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        if order_list_ready(page):
            return True
        if not browser_alive(page):
            raise BrowserClosed("browser window closed while waiting for login")
        time.sleep(interval_s)
    return False


def confirm_or_auto(args: argparse.Namespace, prompt: str, auto_log: str) -> str:
    """Replace input() in --non-interactive mode: print a log line and continue
    instead of blocking on the terminal. Returns the user input (or '' when
    auto-confirmed)."""
    if getattr(args, "non_interactive", False):
        print(auto_log, flush=True)
        return ""
    return input(prompt).strip().lower()


def _maybe_fill_sensitive(scope, item: OrderItem) -> None:
    """
    ONLY called when --include-sensitive is set. Reads receiver info if it
    happens to be visibly present in the order card. We never derive it from
    APIs and never print it. Best-effort and defensive.
    """
    name = first_matching_text(scope, ["[class*='receiver']", "[class*='consignee']", "[class*='收货']"])
    if name:
        item.receiver_name = name
    # Phone / address are intentionally only taken from explicit fields if
    # present; otherwise left blank. We do NOT scrape arbitrary digits.
    phone = first_matching_text(scope, ["[class*='phone']", "[class*='mobile']", "[class*='电话']"])
    if phone:
        item.receiver_phone = phone
    addr = first_matching_text(scope, ["[class*='address']", "[class*='addr']", "[class*='地址']"])
    if addr:
        item.receiver_address = addr


# ---------------------------------------------------------------------------
# Saving
# ---------------------------------------------------------------------------

def output_field_order(include_sensitive: bool) -> list[str]:
    return BASE_FIELDS + (SENSITIVE_FIELDS if include_sensitive else [])


def append_to_csv(path: str, items: list[OrderItem], include_sensitive: bool) -> None:
    """Append rows to a CSV, writing the header if the file is new/empty."""
    cols = output_field_order(include_sensitive)
    new_file = not os.path.exists(path) or os.path.getsize(path) == 0
    # utf-8-sig so Excel on Windows opens Chinese text correctly.
    with open(path, "a", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
        if new_file:
            writer.writeheader()
        for it in items:
            writer.writerow({k: getattr(it, k, "") for k in cols})


def rewrite_xlsx(path: str, all_items: list[OrderItem], include_sensitive: bool) -> None:
    """
    Rewrite the whole XLSX from the in-memory deduped set. (XLSX is not easily
    appendable, so we rewrite after each page — small data, this is fine.)
    """
    try:
        from openpyxl import Workbook
    except ImportError:
        print("ERROR: openpyxl not installed but --format xlsx requested.")
        print("Run: pip install openpyxl   (or it is in requirements.txt)")
        sys.exit(1)

    cols = output_field_order(include_sensitive)
    wb = Workbook()
    ws = wb.active
    ws.title = "orders"
    ws.append(cols)
    for it in all_items:
        ws.append([getattr(it, k, "") for k in cols])
    wb.save(path)


def load_existing(path: str, page_format: str) -> tuple[set[tuple], list[OrderItem]]:
    """
    Load rows already present in the output file so re-runs RESUME safely:
      * seeds the dedup set so existing rows are not appended again (CSV), and
      * preserves existing rows so the XLSX full-rewrite does not lose them.

    Returns (seen_keys, all_items). Missing/empty/unreadable file -> empties.
    """
    seen_keys: set[tuple] = set()
    all_items: list[OrderItem] = []
    if not os.path.exists(path) or os.path.getsize(path) == 0:
        return seen_keys, all_items

    valid_fields = set(OrderItem.__annotations__.keys())

    def _row_to_item(row: dict) -> Optional[OrderItem]:
        data = {k: (v if v is not None else "") for k, v in row.items() if k in valid_fields}
        if not data:
            return None
        it = OrderItem(**data)
        return it

    try:
        if page_format == "xlsx" or path.lower().endswith(".xlsx"):
            from openpyxl import load_workbook
            wb = load_workbook(path, read_only=True, data_only=True)
            ws = wb.active
            rows = ws.iter_rows(values_only=True)
            header = next(rows, None)
            if header:
                header = [str(h) if h is not None else "" for h in header]
                for raw in rows:
                    row = {header[i]: ("" if v is None else str(v))
                           for i, v in enumerate(raw) if i < len(header)}
                    it = _row_to_item(row)
                    if it:
                        all_items.append(it)
                        seen_keys.add(it.dedupe_key())
            wb.close()
        else:
            # utf-8-sig handles the BOM we write; falls back gracefully otherwise.
            with open(path, "r", newline="", encoding="utf-8-sig") as f:
                reader = csv.DictReader(f)
                for row in reader:
                    it = _row_to_item(row)
                    if it:
                        all_items.append(it)
                        seen_keys.add(it.dedupe_key())
    except Exception as e:
        print(f"  WARNING: could not read existing output to resume ({e}).")
        print("           Continuing; in-session dedup still applies.")
        return set(), []

    if all_items:
        print(f"  Resuming: loaded {len(all_items)} existing row(s) from {path}; "
              f"they will not be duplicated.")
    return seen_keys, all_items


def save_snapshot(page: Page, page_number: int) -> None:
    """Save HTML + visible text snapshot for debugging (no sensitive printing)."""
    os.makedirs(DEBUG_DIR, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    base = os.path.join(DEBUG_DIR, f"page{page_number:02d}_{stamp}")
    try:
        html = page.content()
        with open(base + ".html", "w", encoding="utf-8") as f:
            f.write(html)
    except Exception as e:
        print(f"  (could not save HTML snapshot: {e})")
    try:
        txt = page.inner_text("body")
        with open(base + ".txt", "w", encoding="utf-8") as f:
            f.write(txt)
    except Exception as e:
        print(f"  (could not save text snapshot: {e})")
    print(f"  Debug snapshot saved: {base}.html / .txt")


# ---------------------------------------------------------------------------
# Main interactive loop
# ---------------------------------------------------------------------------

def print_extraction_help() -> None:
    print(
        "\n  Extraction returned 0 orders. Debugging tips:\n"
        "    * Make sure the BUYER ORDER LIST is actually visible (not a detail\n"
        "      page, not the login page).\n"
        "    * Scroll the page so order cards have loaded, then press Enter again.\n"
        "    * Run with --debug and inspect the saved files in ./debug_snapshots\n"
        "      to see the real DOM, then update ORDER_CONTAINER_SELECTORS /\n"
        "      FIELD_SELECTORS near the top of this script.\n"
        "    * Taobao changes its layout often; editing those selector lists is\n"
        "      the intended way to adapt.\n"
    )


def process_current_page(page, page_number: int, args: argparse.Namespace,
                         seen_keys: set, all_items: list) -> Optional[list]:
    """
    Extract the current page, deduplicate, save progress, and print SAFE stats
    (counts only — never field values). Shared by manual and auto-next modes.

    Returns the list of parsed items (possibly empty) on success, or None if
    extraction raised (the caller decides how to handle that).
    """
    include_sensitive = bool(args.include_sensitive)
    page_format = args.format
    output_path = args.output

    if args.debug:
        save_snapshot(page, page_number)

    try:
        items = extract_orders_from_page(page, page_number, include_sensitive)
    except Exception as e:
        print(f"  Extraction error: {e}")
        if args.debug:
            save_snapshot(page, page_number)
        else:
            print("  Tip: re-run with --debug to save a page snapshot.")
        print_extraction_help()
        return None

    # Deduplicate by (order_id, item_title, actual_paid).
    fresh: list[OrderItem] = []
    for it in items:
        key = it.dedupe_key()
        if key in seen_keys:
            continue
        seen_keys.add(key)
        fresh.append(it)
        all_items.append(it)

    # Field-validity summary (counts only — never prints field values).
    n_id = sum(1 for it in items if valid_order_id(it.order_id))
    n_paid = sum(1 for it in items if valid_amount(it.actual_paid))
    n_title = sum(1 for it in items if it.item_title.strip())
    print(f"  Order cards found/parsed: {len(items)} row(s); {len(fresh)} new "
          f"after dedup; {len(all_items)} total collected.")
    print(f"  Validity: order_id {n_id}/{len(items)}, "
          f"actual_paid {n_paid}/{len(items)}, item_title {n_title}/{len(items)}.")

    if not items:
        print_extraction_help()

    # Save progress immediately after every page.
    if fresh or page_format == "xlsx":
        try:
            if page_format == "csv":
                append_to_csv(output_path, fresh, include_sensitive)
            else:  # xlsx — rewrite whole file from deduped set
                rewrite_xlsx(output_path, all_items, include_sensitive)
            print(f"  Saved progress -> {output_path}")
        except Exception as e:
            print(f"  WARNING: could not save output: {e}")

    return items


def run_manual_loop(page, args: argparse.Namespace, seen_keys: set, all_items: list) -> None:
    """Default mode: you press Enter per page and advance pages yourself."""
    page_number = 0
    while True:
        if args.max_pages and page_number >= args.max_pages:
            print(f"\nReached --max-pages limit ({args.max_pages}). Stopping.")
            break

        user = input(
            "\nWhen your order list is visible, press Enter to extract this page "
            "(or type 'q' then Enter to quit): "
        ).strip().lower()
        if user == "q":
            print("Quitting at your request.")
            break

        # Safety: never proceed on a verification / login page.
        reason = page_looks_blocked(page)
        if reason:
            print("\n  STOP: This looks like a login / verification / risk-control page.")
            print(f"        Reason: {reason}")
            print("        Please complete it MANUALLY in the browser, then navigate")
            print("        back to your order list and press Enter again.")
            print("        (This tool will not attempt to solve or bypass it.)")
            if args.debug:
                save_snapshot(page, page_number + 1)
            continue

        page_number += 1
        print(f"\nExtracting page #{page_number} ...")
        human_delay(1.0, 2.5, "reading the page gently")

        items = process_current_page(page, page_number, args, seen_keys, all_items)
        if items is None:
            page_number -= 1  # don't count a failed page
            continue

        print(
            "\n  Next: in the BROWSER, click to the NEXT order page yourself,\n"
            "  wait for it to load, then come back here and press Enter.\n"
            "  (Go slowly — low-frequency browsing is safer.)"
        )


def run_auto_loop(page, args: argparse.Namespace, seen_keys: set, all_items: list) -> None:
    """
    Auto-next mode: after you confirm the first page, the script extracts, saves,
    then clicks the next-page control itself — pausing periodically and STOPPING
    on the last page, on any verification/risk page, or if the page stops
    changing. It never bypasses verification or clicks ambiguous controls.
    """
    print("\n>>> AUTO-NEXT MODE enabled.")
    print(f"    Pacing {args.delay_min:.0f}-{args.delay_max:.0f}s between pages; "
          f"pause every {args.pause_every} page(s); max {args.max_pages} page(s).")
    confirm_or_auto(
        args,
        "\nWhen your FIRST order list page is visible, press Enter to begin "
        "(Ctrl+C to abort anytime): ",
        ">>> NON-INTERACTIVE: no Enter needed — proceeding automatically.",
    )

    if args.non_interactive:
        # Nobody is watching the browser: wait for the order list to actually
        # render (up to NI_STABILIZE_WAIT_S) before extracting page 1.
        print(f">>> NON-INTERACTIVE: waiting up to {NI_STABILIZE_WAIT_S}s for the "
              "order list to render...", flush=True)
        if wait_for_login(page, timeout_s=NI_STABILIZE_WAIT_S, interval_s=2.0):
            print("  Order list detected. Starting extraction.", flush=True)
        else:
            print("  Order list NOT detected within the wait window; the safety "
                  "check below will decide what to do.", flush=True)

    # --start-page N: advance (without extracting) until page N is showing.
    # Best-effort: if the next-page control is missing/disabled mid-way, we
    # start from whatever page we are on; dedup keeps re-runs safe.
    page_number = 0
    if args.start_page > 1:
        print(f">>> --start-page {args.start_page}: advancing without extracting "
              f"({args.start_page - 1} page turn(s))...", flush=True)
        for i in range(1, args.start_page):
            nxt, why = find_next_button(page)
            if nxt is None:
                print(f"  Cannot reach page {args.start_page}: {why}. "
                      "Starting from the current page.", flush=True)
                break
            before_id = current_first_order_id(page)
            human_delay(args.delay_min, args.delay_max,
                        f"advancing to page {i + 1} (no extraction)")
            try:
                nxt.click()
            except Exception as e:
                print(f"  Could not click next ({e}). Starting from the current "
                      "page.", flush=True)
                break
            if not wait_for_next_page(page, before_id):
                print("  Page did not change after clicking next; starting from "
                      "the current page.", flush=True)
                break
            page_number = i  # now showing page i+1
        print(f"  Now positioned at page ~{page_number + 1}.", flush=True)

    prev_sig = None
    while True:
        if args.max_pages and page_number >= args.max_pages:
            print(f"\nReached --max-pages limit ({args.max_pages}). Stopping.")
            break

        # Liveness probe BEFORE touching the page: if the user closed the
        # browser window (or it crashed), say so loudly instead of dying
        # silently inside some swallowed downstream exception.
        if not browser_alive(page):
            raise BrowserClosed(
                "browser window/tab closed or connection lost before page "
                f"#{page_number + 1} (window closed manually or crashed)")

        # Safety check BEFORE doing anything on this page.
        reason = page_looks_blocked(page)
        if reason:
            if args.non_interactive:
                # No human present to solve it: stop cleanly with exit code 3.
                print("\nCAPTCHA_STOP — login / verification / risk-control page "
                      "detected in non-interactive mode.", flush=True)
                print(f"  Reason: {reason}", flush=True)
                print("  Already-saved rows are on disk; re-run after handling it "
                      "manually.", flush=True)
                if args.debug:
                    save_snapshot(page, page_number + 1)
                raise CaptchaStop(reason)
            print("\n  STOP (auto-next halted): login / verification / risk-control detected.")
            print(f"        Reason: {reason}")
            print("        Please handle it MANUALLY in the browser. You can re-run later;")
            print("        already-saved rows will not be duplicated.")
            if args.debug:
                save_snapshot(page, page_number + 1)
            break

        page_number += 1
        print(f"\n--- Page #{page_number} ---")
        human_delay(1.0, 2.5, "reading the page gently")

        items = process_current_page(page, page_number, args, seen_keys, all_items)
        if items is None:
            print("  Stopping auto-next due to an extraction error on this page.")
            break

        # Detect 'page did not change' versus the previous page (loop guard).
        cur_sig = page_change_signature(page, items)
        if prev_sig is not None and cur_sig == prev_sig:
            print("  Page content matches the previous page — stopping to avoid a loop.")
            break
        prev_sig = cur_sig

        # Stop right after saving the final allowed page — do NOT find or click
        # next when we have already extracted --max-pages pages.
        if args.max_pages and page_number >= args.max_pages:
            print(f"\nReached --max-pages limit ({args.max_pages}). Stopping.")
            break

        # Periodic checkpoint so you stay in control.
        if args.pause_every and page_number % args.pause_every == 0:
            ans = confirm_or_auto(
                args,
                f"\n  Checkpoint: {page_number} page(s) done. Press Enter to "
                f"continue, or 'q' to quit: ",
                f"  Checkpoint: {page_number} page(s) done — auto-continuing "
                "(non-interactive).",
            )
            if ans == "q":
                print("  Quitting at your request.")
                break

        # Find the next-page control (safe, conservative).
        nxt, why = find_next_button(page)
        print(f"  Next page: {why}")
        if nxt is None:
            print("  Reached the end. Stopping auto-next.")
            break

        # Pace like a human, then click and VERIFY the page actually advanced.
        before_id = items[0].order_id if items else current_first_order_id(page)
        human_delay(args.delay_min, args.delay_max, "pacing before next page")
        try:
            nxt.click()
        except Exception as e:
            print(f"  Could not click the next-page control ({e}). Stopping.")
            break
        print("  Clicked next page; waiting for it to load and change...")
        if not wait_for_next_page(page, before_id):
            print("  Page did not change after clicking next — stopping (avoids re-clicking).")
            break


def run(args: argparse.Namespace) -> None:
    include_sensitive = bool(args.include_sensitive)
    page_format = args.format
    output_path = args.output

    print("=" * 70)
    print(" Taobao Order Exporter — LOCAL, MANUAL-LOGIN, READ-ONLY")
    print("=" * 70)
    print(" * You will log in MANUALLY in the browser window.")
    print(" * This tool only reads the page you navigate to. It does not")
    print("   bypass any verification and never handles your credentials.")
    if include_sensitive:
        print(" * --include-sensitive is ON: receiver fields will be saved IF")
        print("   visibly present. They are never printed to this terminal.")
    print("=" * 70)

    # Seed dedup/preserve store from any existing output file so re-runs RESUME
    # without duplicating CSV rows or overwriting existing XLSX data.
    seen_keys, all_items = load_existing(output_path, page_format)

    profile_path = os.path.abspath(PROFILE_DIR)
    os.makedirs(profile_path, exist_ok=True)

    with sync_playwright() as p:
        # Persistent context = stays logged in across runs. Headed so you can
        # see and interact with the real browser.
        # Prefer the locally installed Google Chrome (channel="chrome") to skip
        # the Playwright Chromium download; fall back to bundled Chromium.
        launch_kwargs = dict(
            user_data_dir=profile_path,
            headless=False,
            viewport={"width": 1280, "height": 900},
            args=["--disable-blink-features=AutomationControlled"],
        )
        try:
            context = p.chromium.launch_persistent_context(channel="chrome", **launch_kwargs)
            print("  Browser: system Google Chrome (channel=chrome)")
        except Exception as e:
            print(f"  (system Chrome unavailable: {e}; falling back to bundled Chromium)")
            context = p.chromium.launch_persistent_context(**launch_kwargs)
        page = context.pages[0] if context.pages else context.new_page()

        print(f"\nOpening start page: {args.url}")
        try:
            page.goto(args.url, wait_until="domcontentloaded", timeout=60000)
        except PWTimeout:
            print("  (page load timed out — that's OK, you can navigate manually)")
        except Exception as e:
            print(f"  (navigation note: {e})")

        # --login-only: open the page, poll until login succeeds (order list
        # visible on buyertrade), then close. Exit 0 on LOGIN_OK, 2 on timeout.
        if args.login_only:
            print("\n>>> LOGIN-ONLY MODE: please log in in the browser window.")
            print(f"    Polling every ~{LOGIN_POLL_INTERVAL_S}s for the buyer "
                  f"order list; giving up after {LOGIN_WAIT_MAX_S // 60} min.",
                  flush=True)
            ok = False
            try:
                ok = wait_for_login(page)
            except KeyboardInterrupt:
                print("\n  Interrupted while waiting for login.")
            except BrowserClosed as e:
                print(f"\nBROWSER_CLOSED while waiting for login: {e}", flush=True)
                try:
                    context.close()
                except Exception:
                    pass
                sys.exit(4)
            if ok:
                print("\nLOGIN_OK — login detected (order list visible on "
                      "buyertrade.taobao.com). Profile saved; closing browser.",
                      flush=True)
                try:
                    context.close()
                except Exception:
                    pass
                return
            print("\nLOGIN_TIMEOUT — no login detected within "
                  f"{LOGIN_WAIT_MAX_S // 60} minutes.", flush=True)
            try:
                context.close()
            except Exception:
                pass
            sys.exit(2)

        print(
            "\n>>> Please log in MANUALLY in the browser window if needed, then\n"
            "    navigate to your BUYER ORDER LIST page.\n"
            "    The browser stays logged in for next time (persistent profile).\n"
        )

        exit_code = 0
        try:
            if args.auto_next:
                run_auto_loop(page, args, seen_keys, all_items)
            else:
                run_manual_loop(page, args, seen_keys, all_items)
        except CaptchaStop as e:
            # Non-interactive safety stop: exit code 3, data already saved.
            exit_code = 3
        except BrowserClosed as e:
            print(f"\nBROWSER_CLOSED — {e}", flush=True)
            print("  Saved rows are already on disk. Re-run the same command to "
                  "resume (dedup prevents duplicates).", flush=True)
            exit_code = 4
        except KeyboardInterrupt:
            print("\n  Interrupted — stopping. Saved rows are already on disk.")
        except Exception as e:
            # Never die silently: a redirected log must always say WHY we stop.
            if looks_like_browser_closed(e):
                print(f"\nBROWSER_CLOSED — browser window closed or connection "
                      f"lost: {e}", flush=True)
                print("  Saved rows are already on disk. Re-run the same command "
                      "to resume (dedup prevents duplicates).", flush=True)
                exit_code = 4
            else:
                print(f"\nUNEXPECTED_ERROR — {type(e).__name__}: {e}", flush=True)
                traceback.print_exc()
                print("  Saved rows are already on disk; re-run the same command "
                      "to resume.", flush=True)
                exit_code = 1

        print("\nDone. Closing browser.")
        try:
            context.close()
        except Exception:
            pass
        if exit_code:
            sys.exit(exit_code)

    print(f"\nCollected {len(all_items)} unique order row(s).")
    print(f"Output file: {os.path.abspath(output_path)}")
    if args.debug:
        print(f"Debug snapshots: {os.path.abspath(DEBUG_DIR)}")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def parse_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Export YOUR OWN Taobao buyer orders from the visible page "
                    "to CSV/XLSX. Manual login; never bypasses verification.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  python taobao_order_exporter.py --output taobao_orders.csv\n"
            "  python taobao_order_exporter.py --output taobao_orders.csv --debug\n"
            "  python taobao_order_exporter.py --output taobao_orders.xlsx --format xlsx\n"
            "  python taobao_order_exporter.py --output taobao_orders.csv --max-pages 10\n"
            "  python taobao_order_exporter.py --output taobao_orders.xlsx --format xlsx "
            "--auto-next --max-pages 100\n"
        ),
    )
    parser.add_argument("--output", default="taobao_orders.csv",
                        help="Output file path (default: taobao_orders.csv).")
    parser.add_argument("--format", choices=["csv", "xlsx"], default="csv",
                        help="Output format (default: csv). xlsx needs openpyxl.")
    parser.add_argument("--max-pages", type=int, default=0,
                        help="Stop after this many extracted pages (0 = no limit in "
                             "manual mode; defaults to 100 when --auto-next is set).")
    parser.add_argument("--url", default=DEFAULT_START_URL,
                        help="Start URL to open (you can also just navigate manually).")
    parser.add_argument("--debug", action="store_true",
                        help="Save HTML/text snapshots to ./debug_snapshots per page.")
    parser.add_argument("--include-sensitive", action="store_true",
                        help="Also save receiver name/phone/address IF visibly present. "
                             "Default OFF. Never printed to terminal.")
    # --- Auto-next pagination (opt-in) ---
    parser.add_argument("--auto-next", action="store_true",
                        help="Automatically click the 'next page' control between pages. "
                             "OFF by default (manual mode). Stops at the last page, on any "
                             "verification/risk page, or if the page stops changing.")
    parser.add_argument("--delay-min", type=float, default=6.0,
                        help="Minimum random delay (seconds) before clicking next "
                             "(default: 6).")
    parser.add_argument("--delay-max", type=float, default=15.0,
                        help="Maximum random delay (seconds) before clicking next "
                             "(default: 15).")
    parser.add_argument("--pause-every", type=int, default=20,
                        help="In auto-next mode, pause for confirmation every N pages "
                             "(default: 20; 0 disables the periodic pause).")
    # --- Shell-driveable modes ---
    parser.add_argument("--login-only", action="store_true",
                        help="Open the order page, then POLL (every ~2.5s, up to 15 "
                             "min) until login succeeds (URL back on buyertrade and "
                             "order list rendered). Prints LOGIN_OK and exits 0; on "
                             "timeout prints LOGIN_TIMEOUT and exits 2. No extraction.")
    parser.add_argument("--non-interactive", action="store_true",
                        help="Never block on input(): all confirmations (first page, "
                             "per-N-page checkpoints) are auto-confirmed with a log "
                             "line. Implies --auto-next. On a verification/login page "
                             "prints CAPTCHA_STOP and exits 3 instead of waiting.")
    parser.add_argument("--start-page", type=int, default=1,
                        help="Auto-next mode: advance to page N (clicking next without "
                             "extracting) before starting. Best-effort; dedup still "
                             "protects re-runs. Default: 1 (always starts at page 1).")
    return parser.parse_args(argv)


def main() -> None:
    # Make redirected logs trustworthy (Start-Process -RedirectStandardOutput,
    # shell '> log', etc.): line-buffer stdout/stderr so every print hits the
    # file immediately. Without this, stdout is block-buffered under redirect
    # and a hard kill leaves the log stuck at an old line — looking exactly
    # like a "silent crash".
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(line_buffering=True)
        except Exception:
            pass
    args = parse_args()
    if args.format == "xlsx" and args.output.lower().endswith(".csv"):
        print("Note: --format xlsx but output ends in .csv. Consider an .xlsx name.")

    # Sanitize delay bounds (non-negative; min <= max).
    args.delay_min = max(0.0, float(args.delay_min))
    args.delay_max = max(0.0, float(args.delay_max))
    if args.delay_max < args.delay_min:
        args.delay_max = args.delay_min
    if args.pause_every < 0:
        args.pause_every = 0
    if args.start_page < 1:
        args.start_page = 1

    # --non-interactive only makes sense with auto page turning.
    if args.non_interactive and not args.auto_next:
        args.auto_next = True
        print("Note: --non-interactive implies --auto-next (enabled automatically).")

    # In auto-next mode, default to a 100-page safety cap if unset.
    if args.auto_next and args.max_pages == 0:
        args.max_pages = 100
        print("Note: --auto-next set without --max-pages; defaulting to --max-pages 100.")

    try:
        run(args)
    except KeyboardInterrupt:
        print("\nInterrupted. Whatever was saved so far is already on disk.")


if __name__ == "__main__":
    main()
