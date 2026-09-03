export const DATE_TAB_PATTERN = /^\d{4}-\d{2}-\d{2}$/;

export function isValidDateTab(value) {
  const text = String(value ?? '').trim();
  if (!DATE_TAB_PATTERN.test(text)) return false;
  const [year, month, day] = text.split('-').map(Number);
  const parsed = new Date(Date.UTC(year, month - 1, day));
  return parsed.getUTCFullYear() === year
    && parsed.getUTCMonth() === month - 1
    && parsed.getUTCDate() === day;
}

export function planDateTabRetention(tabNames, targetDate, {
  maxTabs = 5,
  replaceExisting = false,
  allowBlankBootstrap = false,
} = {}) {
  if (!Number.isInteger(maxTabs) || maxTabs < 1) throw new Error('maxTabs 必须是正整数');
  if (!isValidDateTab(targetDate)) throw new Error(`目标页签不是有效日期: ${targetDate}`);
  if (!Array.isArray(tabNames) || tabNames.length === 0) throw new Error('工作簿必须至少有一个可见工作表');
  const names = tabNames.map((name) => String(name ?? '').trim());
  if (new Set(names).size !== names.length) throw new Error('工作簿含重复页签名称，拒绝管理');

  const nonDateTabs = names.filter((name) => !isValidDateTab(name));
  if (nonDateTabs.length) {
    if (allowBlankBootstrap && names.length === 1) {
      return {
        action: 'bootstrap',
        bootstrapTab: names[0],
        deleteTabs: [],
        createTab: false,
        targetDate,
        desiredOrder: [targetDate],
      };
    }
    throw new Error(`受管工作簿含非日期页签: ${nonDateTabs.join(', ')}`);
  }

  const targetExists = names.includes(targetDate);
  if (targetExists && !replaceExisting) throw new Error(`日期页签已存在，需显式允许重新生成: ${targetDate}`);
  const retained = names.filter((name) => name !== targetDate).sort().reverse();
  const deleteTabs = targetExists ? [targetDate] : [];
  while (retained.length >= maxTabs) deleteTabs.push(retained.pop());
  const desiredOrder = [...retained, targetDate].sort().reverse();
  return {
    action: targetExists ? 'replace-existing' : deleteTabs.length ? 'rotate-oldest' : 'append',
    bootstrapTab: null,
    deleteTabs,
    createTab: true,
    targetDate,
    desiredOrder,
  };
}

