"""
CLI-генератор PDF-чеков из CSV/JSON по HTML-шаблонам.

Структура проекта:
    data/       — файлы с данными (*.csv, *.json)
    templates/  — HTML-шаблоны (Jinja2)
    fonts/      — шрифты с поддержкой кириллицы (DejaVu Sans)
    output/     — готовые PDF

Запуск:  python main.py
"""

from __future__ import annotations

import csv
import json
import os
import platform
import re
import subprocess
import sys
from collections import OrderedDict
from datetime import datetime
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
TEMPLATES_DIR = BASE_DIR / "templates"
OUTPUT_DIR = BASE_DIR / "output"
FONTS_DIR = BASE_DIR / "fonts"

# Возможные названия колонки с номером чека (сравниваются без регистра, пробелов и "_").
INVOICE_ID_KEYS = ("invoiceid", "invoice", "invoiceno", "invoicenumber", "id", "номерчека", "чек")
ITEMS_KEYS = ("items", "lines", "products", "positions", "товары")
# Поля, относящиеся к позиции чека, а не к самому чеку — в плоском CSV они не должны
# попадать в шапку (invoice-level метаданные), даже если формально есть в каждой строке.
ITEM_FIELD_KEYS = (
    "name", "item", "product", "description", "товар", "наименование",
    "quantity", "qty", "количество", "кол-во", "колво",
    "price", "unitprice", "цена",
    "amount", "sum", "linetotal", "сумма",
)

# Базовый CSS: подключение DejaVu Sans для кириллицы. Добавляется к любому шаблону.
FONT_CSS = f"""
@font-face {{
    font-family: "DejaVu Sans";
    src: url("{(FONTS_DIR / 'DejaVuSans.ttf').as_uri()}");
    font-weight: normal;
}}
@font-face {{
    font-family: "DejaVu Sans";
    src: url("{(FONTS_DIR / 'DejaVuSans-Bold.ttf').as_uri()}");
    font-weight: bold;
}}
html, body {{ font-family: "DejaVu Sans", "Roboto", sans-serif; }}
"""


# ─────────────────────────── Вывод в консоль ───────────────────────────

def print_header(title: str) -> None:
    line = "═" * (len(title) + 4)
    print(f"\n╔{line}╗\n║  {title}  ║\n╚{line}╝")


def print_menu(title: str, options: list[str]) -> None:
    print(f"\n{title}")
    print("─" * 50)
    if not options:
        print("  (пусто)")
    width = len(str(len(options)))
    for i, option in enumerate(options, start=1):
        print(f"  [{i:>{width}}] {option}")
    print("─" * 50)


def ask_choice(prompt: str, count: int) -> int:
    """Запрашивает номер варианта от 1 до count. Возвращает индекс (с 0)."""
    while True:
        raw = input(f"{prompt} (1-{count}, q — выход): ").strip()
        if raw.lower() in ("q", "й", "quit", "exit"):
            print("Выход.")
            sys.exit(0)
        if raw.isdigit() and 1 <= int(raw) <= count:
            return int(raw) - 1
        print(f"  ✗ Введите число от 1 до {count}.")


# ─────────────────────────── Чтение данных ───────────────────────────

def norm_key(key: str) -> str:
    return re.sub(r"[\s_\-#№]", "", str(key)).lower()


def find_key(record: dict, candidates: tuple[str, ...]) -> str | None:
    normalized = {norm_key(k): k for k in record}
    for cand in candidates:
        if cand in normalized:
            return normalized[cand]
    return None


def to_number(value):
    """Пытается привести строку к числу ("1 200,50" -> 1200.5). Иначе возвращает как есть."""
    if isinstance(value, (int, float)) or value is None:
        return value
    text = str(value).strip().replace(" ", "").replace(" ", "").replace(",", ".")
    try:
        num = float(text)
        return int(num) if num.is_integer() and "." not in text else num
    except ValueError:
        return value


def read_csv(path: Path) -> list[dict]:
    # utf-8-sig убирает BOM, который добавляет Excel на Windows.
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        sample = f.read(4096)
        f.seek(0)
        try:
            dialect = csv.Sniffer().sniff(sample, delimiters=",;\t")
        except csv.Error:
            dialect = csv.excel
        return [dict(row) for row in csv.DictReader(f, dialect=dialect)]


