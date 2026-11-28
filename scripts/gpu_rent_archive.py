"""Read historical layouts from captured bytes, without fetching linked assets."""
import json
import re
from datetime import datetime

from gpu_rent_sources import (
    coreweave_config, droplet_config, lambda_instance, FEEDS, OD, RESERVED, SPOT, UNSPECIFIED, amount, capacity_blocks, coreweave_name, droplet_name, html, model_of,
    droplet_plans, rate, table_rows,
)


def clean(s):
    return " ".join(s.split())


def old_lambda(soup):
    for table in soup.select("table"):
        rows = table_rows(table)
        if not rows:
            continue
        head = [h.replace(" ", "").upper() for h in rows[0]]
        if not head or ("GPUS" not in head and not (head[0] == "" and "VRAM/GPU" in head)):
            continue
        col = head.index("GPUS") if "GPUS" in head else 0
        for cells in rows[1:]:
            if len(cells) != len(head) or any("reserved" in c.lower() for c in cells[:col + 1]):
                continue
            match = re.fullmatch(r"(?:On-demand )?(\d+)x\s+(.+?)(?: New(?: Price)?)?", clean(cells[col]), re.I)
            price = re.fullmatch(r"\$([\d.]+)\s*(/\s*(GPU\s*/\s*)?hr)?", clean(cells[-1]), re.I)
            if not match or not price or not model_of(match[2]):
                continue
            value = float(price[1])
            if head[-1] == "PRICE/GPU/HR*" or price[3]:
                note = "原表每 GPU 小时。"
            elif price[2] and head[-1] in ("PRICE", "PRICE*", "2024PRICE(EFFECTIVE1/1/2024)"):
                note = f"整机每小时 ${value:g} ÷ {match[1]} 张。"
                value = round(value / int(match[1]), 7)
            else:
                continue
            yield rate(lambda_instance(f"{match[1]}x {match[2]} · {cells[col+1]}", cells, head), value, note=note)


def old_coreweave(soup):
    # Earlier pages separately sold the GPU component, with CPU/RAM charged on top.
    for row in soup.select(".desktop .table-body-row"):
        cells = row.select(":scope > .w-col")
        if len(cells) != 5 or "GPU Component" not in row.parent.parent.get_text(" ", strip=True):
            continue
        name = clean(cells[0].get_text(" ", strip=True))
        price = cells[-1].find(string=re.compile(r"^\$[\d.]+$"))
        if model_of(name) and price:
            yield rate(name, amount(price), "GPU 部件挂牌", note="每 GPU 小时，vCPU、内存和存储另计。")
    for table in soup.select(".table-v2"):
        header = table.select_one(".table-v2-header")
        if not header:
            continue
        text = clean(header.get_text(" ", strip=True))
        if not text.startswith("GPU Model GPU Count") or "Instance Price" not in text or "Per Hour" not in text:
            continue
        heading = table.parent.get_text("|", strip=True).split("|")[0].upper()
        region = {"REGION: NORTH AMERICA": "北美", "REGION: EUROPE": "欧洲"}.get(heading, UNSPECIFIED)
        for row in table.select(".table-grid"):
            cells = row.select(":scope > .table-v2-cell")
            name = row.select_one(".table-model-name")
            if not name or len(cells) != 7:
                continue
            name = coreweave_name(row, clean(name.get_text(" ", strip=True)))
            count = re.fullmatch(r"(\d+)(?:\^\d+)?", cells[1].get_text(strip=True))
            price = amount(cells[6].get_text(strip=True))
            if model_of(name) and count and price:
                yield dict(rate(name, round(price / int(count[1]), 6), region=region,
                           note=f"整机每小时 ${price:g} ÷ {count[1]} 张。"), configuration=coreweave_config(cells))


TERMS = {"Hourly": (OD, "无固定期"), "Pay as you go": (OD, "无固定期"),
         "1 Week - 3 Months": (RESERVED, "1 周至 3 个月"), "1 - 6 Days": (RESERVED, "1–6 天"),
         "1 Week - 1 Month": (RESERVED, "1 周至 1 个月"), "2 - 3 Months": (RESERVED, "2–3 个月"),
         "4 - 6 Months": (RESERVED, "4–6 个月"), "6+ Months": (RESERVED, "6 个月以上"),
         "3-30 days": (RESERVED, "3–30 天"), "91+ days": (RESERVED, "91 天以上"),
         "7-30 days": (RESERVED, "7–30 天"), "31-90 days": (RESERVED, "31–90 天"),
         "91-180 days": (RESERVED, "91–180 天"), "181+ days": (RESERVED, "181 天以上")}


