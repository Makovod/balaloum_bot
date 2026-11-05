import re
import pdfplumber
import pandas as pd

def parse_inventory_pdf(pdf_path: str, output_excel_path: str = "Balaloum_Bot_Ready.xlsx"):
    """
    Точный координатный парсер ведомости 1С (PDF) для бота Balaloum.
    Сохраняет 100% точность по 4-й колонке (Итог), чашкам (B, C, D, E) и цветам.
    Формирует готовый ключ photo_tag вида '9362_пудра' для синхронизации с Фотобанком.
    """
    def parse_qty(val_str: str) -> int:
        if not val_str:
            return 0
        s = val_str.replace(" ", "").replace("\xa0", "").replace(",", ".")
        try:
            return int(round(float(s)))
        except ValueError:
            return 0

    all_clustered_rows = []

    with pdfplumber.open(pdf_path) as pdf:
        for page_idx, page in enumerate(pdf.pages):
            words = page.extract_words(keep_blank_chars=True, x_tolerance=3, y_tolerance=2)
            # Группируем слова по строкам (координата top / Y с допуском 3.0 пт)
            lines_by_y = []
            words.sort(key=lambda w: (round(w['top'], 1), w['x0']))
            
            for w in words:
                y = w['top']
                x = w['x0']
                text = w['text'].strip()
                if not text:
                    continue
                if not lines_by_y:
                    lines_by_y.append([w])
                else:
                    prev_y = sum(item['top'] for item in lines_by_y[-1]) / len(lines_by_y[-1])
                    if abs(y - prev_y) <= 3.2:
                        lines_by_y[-1].append(w)
                    else:
                        lines_by_y.append([w])

            for line_words in lines_by_y:
                line_words.sort(key=lambda w: w['x0'])
                # Разделяем на левую текстовую колонку (x0 < 240) и 4 числовые колонки складов
                left_words = [w for w in line_words if w['x0'] < 240]
                col1_words = [w for w in line_words if 245 <= w['x0'] < 325]  # Ангар 278
                col2_words = [w for w in line_words if 325 <= w['x0'] < 400]  # Синяя СКЛАД
                col3_words = [w for w in line_words if 400 <= w['x0'] < 480]  # СКЛАД
                col4_words = [w for w in line_words if w['x0'] >= 480]        # Итог (4-я колонка)

                left_x = round(left_words[0]['x0'], 1) if left_words else None
                left_text = " ".join(w['text'] for w in left_words).strip()

                c1 = parse_qty("".join(w['text'] for w in col1_words))
                c2 = parse_qty("".join(w['text'] for w in col2_words))
                c3 = parse_qty("".join(w['text'] for w in col3_words))
                c4 = parse_qty("".join(w['text'] for w in col4_words))

                all_clustered_rows.append((page_idx + 1, left_x, left_text, c1, c2, c3, c4))

    brand = ""
    category = ""
    model_raw = ""
    records = []

    cup_map = {'В': 'B', 'B': 'B', 'С': 'C', 'C': 'C', 'D': 'D', 'Е': 'E', 'E': 'E'}

    for page_num, x_val, label, c1, c2, c3, c4 in all_clustered_rows:
        # Пропускаем шапку и общий итог документа (X ~ 37.9)
        if x_val is not None and x_val < 41.0:
            continue
        # Уровень Бренда (X ~ 44.8: Balaloum, Ang Gee, Аксесуары и т.д.)
        elif x_val is not None and 41.0 <= x_val < 48.0:
            brand = label.rstrip(" ,")
            category = "Основное"
        # Уровень Модели (заканчивается на 'шт')
        elif label.endswith("шт"):
            model_raw = label
        # Уровень Категории (X ~ 51.6: Бюсты, Комплекты, Трусы, Боди, Подвязка)
        elif x_val is not None and 48.0 <= x_val < 55.0 and not label.endswith("шт"):
            category = label.rstrip(" ,")
        # Уровень Цвета / конкретной позиции (X ~ 58.4 или 65.3, либо пустая строка без цвета)
        else:
            if not model_raw or c4 == 0:
                continue
            color_raw = label.strip() if label else "стандарт"

            # Очищаем название модели от ', шт' и года '(23 г)', '(26г)'
            clean_model = re.sub(r'\s*,\s*шт$', '', model_raw).strip()
            clean_no_year = re.sub(r'\(\s*20\d{2}\s*г?\s*\)|\(\s*\d{2}\s*г?\s*\)', '', clean_model).strip()
            clean_no_year = re.sub(r'\s+', ' ', clean_no_year)

            # Определяем чашку (B, C, D, E)
            cup = "-"
            actual_color = color_raw
            m_col_cup = re.match(r'^([BВCСDЕE])\s*,\s*(.+)$', color_raw)
            if m_col_cup:
                cup = cup_map.get(m_col_cup.group(1), m_col_cup.group(1))
                actual_color = m_col_cup.group(2).strip()
            else:
                m_cup = re.search(r'(?:\d)\s*([BВCСDЕE])(?:\s+бюсты|\s*$)', clean_no_year)
                if m_cup:
                    cup = cup_map.get(m_cup.group(1), m_cup.group(1))

            # Извлекаем чистый цифровой артикул (например, 9362, 9401, 9063)
            m_art = re.search(r'(\d{3,6}(?:/\d)?)', clean_no_year)
            article = m_art.group(1) if m_art else clean_no_year

            # Формируем точный тег для Фотобанка (например: 9362_пудра)
            if actual_color and actual_color != "стандарт":
                photo_tag = f"{article}_{actual_color.lower()}"
            else:
                photo_tag = f"{article}"

            records.append({
                "Бренд": brand,
                "Категория": category,
                "Артикул": article,
                "Чашка": cup,
                "Цвет": actual_color,
                "Тег_Фотобанка": photo_tag,
                "Модель_Полная": clean_no_year,
                "Ангар_278": c1,
                "Синяя_СКЛАД": c2,
                "СКЛАД": c3,
                "Итог_Остаток": c4
            })

    df_detailed = pd.DataFrame(records)

    # Сводная таблица для Автопостинга: группируем по (Артикул + Цвет), собирая все чашки в одну строку
    grouped_rows = []
    for (brd, cat, art, col, ptag), grp in df_detailed.groupby(
        ["Бренд", "Категория", "Артикул", "Цвет", "Тег_Фотобанка"], as_index=False
    ):
        total_qty = int(grp["Итог_Остаток"].sum())
        cups_breakdown = ", ".join(
            f"{row['Чашка']} ({row['Итог_Остаток']} шт.)" if row['Чашка'] != "-" else f"{row['Итог_Остаток']} шт."
            for _, row in grp.sort_values("Чашка").iterrows()
        )
        grouped_rows.append({
            "Бренд": brd,
            "Категория": cat,
            "Артикул": art,
            "Цвет": col,
            "Тег_Фотобанка": ptag,
            "Разбивка_по_чашкам": cups_breakdown,
            "Общий_Остаток": total_qty
        })

    df_posting = pd.DataFrame(grouped_rows).sort_values(by="Общий_Остаток", ascending=False)

    # Сохраняем в чистый Excel на 2 листа: 1) Готово для Автопоста, 2) Полная детализация по чашкам
    with pd.ExcelWriter(output_excel_path, engine="openpyxl") as writer:
        df_posting.to_excel(writer, sheet_name="Для_Автопоста_По_Цветам", index=False)
        df_detailed.to_excel(writer, sheet_name="Детально_По_Чашкам", index=False)

    return df_posting, df_detailed
