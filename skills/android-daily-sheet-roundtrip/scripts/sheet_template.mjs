#!/usr/bin/env node

export const TEMPLATE_SCHEMA = 'android-daily-sheet-template-v1';
export const ROWS_SCHEMA = 'daily-sheet-rows-v1';
export const COLUMNS = ['A', 'B', 'C', 'D', 'E', 'F'];

export function normalizeCell(value) {
  return String(value ?? '').replace(/\r\n?/g, '\n').replace(/\n+/g, '\n').trim();
}

export function validateTemplate(template) {
  if (!template || template.schema !== TEMPLATE_SCHEMA) throw new Error(`模板 schema 必须为 ${TEMPLATE_SCHEMA}`);
  if (!Array.isArray(template.values) || template.values.length < 2) throw new Error('模板 values 必须至少包含表头和一行内容');
  template.values.forEach((row, index) => {
    if (!Array.isArray(row) || row.length !== COLUMNS.length) throw new Error(`模板第 ${index + 1} 行必须恰有 6 列`);
  });
  const expectedHeader = ['项目 / 客户', '类型', '任务', '分项', '内容', '状态'];
  if (JSON.stringify(template.values[0]) !== JSON.stringify(expectedHeader)) throw new Error('模板表头不符合确认的 A:F 合同');
  const layout = template.layout;
  if (!layout || !layout.column_widths || !layout.row_height) throw new Error('模板缺少 layout 配置');
  for (const column of COLUMNS) {
    if (!Number.isFinite(layout.column_widths[column]) || layout.column_widths[column] <= 0) throw new Error(`列 ${column} 宽度非法`);
  }
  for (const row of layout.footer_rows ?? []) {
    if (normalizeCell(template.values[row - 1]?.[3])) throw new Error(`footer 第 ${row} 行的 D 必须为空，内容应在 E 供渲染器移入合并锚点`);
  }
  const combined = template.values.flat().map(normalizeCell).join('\n');
  const leaked = (template.forbidden_real_markers ?? []).filter((token) => combined.includes(token));
  if (leaked.length) throw new Error(`模板包含真实数据标记: ${leaked.join(', ')}`);
  return template;
}

export function valuesFromInput(payload, template) {
  if (!payload) return template.values;
  if (payload.schema === TEMPLATE_SCHEMA) return validateTemplate(payload).values;
  if (payload.schema === ROWS_SCHEMA && Array.isArray(payload.grid_data?.values)) return payload.grid_data.values;
  if (Array.isArray(payload.values)) return payload.values;
  throw new Error(`不支持的表格输入 schema: ${payload.schema ?? 'missing'}`);
}

export function expectedSheetCells(values, footerRows) {
  const footer = new Set(footerRows);
  const cells = new Map();
  for (let rowIndex = 0; rowIndex < values.length; rowIndex += 1) {
    const row = rowIndex + 1;
    for (let columnIndex = 0; columnIndex < COLUMNS.length; columnIndex += 1) {
      if (footer.has(row) && columnIndex === 3) continue;
      if (footer.has(row) && columnIndex >= 4) {
        if (columnIndex === 4) cells.set(`D${row}`, normalizeCell(values[rowIndex][columnIndex]));
        continue;
      }
      cells.set(`${COLUMNS[columnIndex]}${row}`, normalizeCell(values[rowIndex][columnIndex]));
    }
  }
  return cells;
}

export function calculateRowHeights(template, values, measureText) {
  const { column_widths: widths, footer_rows: footerRows, separator_rows: separatorRows, row_height: rules } = template.layout;
  const footer = new Set(footerRows);
  const separator = new Set(separatorRows);
  const roundTo = rules.round_to || 1;
  const result = {};

  function wrappedLines(raw, availableWidth) {
    let total = 0;
    for (const logicalLine of String(raw ?? '').split('\n')) {
      if (!logicalLine) {
        total += 1;
        continue;
      }
      let lineWidth = 0;
      let lines = 1;
      for (const character of logicalLine) {
        const width = Number(measureText(character));
        if (!Number.isFinite(width) || width < 0) throw new Error('measureText 必须返回非负有限数');
        if (lineWidth > 0 && lineWidth + width > availableWidth) {
          lines += 1;
          lineWidth = width;
        } else {
          lineWidth += width;
        }
      }
      total += lines;
    }
    return Math.max(1, total);
  }

  for (let index = 1; index < values.length; index += 1) {
    const row = index + 1;
    if (separator.has(row)) {
      result[row] = rules.separator;
      continue;
    }
    let maxLines = 1;
    for (let columnIndex = 0; columnIndex < COLUMNS.length; columnIndex += 1) {
      const value = normalizeCell(values[index][columnIndex]);
      if (!value) continue;
      let width = widths[COLUMNS[columnIndex]];
      if (footer.has(row) && columnIndex === 4) width = widths.D + widths.E + widths.F;
      maxLines = Math.max(maxLines, wrappedLines(value, width - rules.horizontal_padding));
    }
    const raw = maxLines * rules.line_height + rules.vertical_padding;
    const rounded = Math.ceil(raw / roundTo) * roundTo;
    result[row] = Math.min(rules.maximum, Math.max(rules.minimum, rounded));
  }
  return result;
}
