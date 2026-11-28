"""Read public GPU price cards. A quote keeps its product, geography and term."""
import json
import re
from datetime import datetime, timezone
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


def coreweave_name(row, name):
    model = row.select_one(".table-model-name")
    if name == "NVIDIA B200" and model and model.get("data-product") == "nvidia-b200":
        return "NVIDIA HGX B200"
    return name


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
            name = coreweave_name(row, name)
            n = int(count.split("^")[0])
            for kind, cls in [(OD, "instance-price"), (SPOT, "spot-price")]:
                cell = row.select_one("." + cls + " .item-value")
                value = amount(cell.get_text(strip=True)) if cell else None
                if value:
                    yield dict(rate(name, round(value / n, 6), kind, "可中断" if kind == SPOT else "无固定期",
                               region, f"自行折算：整机 ${value:g}/小时 ÷ {n} 张。"), configuration=coreweave_config(cells))


def lambda_instance(name, cells, head):
    columns = [h.lower().replace(" ", "") for h in head]
    if not all(col in columns for col in ("vcpus", "ram", "storage")):
        return name + " · 配套配置未披露"
    fields = [cells[columns.index(col)] for col in ("vcpus", "ram", "storage")]
    return f"{name} · {fields[0]} vCPU / {fields[1]} RAM / {fields[2]}"


def coreweave_config(cells):
    count, vram, cpu, ram, disk = [c.get_text(" ", strip=True).split("^")[0] for c in cells[1:6]]
    return f"{count} GPU / {vram} GB VRAM / {cpu} vCPU / {ram} GB RAM / {disk} TB"


def droplet_config(cells):
    labels = ("GPUs per Droplet", "GPU Memory", "Droplet vCPUs", "Droplet Memory", "Boot Disk", "Scratch Disk")
    fields = zip(cells, cells[1:])
    empty_or_label = {*labels, "Transfer", "—", "-", ""}
    values = {label: value for label, value in fields if label in labels and value not in empty_or_label}
    spec = " / ".join(f"{label} {values[label]}" for label in labels if label in values)
    return {"configuration": spec} if spec else {}


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
                panel = table.find_parent(role="tabpanel")
                label = panel["aria-labelledby"]
                # Archived pages reused tab IDs for the cluster and instance selectors.
                tabs = panel.find_previous(attrs={"role": "tablist"})
                button = tabs.find(id=label) if tabs else None
                count = button.get_text(strip=True) if button else ""
                if not re.fullmatch(r"[1-9]\d*x", count):
                    raise ValueError("Lambda 实例表未标明 GPU 数量")
                yield rate(lambda_instance(f"{count} {row[0]} · {row[1]}", row, head), value, note="每 GPU 小时，税另计。")


def droplet_name(name):
    # Some cards repeat the model after the device-count badge in responsive markup.
    name = " ".join(name.split())
    if name in ("NVIDIA RTX 4000 Ada", "NVIDIA RTX 6000 Ada"):
        return name + " Generation"
    count = re.search(r"[×x]8(?=\s|$)", name)
    return name[:count.start()].rstrip() + " · 8 张" if count else name


def droplet_plans(soup):
    # Next.js streams the pricing component's JSON props inside string chunks.
    chunks = []
    for script in soup.find_all("script"):
        call = re.fullmatch(r"\s*self\.__next_f\.push\((.*)\);?\s*", script.string or "", re.S)
        if call:
            chunk = json.loads(call[1])
            if chunk[0] == 1 and isinstance(chunk[1], str):
                chunks.append(chunk[1])
    stream = "".join(chunks)
    for start in re.finditer(r'"plans"\s*:\s*(?=\[)', stream):
        plans, _ = json.JSONDecoder().raw_decode(stream[start.end():])
        yield from plans


