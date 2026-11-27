"""Read public GPU price cards. A quote keeps its product, geography and term."""
import json
import re
from urllib.parse import quote, urlencode

from bs4 import BeautifulSoup

from build_nebius import rate_rows

UNSPECIFIED = "价目表未分地区"
OD, SPOT, RESERVED = "按需挂牌", "可中断挂牌", "预留挂牌"
MODEL = re.compile(r"\b(GB[23]00|GH200|[ABH][123]00T?|MI\d{3}X|RTX PRO 6000|Quadro RTX 6000|RTX [46]000 Ada|RTX A6000|A6000|V100|A10|L40S?)\b", re.I)


def model_of(name):
    match = MODEL.search(name)
    return match[1].upper().replace(" ADA", " Ada") if match else None


def amount(text):
    match = re.fullmatch(r"\$([\d.]+)(?:/h)?", text.strip())
    return float(match[1]) if match else None


def html(body):
    return BeautifulSoup(body, "html.parser", from_encoding="utf-8")


def table_rows(table):
    return [[cell.get_text(" ", strip=True) for cell in tr.find_all(["th", "td"], recursive=False)]
            for tr in table.select("tr")]


def rate(name, value, kind=OD, term="无固定期", region=UNSPECIFIED, note="", model=None):
    return dict(model=model or model_of(name), product=name, price=value, kind=kind,
                term=term, region=region, note=note)


def together(body):
    table, = [t for t in html(body).select("table") if "Preemptible" in t.get_text() and "31-90 days" in t.get_text()]
    header, terms, *rows = table_rows(table)
    if [s.lower() for s in header] != ["hardware", "preemptible", "on-demand", "reserved"] or terms != ["Pay as you go", "Pay as you go", "3-30 days", "31-90 days", "91+ days"]:
        raise ValueError("GPU Clusters 表头变更")
    for name, *cells in rows:
        # Unpriced rack configurations have a single enquiry for the reserved columns.
        if len(cells) == 3 and all(c.startswith("—") for c in cells[:2]) and cells[2] == "Request Capacity":
            continue
        if len(cells) != len(terms):
            raise ValueError(f"Together {name} 的价格列数与表头不符")
        for cell, (kind, term) in zip(cells, [(SPOT, "可中断"), (OD, "无固定期"), (RESERVED, "3–30 天"),
                                              (RESERVED, "31–90 天"), (RESERVED, "91 天以上")]):
            value = amount(cell.split()[0]) if cell.strip() else None
            if value is not None:
                yield rate(name, value, kind, term)


def nebius(body):
    rows, col = rate_rows(body)
    for row in rows:
        price = row[col].strip()
        starting = price.startswith("from ")
        value = amount(price[5:] if starting else price)
        if value is not None:
            yield rate(row[0], value, "按需挂牌起价" if starting else OD,
                       note="官网所列配置起价。" if starting else "")


def coreweave(body):
    for table in html(body).select(".gpu-pricing"):
        heading = table.parent.get_text("|", strip=True).split("|")[0]
        region = {"REGION: NORTH AMERICA": "北美", "REGION: EUROPE": "欧洲"}.get(heading)
        if not region or not table.select_one(".kubernetes-gpu-pricing"):
            continue
        for row in table.select(".table-grid"):
            cells = row.select(":scope > .table-v2-cell")
            if not row.select_one(".table-model-name") or len(cells) < 7:
                continue
            name, count = [c.get_text(" ", strip=True) for c in cells[:2]]
            n = int(count.split("^")[0])
            for kind, cls in [(OD, "instance-price"), (SPOT, "spot-price")]:
                cell = row.select_one("." + cls + " .item-value")
                value = amount(cell.get_text(strip=True)) if cell else None
                if value:
                    yield rate(name, round(value / n, 6), kind, "可中断" if kind == SPOT else "无固定期",
                               region, f"自行折算：整机 ${value:g}/小时 ÷ {n} 张。")


def lambda_cloud(body):
    soup = html(body)
    for table in soup.select("table"):
        head, *rows = table_rows(table)
        if "PRICE/GPU/HR*" not in head:
            continue
        for row in rows:
            value = amount(row[head.index("PRICE/GPU/HR*")])
            if not value:
                continue
            if "DURATION" in head:
                yield rate(f"1-Click · {row[0]} · {row[2]} 张", value, RESERVED, row[1], note="每 GPU 小时，税另计。")
            else:
                label = table.find_parent(role="tabpanel")["aria-labelledby"]
                count = soup.find(id=label).get_text(strip=True)
                yield rate(f"{count} {row[0]} · {row[1]}", value, note="每 GPU 小时，税另计。")


def digitalocean(body):
    for h in html(body).select("h3"):
        name = h.get_text(" ", strip=True)
        cells = h.parent.parent.get_text("|", strip=True).split("|")
        if len(cells) < 4 or cells[2] != "/GPU/hour":
            continue
        options = {"12 Month Reserved Price*": (RESERVED, "12 个月"), "Current Spot Price*": (SPOT, "可中断"),
                   "On-Demand Price": (OD, "无固定期")}
        if cells[3] in options:
            yield rate(name, amount(cells[1]), *options[cells[3]])


