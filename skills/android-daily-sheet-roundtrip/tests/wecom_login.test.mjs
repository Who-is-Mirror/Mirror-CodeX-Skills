import test from 'node:test';
import assert from 'node:assert/strict';

import { isWecomHomeUrl, isWecomLoginUrl, isWecomQrFrameUrl } from '../scripts/check_wecom_login.mjs';

test('recognizes only enterprise WeChat document home URLs', () => {
  assert.equal(isWecomHomeUrl('https://doc.weixin.qq.com/home/recent'), true);
  assert.equal(isWecomHomeUrl('https://doc.weixin.qq.com/home/favorites'), true);
  assert.equal(isWecomHomeUrl('https://doc.weixin.qq.com/sheet/example'), false);
  assert.equal(isWecomHomeUrl('https://example.com/home/recent'), false);
});

test('recognizes the enterprise WeChat login page and QR frame', () => {
  assert.equal(isWecomLoginUrl('https://doc.weixin.qq.com/scenario/login.html?success_jump_url=x'), true);
  assert.equal(isWecomLoginUrl('https://doc.weixin.qq.com/home/recent'), false);
  assert.equal(isWecomQrFrameUrl('https://login.work.weixin.qq.com/wwlogin/partner/login/'), true);
  assert.equal(isWecomQrFrameUrl('https://doc.weixin.qq.com/tim/docs/components/WeworkLogin.html'), false);
});