def droplet_plan_rate(plan):
    slug = plan.get("slug") or ""
    match = re.fullmatch(r"gpu-[a-z0-9]+x(\d+)-\d+gb", slug)
    if not match or not model_of(plan["description"]):
        return None
    count = int(match[1])
    if count <= 0 or plan["gpu"].get("variant", 1) != count:
        raise ValueError(f"DigitalOcean 实例卡数不一致：{slug}")
    name = droplet_name(re.sub(r"[×x]\d+$", "", plan["description"]))
    if count > 1:
        name += f" · {count} 张"
    hourly = plan["price"]["hourly"]
    return dict(rate(name, round(hourly / count, 9), note=f"{slug}：整机每小时 ${hourly:g} ÷ {count} 张。"),
                configuration=f"GPUs per Droplet {count} / Droplet vCPUs {plan['cpus']} / Droplet Memory {plan['memory']} GiB")


def digitalocean(body):
    soup = html(body)
    card_prices = {}
    for h in soup.select("h3"):
        name = droplet_name(h.get_text(" ", strip=True))
        cells = h.parent.parent.get_text("|", strip=True).split("|")
        if len(cells) < 4 or cells[2] != "/GPU/hour":
            continue
        options = {"12 Month Reserved Price*": (RESERVED, "12 个月"), "Current Spot Price*": (SPOT, "可中断"),
                   "On-Demand Price": (OD, "无固定期")}
        if cells[3] in options:
            row = dict(rate(name, amount(cells[1]), *options[cells[3]]), **droplet_config(cells))
            if row["kind"] == OD:
                card_prices[name] = row["price"]
            yield row
    for plan in droplet_plans(soup):
        row = droplet_plan_rate(plan)
        if row is None:
            continue
        name = row["product"]
        if name not in card_prices:
            yield row
        elif card_prices[name] != row["price"]:
            raise ValueError(f"DigitalOcean 价格卡与实例价不符：{name}")
        card_prices[name] = row["price"]


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


def oracle(body):
    for item in json.loads(body)["items"]:
        name = item["displayName"]
        if ("Compute" not in name or "Cloud@Customer" in name or
                item["metricName"] != "GPU Per Hour" or not model_of(name)):
            continue
        for currency in item["currencyCodeLocalizations"]:
            if currency["currencyCode"] == "USD":
                for p in currency["prices"]:
                    if p["model"] == "PAY_AS_YOU_GO":
                        yield dict(rate(name + " · " + item["partNumber"], p["value"], region="全球公开价"),
                                   partNumber=item["partNumber"])


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
        yield dict(rate(sku, round(value / (8760 * years * n), 6), "期限计费折扣", f"{years} 年", "美国东部",
                   f"自行折算：整机全期 ${value:,.0f} ÷ {years} 年 ÷ 8,760 小时 ÷ {n} 张，不含容量保证。", model), effective_date=r["effectiveStartDate"][:10])


def capacity_blocks(body, term="1–182 天，按可订档期"):
    for table in html(body).select("table"):
        rows = table_rows(table)
        if not rows:
            continue
        header = [" ".join(c.upper().split()) for c in rows[0]]
        if header[:2] not in (["INSTANCE TYPE", "REGION"], ["ULTRASERVER TYPE", "REGION"],
                              ["INSTANCE OR ULTRASERVER TYPE", "REGION"]):
            continue
        separate = next((i for i, c in enumerate(header) if c in
                         ("EFFECTIVE GPU HOURLY RATE", "EFFECTIVE ACCELERATOR HOURLY RATE")), None)
        combined = next((i for i, c in enumerate(header) if "PER ACCELERATOR" in c), None)
        if separate is None and combined is None:
            raise ValueError("Capacity Blocks 表头缺少每加速器时价")
        for cells in rows[1:]:
            sku, region = cells[:2]
            sku = re.sub(r"\s*\((?:UltraServer|Instance)\)$", "", sku, flags=re.I)
            if sku in AWS_TYPES:
                model = AWS_TYPES[sku][0]
            elif sku in ("u-p6e-gb200x72", "u-p6e-gb200x36"):
                model = "GB200"
            else:
                continue
            if separate is not None:
                price = re.fullmatch(r"\s*\$([\d.]+)(?:\s*USD)?\s*", cells[separate])
            else:
                price = re.search(r"\(\s*\$([\d.]+)(?:\s*USD)?\s*\)", cells[combined])
            if not price:
                raise ValueError(f"无法识别 {sku} {region} 的每加速器时价")
            yield rate(sku, float(price[1]), "容量预订挂牌", term, region, "原表每加速器有效时价。", model)