def verda(body):
    tables = [table_rows(t) for t in html(body).select("table")]
    discounts, = [r[1:] for r in tables if r[0] == ["Commitment", "Discount"]]
    for rows in tables:
        if rows[0] != ["Hardware", "CPUs", "RAM", "GPU VRAM", "Price", "Spot price"] or not rows[1][0].startswith("1x GB300"):
            continue
        for name, cpus, ram, _, listed, spot in rows[1:]:
            product = f"{name} · {cpus} vCPU / {ram} RAM"
            value = amount(listed)
            yield rate(product, value)
            yield rate(product, amount(spot), SPOT, "可中断")
            for term, discount in discounts:
                pct = int(re.fullmatch(r"(\d+)% off on-demand", discount)[1])
                yield rate(product, round(value * (1 - pct / 100), 6), RESERVED, term,
                           note=f"自行折算：单卡实例按需价 ${value:g} × (1 − {pct}%)。")


def oracle_gpu_items(body):
    data = json.loads(body)
    data["items"] = [item for item in data["items"]
                     if "Compute" in item["displayName"] and "Cloud@Customer" not in item["displayName"]
                     and item["metricName"] == "GPU Per Hour" and model_of(item["displayName"])
                     and any(c["currencyCode"] == "USD" and any(p["model"] == "PAY_AS_YOU_GO" for p in c["prices"])
                             for c in item["currencyCodeLocalizations"])]
    return {"lastUpdated": data["lastUpdated"], "items": data["items"]}


def oracle(body):
    for item in oracle_gpu_items(body)["items"]:
        name = item["displayName"]
        for currency in item["currencyCodeLocalizations"]:
            if currency["currencyCode"] == "USD":
                for p in currency["prices"]:
                    if p["model"] == "PAY_AS_YOU_GO":
                        yield rate(name + " · " + item["partNumber"], p["value"], region="全球公开价")


AWS_TYPES = {"p4d.24xlarge": ("A100", 8), "p4de.24xlarge": ("A100", 8), "p5.4xlarge": ("H100", 1),
             "p5.48xlarge": ("H100", 8), "p5e.48xlarge": ("H200", 8), "p5en.48xlarge": ("H200", 8),
             "p6-b200.48xlarge": ("B200", 8), "p6-b300.48xlarge": ("B300", 8)}


def aws_rate(sku, price, region):
    model, n = AWS_TYPES[sku]
    return rate(sku, round(price / n, 7), region=region, model=model,
                note=f"自行折算：Linux 整机 ${price:g}/小时 ÷ {n} 张。")


def aws(body):
    data = json.loads(body)
    if data["manifest"]["currencyCode"] != "USD":
        raise ValueError("AWS 返回非美元价")
    for region, rows in data["regions"].items():
        for r in rows.values():
            sku = r["Instance Type"]
            if sku in AWS_TYPES and r["Operating System"] == "Linux" and r["Pre Installed S/W"] == "NA":
                yield aws_rate(sku, float(r["price"]), region)


def azure(body):
    d = json.loads(body)
    if d.get("NextPageLink"):
        raise ValueError("Azure 返回额外分页")
    for r in d["Items"]:
        if (r["armRegionName"] != "eastus" or r["currencyCode"] != "USD" or not r["isPrimaryMeterRegion"]
                or "Windows" in r["productName"] or r["type"] != "Reservation" or r["unitOfMeasure"] != "1 Hour"):
            continue
        sku = r["armSkuName"]
        model, n = AZURE_SKUS[sku]
        years = int(re.fullmatch(r"(\d+) Years?", r["reservationTerm"])[1])
        value = r["retailPrice"]
        yield rate(sku, round(value / (8760 * years * n), 6), "期限计费折扣", f"{years} 年", "美国东部",
                   f"自行折算：整机全期 ${value:,.0f} ÷ {years} 年 ÷ 8,760 小时 ÷ {n} 张，不含容量保证。", model)


AZURE_SKUS = {"Standard_ND96isr_H100_v5": ("H100", 8), "Standard_ND128isr_NDR_GB200_v6": ("GB200", 4)}
# One request is one independent failure domain.
FEEDS = {
    "together": ("Together", "https://www.together.ai/pricing", together),
    "nebius": ("Nebius", "https://nebius.com/prices", nebius),
    "coreweave": ("CoreWeave", "https://www.coreweave.com/pricing", coreweave),
    "lambda": ("Lambda", "https://lambda.com/pricing", lambda_cloud),
    "digitalocean": ("DigitalOcean", "https://www.digitalocean.com/pricing/gpu-droplets", digitalocean),
    "verda": ("Verda", "https://verda.com/pricing", verda),
    "oracle": ("Oracle", "https://apexapps.oracle.com/pls/apex/cetools/api/v1/products/?currencyCode=USD", oracle),
}
for suffix, region in [("virginia", "US East (N. Virginia)"), ("ohio", "US East (Ohio)"), ("oregon", "US West (Oregon)"),
                       ("california", "US West (N. California)")]:
    url = "https://b0.p.awsstatic.com/pricing/2.0/meteredUnitMaps/ec2/USD/current/ec2-ondemand-without-sec-sel/"
    FEEDS["aws-" + suffix] = ("AWS EC2", url + quote(region) + "/Linux/index.json", aws)
for sku, (model, _) in AZURE_SKUS.items():
    query = urlencode({"$filter": f"armRegionName eq 'eastus' and armSkuName eq '{sku}'",
                       "api-version": "2023-01-01-preview"})
    FEEDS["azure-" + model.lower()] = ("Azure", "https://prices.azure.com/api/retail/prices?" + query, azure)