def old_together(soup):
    for table in soup.select("table"):
        heading = table.find_previous("h2")
        if heading and "Inference" in heading.get_text(" ", strip=True):
            continue
        heads = [clean(th.get_text(" ", strip=True)).rstrip("*") for th in table.select("thead th")]
        if not heads or heads[0] != "Hardware":
            continue
        terms = [TERMS[h] for h in heads if h in TERMS]
        if "Preemptible Compute" in heads and heads.count("Pay as you go") == 2:
            terms[0] = (SPOT, "可中断")
        for tr in table.select("tbody tr"):
            cells = [clean(td.get_text(" ", strip=True)) for td in tr.select("td")]
            if len(cells) < len(terms) + 1 and cells and cells[-1] == "Request Capacity":
                cells.extend(["Request Capacity"] * (len(terms) + 1 - len(cells)))
            if len(cells) != len(terms) + 1 or not cells[0].startswith("NVIDIA") or not model_of(cells[0]):
                continue
            cells[0] = cells[0].replace("HGX HGX ", "HGX ")
            for cell, (kind, term) in zip(cells[1:], terms):
                value = amount(cell.removesuffix(" Checking…"))
                if value:
                    yield rate(cells[0], value, kind, term)


def nebius_tables(soup, day):
    for table in soup.select(".pc-highlight-table-block__table, .pc-table__table"):
        rows = [[clean(c.get_text(" ", strip=True)) for c in r.select(".pc-highlight-table-block__cell, [role=cell]")]
                for r in table.select(".pc-highlight-table-block__row, [role=row]")]
        if not rows:
            continue
        header = rows[0]
        cols = [i for i, h in enumerate(header) if h in ("Price per GPU-hour", "On-demand, GPU-hour", "On demand", "Price per hour", "GPU-hour")]
        if not cols:
            continue
        col = cols[0]
        for i, h in enumerate(header):
            effective = re.search(r"Effective (\w+ \d+, \d{4})", h, re.I)
            if effective and datetime.strptime(effective[1], "%B %d, %Y").date().isoformat() <= day:
                col = i
        for cells in rows[1:]:
            if len(cells) != len(header) or not model_of(cells[0]):
                continue
            price = cells[col]
            value = amount(price.removeprefix("from "))
            if not value:
                continue
            product = cells[0].replace(" GPU with Intel", " with Intel CPU").replace(" GPU with AMD", " with AMD CPU")
            note = "官网所列配置起价。" if price.startswith("from ") else ""
            if header[0] == "GPU":
                count = re.match(r"(\d+) *[×x] ", product)
                if not count:
                    continue
                note = f"整机每小时 ${value:g} ÷ {count[1]} 张。"
                value = round(value / int(count[1]), 6)
            yield rate(product, value, "按需挂牌起价" if price.startswith("from ") else OD, note=note)


def old_digitalocean(soup):
    for h in soup.select("h3"):
        name = droplet_name(h.get_text(" ", strip=True))
        if not model_of(name):
            continue
        card = h.parent.parent
        text = card.get_text("|", strip=True)
        cells = text.split("|")
        if len(cells) < 4 or cells[2] != "/GPU/hour":
            continue
        # On older cards the first dollar amount can be a struck-through pre-discount price.
        if len(cells) > 5 and cells[4] == "/GPU/hour*" and cells[5] == "On-Demand Price":
            price = amount(cells[3])
            if price:
                yield dict(rate(name, price, model=model_of(name), note="官网标示的优惠按需价。"), **droplet_config(cells))
        elif cells[3] == "Reserved Price" and "12 months" in text and amount(cells[1]):
            yield dict(rate(name, amount(cells[1]), "预留挂牌起价", "12 个月", model=model_of(name)), **droplet_config(cells))


def cluster_floors(soup):
    for table in soup.select("table"):
        rows = table_rows(table)
        if not rows:
            continue
        name = ""
        if len(rows[0]) == 1:
            name = rows.pop(0)[0].replace(" GPUs", "")
        if not rows or rows[0][1:3] != ["Commitment", "As low as (per GPU-hour)"]:
            continue
        if not name:
            panel = table.find_parent(role="tabpanel")
            tabs = panel.find_previous(attrs={"role": "tablist"}) if panel else None
            button = tabs.find(id=panel.get("aria-labelledby")) if tabs else None
            name = button.get_text(" ", strip=True) if button else ""
        if not model_of(name):
            continue
        # The same page spells out its abbreviated B200 cluster size in the FAQ.
        if name in ("16-1.5k NVIDIA HGX B200", "From 16 to 1,536 NVIDIA HGX B200"):
            name = "NVIDIA HGX B200 · 16–1,536 张"
        for cells in rows[1:]:
            if len(cells) < 3:
                continue
            billing = clean(cells[0]).lower().replace("-", " ")
            kind = {"on demand": "按需挂牌起价", "reserved": "预留挂牌起价"}.get(billing)
            value = re.fullmatch(r"(?:As low as )?\$([\d.]+)(?:\s*[—–-]\s*Contact us)?", clean(cells[2]), re.I)
            if kind and value:
                yield rate("1-Click · " + name, float(value[1]), kind, cells[1],
                           note="原表按所选期限列示每 GPU 小时起价。")


