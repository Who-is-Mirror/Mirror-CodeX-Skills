#!/usr/bin/env node

import { COLUMNS, normalizeCell } from './sheet_template.mjs';

const FOOTER_LABELS = new Set(['重点说明', '依赖 / 需协调', '明日计划']);
const FIELD_LABELS = new Set(['今日主题', '当前结果', '做了什么', '怎么做的', '结果']);

function columnNumber(column) {
  let value = 0;
  for (const character of column) value = value * 26 + character.charCodeAt(0) - 64;
  return value;
}

function parseRef(ref) {
  const match = String(ref).match(/^([A-Z]+)(\d+)$/);
  if (!match) throw new Error(`非法单元格引用: ${ref}`);
  return { column: columnNumber(match[1]), row: Number(match[2]) };
}

export function refInRange(ref, range) {
  const [startText, endText = startText] = String(range).split(':');
  const cell = parseRef(ref);
  const start = parseRef(startText);
  const end = parseRef(endText);
  return cell.column >= start.column && cell.column <= end.column && cell.row >= start.row && cell.row <= end.row;
}

export function deriveSheetLayout(template, values) {
  if (!Array.isArray(values) || values.length < 2) throw new Error('表格 values 至少需要表头和一行内容');
  const lastRow = values.length;
  const footerRows = [];
  const separatorRows = [];
  const scopeRows = [];
  const fieldRows = [];
  for (let index = 1; index < values.length; index += 1) {
    const row = index + 1;
    const normalized = values[index].map(normalizeCell);
    if (normalized.every((value) => !value)) separatorRows.push(row);
    if (normalized[2] === '今日概况') scopeRows.push(row);
    if (FOOTER_LABELS.has(normalized[2])) footerRows.push(row);
    if (FIELD_LABELS.has(normalized[3])) fieldRows.push(row);
  }
  const mergeRanges = footerRows.map((row) => `D${row}:F${row}`);
  return {
    ...template.layout,
    header_center_range: 'A1:F1',
    body_left_range: `A2:F${lastRow}`,
    footer_rows: footerRows,
    separator_rows: separatorRows,
    merge_ranges: mergeRanges,
    wrap_ranges: [`B2:B${lastRow}`, `C2:C${lastRow}`, `E2:E${lastRow}`, ...mergeRanges],
    bold_ranges: ['B1:F1', ...scopeRows.map((row) => `C${row}`), ...fieldRows.map((row) => `D${row}`), ...footerRows.map((row) => `C${row}`)]
  };
}

function escapeHtml(value) {
  return String(value ?? '')
    .replaceAll('&', '&amp;')
    .replaceAll('<', '&lt;')
    .replaceAll('>', '&gt;')
    .replaceAll('"', '&quot;')
    .replace(/\r\n?/g, '\n')
    .replaceAll('\n', '<br>');
}

function quoteTsv(value) {
  const text = String(value ?? '').replace(/\r\n?/g, '\n');
  return /[\t\n"]/.test(text) ? `"${text.replaceAll('"', '""')}"` : text;
}

export function buildDualClipboardPayload(template, values, rowHeights, layout = deriveSheetLayout(template, values)) {
  const footer = new Set(layout.footer_rows);
  const widths = layout.column_widths;
  const plainRows = [];
  const htmlRows = [];
  for (let rowIndex = 0; rowIndex < values.length; rowIndex += 1) {
    const row = rowIndex + 1;
    const plainValues = values[rowIndex].map((value) => normalizeCell(value));
    if (footer.has(row)) {
      plainValues[3] = plainValues[4];
      plainValues[4] = '';
      plainValues[5] = '';
    }
    plainRows.push(plainValues.map(quoteTsv).join('\t'));

    const cells = [];
    for (let columnIndex = 0; columnIndex < COLUMNS.length; columnIndex += 1) {
      if (footer.has(row) && columnIndex > 3) continue;
      const column = COLUMNS[columnIndex];
      const ref = `${column}${row}`;
      const mergedFooter = footer.has(row) && columnIndex === 3;
      const colspan = mergedFooter ? 3 : 1;
      const value = mergedFooter ? values[rowIndex][4] : values[rowIndex][columnIndex];
      const width = mergedFooter ? widths.D + widths.E + widths.F : widths[column];
      const bold = layout.bold_ranges.some((range) => refInRange(ref, range));
      const wrap = layout.wrap_ranges.some((range) => refInRange(ref, range));
      const style = [
        `width:${width}px`,
        'font-family:Arial,Microsoft YaHei,sans-serif',
        'font-size:14px',
        `font-weight:${bold ? 700 : 400}`,
        `text-align:${row === 1 ? 'center' : 'left'}`,
        'vertical-align:middle',
        'overflow-wrap:break-word',
        `white-space:${wrap ? 'normal' : 'nowrap'}`,
        row === 1 ? `background-color:${layout.header_fill}` : 'background-color:#ffffff'
      ].join(';');
      cells.push(`<td${colspan > 1 ? ` colspan="${colspan}"` : ''} style="${style}">${escapeHtml(value)}</td>`);
    }
    const height = row === 1 ? 24 : rowHeights[row];
    if (!Number.isFinite(height) || height <= 0) throw new Error(`第 ${row} 行缺少合法行高`);
    htmlRows.push(`<tr height="${height}" style="height:${height}px">${cells.join('')}</tr>`);
  }
  const plain = plainRows.join('\n');
  const html = `<html xmlns:x="tencent"><head><meta charset="utf-8"><style>table{border-collapse:collapse;table-layout:fixed}td{padding:0 12px}</style></head><body><!--StartFragment--><table><colgroup>${COLUMNS.map((column) => `<col width="${widths[column]}" style="width:${widths[column]}px">`).join('')}</colgroup>${htmlRows.join('')}</table><!--EndFragment--></body></html>`;
  return { plain, html, layout };
}
