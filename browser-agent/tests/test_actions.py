from browser_agent import actions


async def test_open_and_screenshot(site, tmp_path):
    info = await actions.open_url(f"{site}/index.html", screenshot=str(tmp_path / "a.png"))
    assert info.title == "Test Shop"
    assert info.status == 200
    assert (tmp_path / "a.png").stat().st_size > 1000


async def test_click_navigates(site):
    info = await actions.click(f"{site}/index.html", "#cpu")
    assert info.title == "CPU list"
    assert info.url.endswith("/cpu.html")


async def test_type_and_submit(site):
    info = await actions.type_text(f"{site}/index.html", "input[name=q]", "ryzen", submit=True)
    assert info.title == "Results"
    assert "q=ryzen" in info.url


async def test_text_and_links(site):
    assert await actions.page_text(f"{site}/cpu.html", selector="h1") == "Ryzen 7 9700X"
    found = await actions.links(f"{site}/index.html")
    assert any(link["href"].endswith("/cpu.html") for link in found)


async def test_element_screenshot(site, tmp_path):
    info = await actions.screenshot(f"{site}/index.html", str(tmp_path / "h1.png"), selector="h1")
    assert info.screenshot and (tmp_path / "h1.png").exists()