def nebius_docs(body, day):
    soup = html(body)
    state = soup.select_one("script#__NEXT_DATA__")
    if state:
        content = json.loads(state.string)["props"]["pageProps"].get("html")
        if content:
            soup = html(content.encode())
    quotes, excerpts = [], []
    for table in soup.select("table"):
        active = False
        values = table_rows(table)
        matched = False
        for cells in values:
            if len(cells) != 3:
                continue
            name, price, unit = cells
            if price == "Price" and unit == "Per":
                active = name == "Item"
                bound = re.fullmatch(r"Item\s*[—–-]\s*(before|from) (\w+ \d+, \d{4})", name, re.I)
                if bound:
                    start = datetime.strptime(bound[2], "%B %d, %Y").date().isoformat()
                    active = day < start if bound[1].lower() == "before" else day >= start
                continue
            if not active or not name.startswith("NVIDIA") or unit != "1 GPU hour" or not amount(price):
                continue
            model = model_of(name)
            if not model:
                continue
            product = clean(name.replace("®", ""))
            component = product.endswith(". GPU")
            # A separately metered accelerator excludes the CPU/RAM cost of a whole instance.
            kind = "GPU 部件挂牌" if component else OD
            note = "vCPU 和内存另计。" if component else ""
            if re.fullmatch(r"NVIDIA (H100|H200|B200|B300) NVLink", product):
                product = "NVIDIA HGX " + model
            heading = table.find_previous(["h3", "h4"])
            if heading:
                sku = re.search(r"\bgpu-[a-z0-9-]+", heading.get_text(" ", strip=True))
                if sku:
                    product += " · " + sku[0]
            quotes.append(rate(product, amount(price), kind, note=note, model=model))
            matched = True
        if matched:
            heading = table.find_previous(["h3", "h4"])
            excerpts.append({"heading": heading.get_text(" ", strip=True) if heading else "", "table": values})
    return quotes, excerpts