def impossible_savrn(body):
    # Reduced facts from the licensed dataset. No HTML or branding is redistributed.
    for item in json.loads(body):
        if item["sku"] == "342-MI355X" and item["region"] == "EU" and item["gpus"] == 8 and item["nodes"] == 1:
            yield dict(rate("342-MI355X · 1 节点 8 张", item["usd_per_gpu_hour"], RESERVED,
                            f'{item["months"]} 个月', "欧盟", "SAVRN 核对的厂商报价。", "MI355X"),
                       observed_date=item["observed_date"])


def rental_facts(body):
    data = json.loads(body)
    if isinstance(data, list):
        return data
    facts = []
    for offer in data["offers"]:
        if (offer.get("provider") != "impossiblecloud" or offer.get("gpu") != "mi355x"
                or offer.get("region") != "EU" or offer.get("instance", "").partition(";")[0] != "mi355x-8gpu-342-mi355x"
                or offer.get("kind") != "reserved" or not offer.get("fetched_at")):
            continue
        commitment = offer.get("commitment", {})
        waitlist = offer.get("availability") == "waitlist"
        starting = offer.get("rate_basis") == "starting-at"
        ordinary = (offer.get("rate_basis") in (None, "exact") and
                    offer.get("availability") in (None, "in-stock", "not-reported"))
        if not (waitlist and starting or ordinary):
            continue
        valid_terms = (24,) if waitlist else (12, 24)
        if commitment.get("months") in valid_terms:
            months = commitment["months"]
        elif waitlist and commitment.get("label") == "Tier 12 months; published minimum 24 months; confirm contract":
            months = 12
        else:
            continue
        facts.append(dict(sku="342-MI355X", region="EU", nodes=1, gpus=8, months=months,
                          usd_per_gpu_hour=offer["usd_hr"], fetched_at=offer["fetched_at"],
                          waitlist=waitlist, starting_at=starting, source=offer["source_url"],
                          **({"minimum_months": 24} if waitlist else {})))
    return facts


def impossible_rental(body):
    for item in rental_facts(body):
        term = "12 个月报价，最低合同 24 个月，须另行确认" if item["months"] == 12 else "24 个月，最低合同 24 个月"
        kind, note = "候补预留挂牌起价", "GPU Rental Prices 逐项采集，按申请交付。"
        if not item["waitlist"] and not item["starting_at"]:
            kind, term, note = RESERVED, f'{item["months"]} 个月', "GPU Rental Prices 核对的普通预留挂牌。"
        elif not (item["waitlist"] and item["starting_at"]):
            continue
        observed = datetime.fromisoformat(item["fetched_at"].replace("Z", "+00:00")).astimezone(timezone.utc)
        yield dict(rate("342-MI355X · 1 节点 8 张", item["usd_per_gpu_hour"], kind,
                        term, "欧盟", note, "MI355X"),
                   observed_date=observed.date().isoformat())


AZURE_SKUS = {"Standard_ND96isr_H100_v5": ("H100", 8), "Standard_ND128isr_NDR_GB200_v6": ("GB200", 4)}
# One request is one independent failure domain.
FEEDS = {
    "aws-capacity-blocks": ("AWS Capacity Blocks", "https://aws.amazon.com/ec2/capacityblocks/pricing/", capacity_blocks),
    "impossible-rental": ("Impossible Cloud", "https://raw.githubusercontent.com/adriannutiu/gpu-rental-prices/main/data/latest.json", impossible_rental),
    "impossible-savrn": ("Impossible Cloud", "https://savrn.com/ai-index/pricing/gpus/mi355x", impossible_savrn),
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
