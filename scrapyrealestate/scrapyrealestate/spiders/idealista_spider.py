import logging
import re
from urllib.parse import urljoin

import scrapy
from bs4 import BeautifulSoup
from scrapy_playwright.page import PageMethod

from scrapyrealestate.domain.values import PortalKey, TransactionType
from scrapyrealestate.items import ScrapyrealestateItem


def _transaction_type(start_url: str) -> TransactionType | None:
    path_section = start_url.split("/")[3].split("-")[0]
    if path_section == "alquiler":
        return TransactionType.RENT
    if path_section == "venta":
        return TransactionType.BUY
    return None


def _listing_id(href: str) -> str:
    match = re.search(r"/inmueble/([^/?#]+)", href)
    return match.group(1) if match else ""


def _location_parts(title: str) -> tuple[str, str, str, str]:
    parts = [part.strip() for part in title.split(",")]
    town = neighbourhood = street = number = ""
    if len(parts) == 4:
        street = parts[0].split(" en ")[-1]
        number = parts[1]
        neighbourhood = parts[2]
        town = parts[3]
    elif len(parts) == 3:
        street = parts[0].split(" en ")[-1]
        neighbourhood = parts[1]
        town = parts[2]
    elif len(parts) == 2:
        neighbourhood = parts[0].split(" en ")[-1]
        town = parts[1]
    if " / " in town:
        town = town.split(" / ", 1)[1]
    elif "-" in town:
        town = town.split("-", 1)[0].strip()
    return town, neighbourhood, street, number


class IdealistaSpider(scrapy.Spider):
    name = "idealista"
    allowed_domains = ["idealista.com"]

    def start_requests(self):
        # DataDome can delay or block the result page, so the asynchronous
        # Playwright failure is handled through the request errback.
        yield scrapy.Request(
            f"{self.start_urls}",
            meta={
                "playwright": True,
                "playwright_page_methods": [
                    PageMethod("wait_for_selector", "main.listing-items", timeout=45000),
                ],
            },
            errback=self.on_error,
        )

    def on_error(self, failure):
        logging.error(f"Error al obtener datos de idealista.com: {failure.value}")

    def parse(self, response):
        soup = BeautifulSoup(response.text, "lxml")
        flats = soup.find_all("div", {"class": "item-info-container"})
        if not flats:
            challenge_html = str(soup).lower()
            challenge_markers = (
                "datadome",
                "captcha-delivery.com",
                "captcha",
                "access denied",
            )
            if any(marker in challenge_html for marker in challenge_markers):
                logging.warning(
                    "IDEALISTA: respuesta de desafío detectada "
                    "(posible bloqueo anti-bot)"
                )

        transaction_type = _transaction_type(self.start_urls)
        if transaction_type is None:
            return
        portal = PortalKey(self.name)
        seen_ids: set[str] = set()

        for flat in flats:
            link = flat.find(class_="item-link", href=True)
            if link is None:
                continue
            href = link["href"]
            title = link.get_text(strip=True)
            if not title:
                continue

            listing_id = _listing_id(href)
            if listing_id and listing_id in seen_ids:
                continue

            town, neighbourhood, street, number = _location_parts(title)
            price_element = flat.find("span", {"class": "item-price h2-simulated"})
            price = price_element.get_text(strip=True) if price_element else ""
            rooms = area = floor = ""
            for detail in flat.find_all("span", {"class": "item-detail"}):
                value = detail.get_text(strip=True)
                folded = value.casefold()
                if "hab." in folded:
                    rooms = value
                elif "m²" in folded or "m2" in folded:
                    area = value
                elif any(label in folded for label in ("planta", "bajo", "sótano", "entreplanta")):
                    floor = value

            item = ScrapyrealestateItem()
            item["id"] = listing_id
            item["price"] = price
            item["m2"] = area
            item["rooms"] = rooms
            item["floor"] = floor
            item["town"] = town
            item["neighbour"] = neighbourhood
            item["street"] = street
            item["number"] = number
            item["type"] = transaction_type.value
            item["title"] = title
            item["href"] = urljoin("https://www.idealista.com", href)
            item["site"] = portal.value
            if listing_id:
                seen_ids.add(listing_id)
            yield item

    parse_start_url = parse