def parse_capture(feed, body, day):
    """Return verified candidate rows and original price-table text or JSON excerpts."""
    if feed == "lambda-clusters":
        feed = "lambda"
    if feed == "nebius-docs":
        return nebius_docs(body, day)
    if feed == "oracle":
        if not body.lstrip().startswith(b"{"):
            raise ValueError("HTML 表内无美元 GPU 数值，价格由外部 API 注入")
        return list(FEEDS[feed][2](body)), None
    if feed == "aws-capacity-blocks":
        feed = "capacity"
    if feed.startswith("azure-"):
        return list(FEEDS[feed][2](body)), json.loads(body)
    if feed.startswith("aws-"):
        from gpu_rent_sources import AWS_TYPES
        d = json.loads(body)
        rows = list(FEEDS[feed][2](body))
        regions = {k: {sku: r for sku, r in v.items() if r.get("Instance Type") in AWS_TYPES}
                   for k, v in d["regions"].items()}
        return rows, {"manifest": d["manifest"], "regions": regions}
    if feed == "ec2page":
        if b"meteredUnitMaps" in body or b"awsstatic.com/pricing" in body:
            raise ValueError("按需页通过独立区域价格 JSON 加载，正文没有 Linux GPU 价目表")
        raise ValueError("正文未识别出带地区和 Linux 口径的 GPU 价目表")
    if feed == "spot":
        raise ValueError("历史使用逐 SKU 的日线，文档页未提供完整 UTC 日价")
    soup = html(body)
    selectors = {"coreweave": ".desktop .table-body-row, .table-v2.gpu-pricing, .table-v2.kubernetes-gpu-pricing",
                 "nebius": ".pc-highlight-table-block__table, .pc-table__table"}
    if feed == "capacity":
        # AWS announced the six-month option on Nov 25, 2024.
        term = "1–14 天，按可订档期" if day < "2024-11-25" else "1–182 天，按可订档期"
        rows = list(capacity_blocks(body, term))
    elif feed == "nebius":
        # Transitional price cards include both current and future effective columns.
        rows = list(nebius_tables(soup, day))
        if not rows:
            rows = list(FEEDS[feed][2](body))
    else:
        try:
            rows = list(FEEDS[feed][2](body))
        except (ValueError, KeyError, TypeError, IndexError, AttributeError):
            if feed not in ("lambda", "together"):
                raise
            rows = []
        if not rows and feed in ("lambda", "together", "coreweave"):
            parser = {"lambda": old_lambda, "together": old_together, "coreweave": old_coreweave}[feed]
            rows = list(parser(soup))
        if feed == "lambda":
            rows.extend(cluster_floors(soup))
        if feed == "digitalocean":
            rows += list(old_digitalocean(soup))
    # Excerpts include the original headers, cells and row ordering, not just parsed prices.
    elements = soup.select(selectors.get(feed, "table")) if rows else []
    if feed == "digitalocean" and rows:
        elements = [h.parent.parent for h in soup.select("h3") if model_of(h.get_text(" ", strip=True).replace("×8", "").replace("x8", ""))]
    if feed == "coreweave" and rows:
        elements = [e.parent if "table-body-row" not in e.get("class", []) else e.parent.parent for e in elements]
    excerpts = list(dict.fromkeys(e.get_text("|", strip=True) for e in elements
                                  if re.search(r"\$[\d.]|off on-demand", e.get_text(" ", strip=True)) and
                                  (feed not in ("lambda", "together", "capacity") or
                                   re.search(r"H100|B200|GB200|A100|A10|NVIDIA|H200|Effective.*Hourly Rate", e.get_text(" ", strip=True)))))
    if feed == "digitalocean" and rows:
        used_slugs = {r["note"].partition("：")[0] for r in rows if r["note"].startswith("gpu-")}
        excerpts.extend(plan for plan in droplet_plans(soup) if plan.get("slug") in used_slugs)
    if feed == "coreweave" and soup.select_one('[data-product="nvidia-b200"]'):
        excerpts.append({"data-product": "nvidia-b200", "labels": sorted({
            el.get_text(" ", strip=True) for el in soup.select('.table-model-name[data-product="nvidia-b200"]')
        })})
    if feed == "lambda" and rows:
        excerpts = []
        for table in soup.select("table"):
            text = table.get_text("|", strip=True)
            if "$" not in text:
                continue
            panel = table.find_parent(role="tabpanel")
            tabs = panel.find_previous(attrs={"role": "tablist"}) if panel else None
            tab = tabs.find(id=panel.get("aria-labelledby")) if tabs else None
            excerpts.append({"tab": tab.get_text(" ", strip=True) if tab else "", "table": text})
        cluster_size = soup.find(string=re.compile(r"1,536 NVIDIA GPUs"))
        if cluster_size:
            excerpts.append({"cluster_size": clean(str(cluster_size))})
    if rows and not excerpts:
        raise ValueError("没有可归档的原始价目表摘录")
    return rows, excerpts


def main():
    """List HTTP 200 captures or inspect a single id_ replay with its source excerpt."""
    import argparse
    from email.utils import parsedate_to_datetime
    from urllib.parse import urlencode
    from urllib.request import Request, urlopen
    from build_gpu_rent import UA, observation_key, verify

    parser = argparse.ArgumentParser(description=main.__doc__)
    parser.add_argument("feed", help="A feed name or historical layout such as capacity or nebius-docs")
    parser.add_argument("url", help="Original price page URL from the CDX index")
    parser.add_argument("--timestamp", help="14-digit UTC capture time. Omit to list captures since 2024.")
    args = parser.parse_args()
    if args.timestamp:
        stamp = datetime.strptime(args.timestamp, "%Y%m%d%H%M%S")
        replay = f"https://web.archive.org/web/{args.timestamp}id_/{args.url}"
        with urlopen(Request(replay, headers={"User-Agent": UA}), timeout=45) as response:
            actual = parsedate_to_datetime(response.headers["Memento-Datetime"])
            if actual.strftime("%Y%m%d%H%M%S") != args.timestamp:
                raise ValueError("Wayback 返回了其他时间的快照")
            rows, evidence = parse_capture(args.feed, response.read(), stamp.date().isoformat())
        unique = {}
        for row in rows:
            key = observation_key(row)
            if key in unique and unique[key]["price"] != row["price"]:
                raise ValueError(f"同一规格的两行报价冲突：{key}")
            unique[key] = row
        rows = list(unique.values())
        verify(rows)
        result = {"source": "Wayback 快照", "timestamp": args.timestamp, "url": replay,
                  "quotes": rows, "excerpt": evidence}
    else:
        query = urlencode({"url": args.url, "from": "2024", "output": "json",
                           "filter": "statuscode:200", "fl": "timestamp,original,digest"})
        with urlopen(Request("https://web.archive.org/cdx/search/cdx?" + query, headers={"User-Agent": UA}),
                     timeout=60) as response:
            result = json.loads(response.read() or "[]")
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
