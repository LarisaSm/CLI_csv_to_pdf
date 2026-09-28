# CLI CSV/JSON → PDF

Консольный генератор PDF-чеков: читает данные из `data/` (CSV или JSON),
подставляет выбранный чек (по `invoice_id`) в HTML-шаблон из `templates/`
и сохраняет готовый PDF в `output/`, сразу открывая его.

## Установка

```bash
python3 -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

WeasyPrint рендерит PDF через Pango/Cairo/GObject — это системные библиотеки,
их не поставить через pip.

### macOS

```bash
# Homebrew, если ещё не установлен: https://brew.sh
brew install pango
```

Если Python установлен с python.org (не через `brew install python`), после
`brew install pango` может понадобиться подсказать линковщику путь к
библиотекам — `main.py` делает это автоматически
(`ensure_macos_library_path()` в [main.py](main.py)), вручную ничего делать не нужно.

### Windows

Скачайте и установите GTK3 runtime (содержит Pango/Cairo):
https://github.com/tschoonj/GTK-for-Windows-Runtime-Environment-Installer/releases
— и перезапустите терминал после установки.

Подробности и другие ОС: https://doc.courtbouillon.org/weasyprint/stable/first_steps.html

## Запуск

```bash
python main.py
```

Скрипт покажет пронумерованное меню: файл данных → HTML-шаблон → invoice id,
сгенерирует PDF в `output/` и откроет его в системной программе
(Preview на macOS, программа по умолчанию для PDF на Windows).

## Структура

```
data/       CSV/JSON с чеками (пример: invoices.csv, invoices.json)
templates/  HTML-шаблоны на Jinja2 (пример: invoice.html)
fonts/      DejaVu Sans — поддержка кириллицы в PDF
output/     сюда сохраняются готовые PDF
```

## Формат данных

**CSV** — построчный (плоский) формат: каждая строка — одна позиция,
строки с одинаковым `invoice_id` объединяются в один чек:

```csv
invoice_id,customer,date,name,quantity,price
INV-001,Иван Петров,2026-09-01,Клавиатура,1,4500
```

**JSON** — вложенный формат: у чека есть список позиций в поле `items`:

```json
{"invoices": [
  {"invoice_id": "JSN-100", "customer": "Дмитрий Волков",
   "items": [{"name": "Веб-камера", "quantity": 1, "price": 3200}]}
]}
```

Сумма по позиции (`amount`) и итог чека (`total`) считаются автоматически,
если их нет в исходных данных.

## Свой шаблон

Добавьте `.html`-файл в `templates/` — он появится в меню. В шаблоне доступны:

- `invoice` — словарь чека (`invoice_id`, `total`, произвольные поля шапки);
- `items` — список позиций (`name`, `quantity`, `price`, `amount`);
- `source` — имя исходного файла данных;
- `generated_at` — дата/время генерации;
- фильтр `| money` — форматирование чисел (`12345.6` → `12 345.60`).

Шрифт с кириллицей подключается автоматически ко всем шаблонам
(`FONT_CSS` в [main.py](main.py)), указывать `@font-face` самому не нужно.