def read_json(path: Path) -> list[dict]:
    with path.open("r", encoding="utf-8-sig") as f:
        data = json.load(f)
    if isinstance(data, dict):
        # {"invoices": [...]} или {"INV-1": {...}, "INV-2": {...}}
        lists = [v for v in data.values() if isinstance(v, list)]
        if lists:
            data = lists[0]
        else:
            data = [{"invoice_id": k, **v} for k, v in data.items() if isinstance(v, dict)]
    if not isinstance(data, list):
        raise ValueError("ожидался список записей")
    return [r for r in data if isinstance(r, dict)]


def load_records(path: Path) -> list[dict]:
    if path.suffix.lower() == ".csv":
        return read_csv(path)
    return read_json(path)


def group_invoices(records: list[dict]) -> "OrderedDict[str, dict]":
    """
    Приводит записи к единому виду: {invoice_id: {поля чека..., "items": [...], "total": ...}}.

    Поддерживаются два формата:
      • плоский (CSV): одна строка = одна позиция, строки с одинаковым invoice_id объединяются;
      • вложенный (JSON): у записи есть список позиций в поле items / lines / products.
    """
    invoices: OrderedDict[str, dict] = OrderedDict()
    for record in records:
        id_key = find_key(record, INVOICE_ID_KEYS)
        if id_key is None or record[id_key] in (None, ""):
            continue
        inv_id = str(record[id_key]).strip()
        items_key = find_key(record, ITEMS_KEYS)

        header = {k: v for k, v in record.items() if k not in (id_key, items_key)}
        invoice = invoices.setdefault(inv_id, {"invoice_id": inv_id, "items": []})

        if items_key and isinstance(record[items_key], list):
            invoice.update(header)
            invoice["items"].extend(dict(i) for i in record[items_key] if isinstance(i, dict))
        else:
            # Плоская строка: общие поля (кроме относящихся к позиции) — в шапку чека,
            # строка целиком — в позиции.
            normalized_item_keys = {norm_key(k) for k in ITEM_FIELD_KEYS}
            for k, v in header.items():
                if norm_key(k) not in normalized_item_keys:
                    invoice.setdefault(k, v)
            invoice["items"].append(header)

    for invoice in invoices.values():
        enrich_invoice(invoice)
    return invoices


def enrich_invoice(invoice: dict) -> None:
    """Считает суммы по позициям и итог, если их нет в данных."""
    total = 0.0
    for item in invoice["items"]:
        for k, v in list(item.items()):
            item[k] = to_number(v)
        qty_key = find_key(item, ("quantity", "qty", "количество", "кол-во", "колво"))
        price_key = find_key(item, ("price", "unitprice", "цена"))
        sum_key = find_key(item, ("amount", "sum", "linetotal", "сумма"))
        qty = item.get(qty_key, 1) if qty_key else 1
        price = item.get(price_key) if price_key else None

        # Единые поля, чтобы шаблоны не зависели от названий колонок.
        item.setdefault("name", item.get(find_key(item, ("name", "item", "product", "description",
                                                           "товар", "наименование")) or "", ""))
        item["quantity"] = qty
        item["price"] = price
        if sum_key and isinstance(item[sum_key], (int, float)):
            item["amount"] = item[sum_key]
        elif isinstance(qty, (int, float)) and isinstance(price, (int, float)):
            item["amount"] = round(qty * price, 2)
        else:
            item["amount"] = None
        if isinstance(item["amount"], (int, float)):
            total += item["amount"]

    total_key = find_key(invoice, ("total", "totalamount", "итого"))
    invoice["total"] = to_number(invoice[total_key]) if total_key else round(total, 2)


# ─────────────────────────── PDF ───────────────────────────

def money(value) -> str:
    if isinstance(value, (int, float)):
        return f"{value:,.2f}".replace(",", " ")
    return "" if value is None else str(value)


def ensure_macos_library_path() -> None:
    """
    На macOS python.org-сборка Python не видит библиотеки Homebrew (Pango/GObject/Cairo)
    через стандартный поиск динамических библиотек. WeasyPrint падает с
    'cannot load library libgobject-2.0-0', если не подсказать путь явно.
    """
    if platform.system() != "Darwin":
        return
    for prefix in ("/opt/homebrew", "/usr/local"):
        lib_dir = Path(prefix) / "lib"
        if (lib_dir / "libgobject-2.0.0.dylib").exists():
            existing = os.environ.get("DYLD_FALLBACK_LIBRARY_PATH", "")
            if str(lib_dir) not in existing.split(":"):
                os.environ["DYLD_FALLBACK_LIBRARY_PATH"] = (
                    f"{lib_dir}:{existing}" if existing else str(lib_dir)
                )
            return


