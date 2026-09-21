import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { spawnSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';

import {
  FACTS_SCHEMA,
  convertDailySheet,
  normalizeOfflineCells,
} from '../scripts/daily_sheet_to_facts.mjs';

const REPORT_DATE = '2026-09-01';
const TODAY = '2026-09-01';
const TEST_DIR = dirname(fileURLToPath(import.meta.url));
const ROWS_SCRIPT = join(TEST_DIR, '..', 'scripts', 'facts_to_sheet_rows.py');

function convert(rows, options = {}) {
  return convertDailySheet({ rows }, { reportDate: REPORT_DATE, today: TODAY, ...options });
}

function header() {
  return ['项目 / 客户', '类型 / 内容', '任务', '分项', '内容', '状态'];
}

function appRows({ overviewStatus = '', resultStatus = '已完成', omitHow = false } = {}) {
  const rows = [
    header(),
    ['TVE1215M / 浪潮 → 杭研中屏', 'App / FactoryTestNew', '今日概况', '今日主题', 'FactoryTestNew 仓库整理', overviewStatus],
    ['', '', '', '当前结果', '提交边界已经核对完成', ''],
    ['', '', '1. 仓库清理与边界梳理', '做了什么', '- 清理无用材料\n- 保留有效源码', ''],
  ];
  if (!omitHow) rows.push(['', '', '', '怎么做的', '- 核对 Git 状态\n- 复查文件范围', '']);
  rows.push(['', '', '', '结果', '形成干净提交边界', resultStatus]);
  rows.push(['', '', '重点说明', '- 功能提交边界清晰\n- 未改写原始事实', '', '']);
  rows.push(['', '', '依赖 / 需协调', '验证环境', '- 等待兼容 SDK 环境', '']);
  rows.push(['', '', '明日计划', '计划', '- 补充完整构建\n- 执行设备验证', '']);
  return rows;
}

function otherRows() {
  return [
    header(),
    ['Other', '团队工具维护', '今日概况', '今日主题', '维护日报转换工具', ''],
    ['', '', '', '当前结果', '离线转换链路可验证', ''],
    ['', '', '1、实现离线解析', '做了什么', '实现 A:F 单元格解析', ''],
    ['', '', '', '怎么做的', '- 使用确定性输入\n保留续行文字', ''],
    ['', '', '', '结果', '- 离线测试通过', '待验证'],
    ['', '', '重点说明', '无。', '', ''],
    ['', '', '依赖 / 需协调', '', '无', ''],
    ['', '', '明日计划', '', '', '无。'],
  ];
}

function documentRows() {
  return [
    header(),
    ['Other', 'Doc / 日报闭环设计文档', '今日概况', '今日主题', '- 完善日报闭环文档', ''],
    ['', '', '', '当前结果', '- 两阶段边界已写清', ''],
    ['', '', '1. 补齐流程说明', '做了什么', '- 增加 Stage A/B 门禁', ''],
    ['', '', '', '怎么做的', '- 对照当前 facts 合约', ''],
    ['', '', '', '结果', '- 文档可供评审', '已完成'],
    ['', '', '重点说明', '', '无。', ''],
    ['', '', '依赖 / 需协调', '', '无。', ''],
    ['', '', '明日计划', '', '- 复核命令示例', ''],
  ];
}

test('App 项目转换客户链、任务、重点、依赖和项目明日计划', () => {
  const facts = convert(appRows());

  assert.equal(facts.schema, FACTS_SCHEMA);
  assert.equal(facts.report_date, REPORT_DATE);
  assert.deepEqual(facts.documents, []);
  assert.deepEqual(facts.standalone_work, []);
  assert.equal(facts.projects.length, 1);
  assert.deepEqual(facts.projects[0], {
    project: 'TVE1215M',
    customer: '浪潮',
    downstream_customer: '杭研中屏',
    work_type: 'App',
    app_name: 'FactoryTestNew',
    today_topic: 'FactoryTestNew 仓库整理',
    current_result: '提交边界已经核对完成',
    work_items: [{
      name: '仓库清理与边界梳理',
      did: ['清理无用材料', '保留有效源码'],
      how: ['核对 Git 状态', '复查文件范围'],
      result: '形成干净提交边界',
      status: '已完成',
    }],
    key_points: ['功能提交边界清晰', '未改写原始事实'],
    dependencies: ['等待兼容 SDK 环境'],
  });
  assert.deepEqual(facts.tomorrow_plan, {
    projects: [{
      project: 'TVE1215M',
      customer: '浪潮',
      downstream_customer: '杭研中屏',
      work_type: 'App',
      app_name: 'FactoryTestNew',
      plan_items: ['补充完整构建', '执行设备验证'],
    }],
    documents: [],
    standalone_work: [],
  });
  assert.equal('status' in facts.projects[0], false);
  assert.equal('status' in facts.tomorrow_plan.projects[0], false);
});

test('接受线上模板实际使用的“类型”表头', () => {
  const rows = appRows();
  rows[0][1] = '类型';
  const facts = convert(rows);
  assert.equal(facts.projects[0].project, 'TVE1215M');
});

test('A=Other 映射 standalone_work，并把“无/无。”转为空数组', () => {
  const facts = convert(otherRows());

  assert.deepEqual(facts.projects, []);
  assert.deepEqual(facts.standalone_work, [{
    work_type: 'Other',
    work_name: '团队工具维护',
    today_topic: '维护日报转换工具',
    current_result: '离线转换链路可验证',
    work_items: [{
      name: '实现离线解析',
      did: ['实现 A:F 单元格解析'],
      how: ['使用确定性输入\n保留续行文字'],
      result: '离线测试通过',
      status: '待验证',
    }],
    key_points: [],
    dependencies: [],
  }]);
  assert.deepEqual(facts.tomorrow_plan.standalone_work, []);
});

test('拒绝今日概况行携带状态', () => {
  assert.throws(
    () => convert(appRows({ overviewStatus: '已完成' })),
    /今日概况不允许填写状态/,
  );
});

test('拒绝任务结果的非法状态', () => {
  assert.throws(
    () => convert(appRows({ resultStatus: '完成' })),
    /非法状态“完成”/,
  );
});

test('拒绝任务结果缺失状态', () => {
  assert.throws(
    () => convert(appRows({ resultStatus: '' })),
    /F 状态必填/,
  );
});

test('拒绝任务缺少怎么做的字段', () => {
  assert.throws(
    () => convert(appRows({ omitHow: true })),
    /work_items\[0\]\.how 必须是非空数组/,
  );
});

test('拒绝未来日期', () => {
  assert.throws(
    () => convertDailySheet({ rows: otherRows() }, { reportDate: '2026-09-02', today: TODAY }),
    /未来日期被拒绝/,
  );
});

test('拒绝 GMS 和身份歧义，不猜测专属周期或客户链', () => {
  const gms = appRows();
  gms[1][1] = 'GMS';
  assert.throws(() => convert(gms), /GMS 需要专属周期事实/);

  const ambiguous = appRows();
  ambiguous[1][0] = 'TVE1215M / 浪潮 → 杭研中屏 → 最终客户';
  assert.throws(() => convert(ambiguous), /客户链.*含糊/);
});

test('离线地址映射和 address 数组均保留 innerText 换行', () => {
  const mapped = normalizeOfflineCells({ cells: {
    A2: { innerText: 'Other' },
    B2: { innerText: '工具维护' },
    E2: { innerText: '- 第一项\n- 第二项' },
  } });
  assert.equal(mapped[0].rowNumber, 2);
  assert.equal(mapped[0].E, '- 第一项\n- 第二项');

  const listed = normalizeOfflineCells([
    { address: 'A3', innerText: 'Other' },
    { address: 'B3', value: '工具维护' },
  ]);
  assert.equal(listed[0].A, 'Other');
  assert.equal(listed[0].B, '工具维护');
});

test('non-project Doc 映射 documents 与独立明日计划', () => {
  const facts = convert(documentRows());
  assert.deepEqual(facts.projects, []);
  assert.deepEqual(facts.standalone_work, []);
  assert.deepEqual(facts.documents, [{
    work_type: 'Doc',
    document_name: '日报闭环设计文档',
    today_topic: '完善日报闭环文档',
    current_result: '两阶段边界已写清',
    work_items: [{
      name: '补齐流程说明',
      did: ['增加 Stage A/B 门禁'],
      how: ['对照当前 facts 合约'],
      result: '文档可供评审',
      status: '已完成',
    }],
    key_points: [],
    dependencies: [],
  }]);
  assert.deepEqual(facts.tomorrow_plan.documents, [{
    work_type: 'Doc',
    document_name: '日报闭环设计文档',
    plan_items: ['复核命令示例'],
  }]);
});

test('facts_to_sheet_rows → daily_sheet_to_facts 离线 roundtrip 确定且语义一致', () => {
  const facts = {
    schema: FACTS_SCHEMA,
    report_date: REPORT_DATE,
    projects: [],
    documents: [{
      work_type: 'Doc',
      document_name: '日报闭环设计文档',
      today_topic: '完善日报闭环文档',
      current_result: '两阶段边界已写清',
      work_items: [{
        name: '补齐流程说明',
        did: ['增加 Stage A/B 门禁'],
        how: ['对照当前 facts 合约'],
        result: '文档可供评审',
        status: '已完成',
      }],
      key_points: ['Stage A 写表后停止'],
      dependencies: [],
    }],
    standalone_work: [],
    tomorrow_plan: {
      projects: [],
      documents: [{
        work_type: 'Doc',
        document_name: '日报闭环设计文档',
        plan_items: ['复核命令示例'],
      }],
      standalone_work: [],
    },
  };
  const temporary = mkdtempSync(join(TEST_DIR, '.tmp-roundtrip-'));
  const input = join(temporary, 'facts.json');
  const output = join(temporary, 'rows.json');
  try {
    writeFileSync(input, `${JSON.stringify(facts)}\n`, 'utf8');
    const run = spawnSync('python3', [ROWS_SCRIPT, '--input', input, '--output', output], { encoding: 'utf8' });
    assert.equal(run.status, 0, run.stderr || run.stdout);
    const rows = JSON.parse(readFileSync(output, 'utf8'));
    const first = convertDailySheet(rows, { reportDate: REPORT_DATE, today: TODAY });
    const second = convertDailySheet(rows, { reportDate: REPORT_DATE, today: TODAY });
    assert.deepEqual(first, facts);
    assert.deepEqual(second, first);
  } finally {
    rmSync(temporary, { recursive: true, force: true });
  }
});
