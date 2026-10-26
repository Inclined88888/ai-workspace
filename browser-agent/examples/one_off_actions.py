"""Direct browser actions without an LLM (free, fast, deterministic).

Uses books.toscrape.com and quotes.toscrape.com, sandbox sites published for scraping practice.
"""

import asyncio

from browser_agent import actions


async def main() -> None:
    info = await actions.open_url("https://books.toscrape.com", screenshot="out/books.png")
    print("opened:", info.title, info.url)

    info = await actions.click("https://books.toscrape.com", "text=Travel", screenshot="out/travel.png")
    print("after click:", info.title, info.url)

    print("heading:", await actions.page_text(info.url, selector="h1"))

    info = await actions.type_text("https://quotes.toscrape.com/login", "#username", "demo", screenshot="out/login.png")
    print("typed into:", info.url)

    for link in (await actions.links("https://books.toscrape.com"))[:5]:
        print("link:", link["text"], link["href"])


if __name__ == "__main__":
    asyncio.run(main())