def render_pdf(template_path: Path, invoice: dict, source_name: str) -> Path:
    ensure_macos_library_path()
    from weasyprint import CSS, HTML  # импорт здесь, чтобы меню работало даже без GTK/Pango

    env = Environment(
        loader=FileSystemLoader(str(TEMPLATES_DIR)),
        autoescape=select_autoescape(["html", "htm"]),
    )
    env.filters["money"] = money
    html = env.get_template(template_path.name).render(
        invoice=invoice,
        items=invoice["items"],
        source=source_name,
        generated_at=datetime.now().strftime("%d.%m.%Y %H:%M"),
    )

    OUTPUT_DIR.mkdir(exist_ok=True)
    safe_id = re.sub(r"[^\w\-]+", "_", invoice["invoice_id"])
    out_path = OUTPUT_DIR / f"invoice_{safe_id}_{template_path.stem}.pdf"
    HTML(string=html, base_url=str(TEMPLATES_DIR)).write_pdf(
        out_path, stylesheets=[CSS(string=FONT_CSS)]
    )
    return out_path


def open_file(path: Path) -> None:
    system = platform.system()
    try:
        if system == "Windows":
            os.startfile(path)  # type: ignore[attr-defined]
        elif system == "Darwin":
            subprocess.run(["open", str(path)], check=False)
        else:
            subprocess.run(["xdg-open", str(path)], check=False)
    except OSError as e:
        print(f"  Не удалось открыть файл автоматически: {e}")


# ─────────────────────────── main ───────────────────────────

def list_files(directory: Path, suffixes: tuple[str, ...]) -> list[Path]:
    directory.mkdir(exist_ok=True)
    return sorted(p for p in directory.iterdir() if p.is_file() and p.suffix.lower() in suffixes)


def main() -> None:
    if platform.system() == "Windows":
        # Корректный вывод кириллицы и рамок в консоли Windows.
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]

    print_header("Генератор PDF-чеков")

    data_files = list_files(DATA_DIR, (".csv", ".json"))
    templates = list_files(TEMPLATES_DIR, (".html", ".htm"))

    print_menu(f"Файлы с данными ({DATA_DIR.name}/):",
               [f"{p.name}  [{p.suffix[1:].upper()}]" for p in data_files])
    print_menu(f"HTML-шаблоны ({TEMPLATES_DIR.name}/):", [p.name for p in templates])

    if not data_files or not templates:
        print("\nНет файлов данных или шаблонов — добавьте их и запустите снова.")
        sys.exit(1)

    data_path = data_files[ask_choice("\nВыберите файл данных", len(data_files))]
    template_path = templates[ask_choice("Выберите шаблон", len(templates))]

    try:
        invoices = group_invoices(load_records(data_path))
    except (OSError, ValueError, csv.Error) as e:
        print(f"\n✗ Ошибка чтения {data_path.name}: {e}")
        sys.exit(1)

    if not invoices:
        print(f"\n✗ В файле {data_path.name} не найдено чеков (нужна колонка invoice_id).")
        sys.exit(1)

    ids = list(invoices)
    print_menu(
        f"Чеки в {data_path.name}:",
        [f"{inv_id:<14} позиций: {len(invoices[inv_id]['items']):<3} "
         f"итого: {money(invoices[inv_id]['total'])}" for inv_id in ids],
    )
    invoice = invoices[ids[ask_choice("Выберите invoice id", len(ids))]]

    print(f"\nГенерация PDF для чека {invoice['invoice_id']}...")
    try:
        out_path = render_pdf(template_path, invoice, data_path.name)
    except OSError as e:
        print(f"✗ Не удалось загрузить WeasyPrint/системные библиотеки: {e}\n"
              "  См. https://doc.courtbouillon.org/weasyprint/stable/first_steps.html")
        sys.exit(1)

    print(f"✓ PDF сохранён: {out_path}")
    open_file(out_path)


if __name__ == "__main__":
    try:
        main()
    except (KeyboardInterrupt, EOFError):
        print("\nПрервано.")
