// Run with Playwright installed: node 原型集成回归.cjs [path/to/HTML] [artifact-directory]
// PLAYWRIGHT_MODULE can point at an existing Playwright installation.
const {chromium} = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
const path = require('node:path');
const os = require('node:os');
const crypto = require('node:crypto');
const {pathToFileURL} = require('node:url');

const html = path.resolve(process.argv[2] || path.join(__dirname, '..', '雅典娜美化方案.html'));
const output = path.resolve(process.argv[3] || path.join(os.tmpdir(), 'athena-prototype-qa'));
const checks = [], errors = [], externalRequests = [];
let browser;
async function check(name, run) {
  try {await run();checks.push({name, passed:true});console.log('PASS', name);}
  catch (error) {checks.push({name, passed:false, error:error.message});console.error('FAIL', name, error.message);throw error;}
}
async function settled(page) {
  await page.waitForFunction(() => !document.querySelector('.panel.in-transit'));
  await page.waitForTimeout(80);
}
async function click(page, selector) {await page.locator(selector).filter({visible:true}).click();await settled(page);}
async function tab(page, name) {await click(page, '.bottom-nav [data-page="' + name + '"]');}
async function back(page) {await click(page, '#header-back');}
async function text(page, selector) {return page.locator(selector).textContent();}
async function screenshot(page, name) {
  await page.waitForFunction(() => !document.querySelector('#toast').classList.contains('visible'));
  await page.locator('.panel:not([hidden])').evaluate(node => node.scrollTop = 0);
  await page.screenshot({path:path.join(output, name)});
}
async function newPage(width = 390, reduced = true) {
  const context = await browser.newContext({viewport:{width, height:844}, acceptDownloads:true});
  const page = await context.newPage();
  page.on('pageerror', error => errors.push(error.message));
  page.on('request', request => {if (/^https?:/.test(request.url())) externalRequests.push(request.url());});
  await page.addInitScript(reduced => {
    localStorage.removeItem('eduagent-design-theme');
    localStorage.setItem('eduagent-design-reduce-motion', String(reduced));
  }, reduced);
  await page.goto(pathToFileURL(html).href);
  await settled(page);
  return {page, context};
}
async function noOverflow(page) {
  const result = await page.evaluate(() => {
    const selectors = ['body', '#stage', '.main', '.product-header', '.panel:not([hidden])', '.profile-shortcuts', '.card-ai-form'];
    return selectors.flatMap(selector => [...document.querySelectorAll(selector)]).filter(node => node.getClientRects().length && node.scrollWidth > node.clientWidth + 2).map(node => ({tag:node.id || node.className || node.tagName, width:node.clientWidth, scroll:node.scrollWidth}));
  });
  assert.deepEqual(result, []);
}
async function run() {
  await fs.mkdir(output, {recursive:true});
  browser = await chromium.launch({headless:true, args:['--no-sandbox']});
  const {page, context} = await newPage();
  const baseHistory = 7;
  await check('初始化、唯一 ID、本地插画与四项导航', async () => {
    assert.deepEqual(errors, []);
    assert.equal(await page.locator('.bottom-nav button').count(), 4);
    const duplicates = await page.evaluate(() => [...document.querySelectorAll('[id]')].map(node => node.id).filter((id,index,all) => all.indexOf(id) !== index));
    assert.deepEqual(duplicates, []);
    assert(await page.locator('.hero-art').evaluate(image => image.complete && image.naturalWidth > 0));
    await noOverflow(page);
  });
  await check('模拟登录、个人资料修改和侧栏身份同步', async () => {
    await tab(page, 'profile');await click(page, '#profile-avatar');
    await page.locator('#demo-name').fill('知夏');await click(page, '#demo-login-form button');
    assert.equal(await text(page, '#profile-name'), '知夏');
    await click(page, '#profile-auth-action');await page.locator('#account-name').fill('知夏同学');
    await page.locator('#account-stage').selectOption('高中');await click(page, '#account-form button');
    assert.equal(await text(page, '#profile-name'), '知夏同学');
    assert((await text(page, '#profile-identity-note')).includes('高中'));
    assert.equal(await text(page, '#drawer-name'), '知夏同学');
  });
  await check('会员权益页与普通／会员预览切换', async () => {
    await click(page, '#member-card');await click(page, '#member-preview');
    assert.equal(await page.locator('#member-preview').getAttribute('aria-pressed'), 'true');
    await back(page);assert.equal(await text(page, '.member-badge'), '会员体验 · DEMO');
    await click(page, '#member-card');await click(page, '#member-preview');await back(page);
    assert.equal(await text(page, '.member-badge'), '权益预览');
  });
  const bookTitle = '<img src=x onerror=window.__injected=1>';
  await check('教辅书架、添加、纯文本标题与内容预览', async () => {
    await click(page, '[data-profile-view="library"]');
    assert.equal(await page.locator('#book-library [data-book]').count(), 3);
    await click(page, '[data-profile-view="bookadd"]');
    await page.locator('#book-title').fill(bookTitle);await page.locator('#book-subject').selectOption('其他');
    await click(page, '#book-add-form button');
    assert.equal(await page.locator('#book-library [data-book]').count(), 4);
    await click(page, '#book-library [data-book="book-1"]');
    assert.equal(await text(page, '#book-detail-name'), bookTitle);
    assert.equal(await page.locator('#book-detail-name img').count(), 0);
    assert.equal(await page.evaluate(() => window.__injected), undefined);
    await click(page, '#book-back');await back(page);
    assert.equal(await page.locator('#home-book-shelf [data-book]').count(), 4);
    await screenshot(page, '个人空间-390.png');
  });
  const question1 = '第一问：为什么要先看系数？', question2 = '第二问：可以再举一个例子吗？';
  await check('新会话、多轮提问、回答收藏和首页统计联动', async () => {
    await tab(page, 'chat');await click(page, '.mobile-new');
    await page.locator('#chat-input').fill(question1);await click(page, '#chat-form [type="submit"]');
    await page.locator('#chat-input').fill(question2);await click(page, '#chat-form [type="submit"]');
    assert.equal(await page.locator('#demo-messages .demo-exchange').count(), 2);
    await click(page, '[data-chat-save="local-1"] >> nth=1');
    await tab(page, 'profile');
    assert.equal(await text(page, '#history-count'), String(baseHistory + 1));
    assert.equal(await text(page, '#saved-count'), '3');
    assert.equal(await text(page, '#recent-title'), question1);
  });
  await check('历史搜索、恢复完整多轮会话、返回保留搜索状态', async () => {
    await click(page, '.profile-shortcuts [data-profile-view="history"]');
    await page.locator('#history-search').fill('第一问');
    assert.equal(await page.locator('#history-list .history-entry').count(), 1);
    await click(page, '#history-list [data-history-open="local-1"]');
    assert.equal(await page.locator('#demo-messages .demo-exchange').count(), 2);
    assert((await text(page, '#demo-messages')).includes(question2));
    await tab(page, 'profile');
    assert.equal(await page.locator('#history-search').inputValue(), '第一问');
    assert.equal(await text(page, '#page-title'), '历史记录');
    await click(page, '#history-list [data-history-save="local-1"]');
    assert.equal(await text(page, '#saved-count'), '2');
    await page.locator('#history-search').fill('不存在的记录');
    await click(page, '[data-empty-action="history"]');
    assert.equal(await page.locator('#history-search').inputValue(), '');
    await back(page);
  });
  await check('聊天侧栏搜索、分组与会话恢复', async () => {
    await tab(page, 'chat');await click(page, '#drawer-button');
    await page.locator('#drawer-search').fill('第一问');
    assert.equal(await page.locator('#drawer-list .drawer-item').count(), 1);
    assert.equal(await text(page, '#drawer-list .drawer-group'), '今天');
    await click(page, '#drawer-list [data-conversation="local-1"]');
    assert.equal(await page.locator('#demo-messages .demo-exchange').count(), 2);
    assert.equal(await page.locator('.main').getAttribute('inert'), null);
  });
  await check('新对话撤销恢复原会话与草稿', async () => {
    await page.locator('#chat-input').fill('尚未发送的草稿');await click(page, '.mobile-new');
    assert.equal(await page.locator('#demo-messages .demo-exchange').count(), 0);
    await click(page, '.toast-action');
    assert.equal(await page.locator('#demo-messages .demo-exchange').count(), 2);
    assert.equal(await page.locator('#chat-input').inputValue(), '尚未发送的草稿');
  });
  await check('聊天模块历史入口、来源 Tab、搜索空态与恢复', async () => {
    await click(page, '#module-history-open');
    assert.equal(await text(page, '#page-title'), '聊天历史');
    assert.equal(await page.locator('.bottom-nav [aria-current="page"]').getAttribute('data-page'), 'chat');
    await page.locator('#module-history-search').fill('无结果');await click(page, '[data-module-empty="clear"]');
    await click(page, '[data-module-record="local-1"]');
    assert.equal(await text(page, '#page-title'), '对话');
    assert.equal(await page.locator('#demo-messages .demo-exchange').count(), 2);
  });
  await check('错题操作自动记录、返回筛选与历史定位', async () => {
    await tab(page, 'mistakes');await click(page, '#mistake-filters [data-subject="英语"]');
    await click(page, '#mistake-list [data-practice="现在完成时"]');await back(page);
    assert.equal(await page.locator('#mistake-filters [aria-pressed="true"]').textContent(), '英语');
    await click(page, '#module-history-open');
    assert.equal(await text(page, '#page-title'), '错题历史');
    assert((await text(page, '#module-history-list')).includes('变式练习'));
    await click(page, '[data-module-record="现在完成时"]');
    assert.equal(await page.locator('#mistake-filters [aria-pressed="true"]').textContent(), '英语');
  });
  await check('卡片范围生成、翻面和按钮评分', async () => {
    await tab(page, 'cards');await click(page, '[data-card-scope*="一元二次方程"]');await click(page, '#card-ai-generate');
    assert.equal(await text(page, '#card-active-scope'), '数学 · 一元二次方程');
    await click(page, '#review-card');assert.equal(await page.locator('#review-card').getAttribute('aria-pressed'), 'true');
    await click(page, '[data-rating="记得"]');assert.equal(await text(page, '#review-index'), '2');
    assert.equal(await text(page, '#card-count'), '2');
  });
  await check('未知／多主题范围保留原卡组与进度', async () => {
    const before = await text(page, '#card-question');
    for (const scope of ['世界历史', '一元二次方程和现在完成时']) {
      await page.locator('#card-ai-scope').fill(scope);await click(page, '#card-ai-generate');
      assert.equal(await text(page, '#card-question'), before);
      assert.equal(await text(page, '#review-index'), '2');
      assert.equal(await page.locator('#card-ai-status').getAttribute('data-error'), 'true');
    }
  });
  await check('旧卡组快照恢复题目、范围和已评进度', async () => {
    await click(page, '[data-card-scope*="现在完成时"]');await click(page, '#card-ai-generate');
    await click(page, '#module-history-open');
    assert.equal(await page.locator('#module-history-list .history-entry').count(), 3);
    await click(page, '[data-module-record="cards-1"]');
    assert.equal(await text(page, '#card-active-scope'), '数学 · 一元二次方程');
    assert.equal(await text(page, '#review-index'), '2');
    assert((await text(page, '#card-question')).includes('两个根'));
    assert((await page.locator('#card-ai-scope').inputValue()).includes('一元二次方程'));
  });
  await check('滑动评分写入历史并能继续复习', async () => {
    const box = await page.locator('#review-card').boundingBox();
    await page.mouse.move(box.x + box.width * .45, box.y + 70);
    await page.mouse.down();await page.mouse.move(box.x + box.width * .95, box.y + 70, {steps:12});await page.mouse.up();
    await page.waitForTimeout(200);
    assert.equal(await text(page, '#review-index'), '3');
    await click(page, '#module-history-open');
    assert((await text(page, '#module-history-list')).includes('已复习 2 张'));
    await click(page, '[data-module-record="cards-1"]');assert.equal(await text(page, '#review-index'), '3');
  });
  await check('完成复习联动计划，已完成卡组可以重开', async () => {
    await click(page, '[data-rating="模糊"]');
    assert(await page.locator('#card-complete').isVisible());
    assert((await text(page, '#review-summary')).includes('3 张'));
    await tab(page, 'profile');await click(page, '.profile-shortcuts [data-page="plans"]');
    assert.equal(await page.locator('[data-plan="cards"]').getAttribute('aria-pressed'), 'true');
    await tab(page, 'cards');await click(page, '#module-history-open');await click(page, '[data-module-record="cards-1"]');
    assert.equal(await text(page, '#review-index'), '1');assert(await page.locator('#card-review').isVisible());
  });
  await check('导出包含完整会话、卡组历史与书架', async () => {
    await tab(page, 'profile');await click(page, '[data-profile-view="privacy"]');
    const downloadPromise = page.waitForEvent('download');await click(page, '#profile-export');
    const download = await downloadPromise;
    const data = JSON.parse(await fs.readFile(await download.path(), 'utf8'));
    assert.equal(data.is_demo, true);assert.equal(data.books.length, 4);
    assert.equal(data.history.find(record => record.id === 'local-1').messages.length, 2);
    assert.equal(data.card_history.length, 3);
  });
  await check('隐私重置恢复所有新增模块和默认样例', async () => {
    await click(page, '#profile-reset');await click(page, '#profile-reset-cancel');
    assert.equal(await text(page, '#history-count'), '9');
    await click(page, '#profile-reset');await click(page, '#profile-reset-confirm-button');await back(page);
    assert.equal(await text(page, '#history-count'), String(baseHistory));assert.equal(await text(page, '#saved-count'), '2');
    assert.equal(await page.locator('#home-book-shelf [data-book]').count(), 3);
    assert.equal(await text(page, '.member-badge'), '权益预览');
    assert.equal(await text(page, '#profile-name'), '我的学习空间');
    await tab(page, 'cards');assert.equal(await text(page, '#card-active-scope'), '数学 · 基础巩固');
    assert.equal(await text(page, '#review-index'), '1');
    await click(page, '#module-history-open');assert.equal(await page.locator('#module-history-list .history-entry').count(), 1);
  });
  await context.close();

  await check('正常动效下的推开侧栏、边缘返回与卡片翻转', async () => {
    const {page, context} = await newPage(390, false);
    await click(page, '#drawer-button');await page.waitForTimeout(550);
    const translation = await page.locator('.main').evaluate(node => new DOMMatrix(getComputedStyle(node).transform).m41);
    assert(translation > 200);
    await page.keyboard.press('Escape');await page.waitForTimeout(550);
    await tab(page, 'profile');await click(page, '#member-card');
    await page.mouse.move(5, 260);await page.mouse.down();await page.mouse.move(35,260,{steps:3});await page.mouse.move(300,260,{steps:15});await page.mouse.up();
    await settled(page);assert.equal(await text(page, '#page-title'), '我的');
    await tab(page, 'cards');await page.locator('#review-card').scrollIntoViewIfNeeded();
    await click(page, '#review-card');await page.waitForTimeout(600);
    assert.equal(await page.locator('#review-card').getAttribute('aria-pressed'), 'true');
    await context.close();
  });
  for (const width of [320, 375, 390, 430, 1440]) {
    await check(width + 'px 手机画布：新增页面布局、长名称与深色主题', async () => {
      const {page, context} = await newPage(width);
      await noOverflow(page);await tab(page, 'profile');await noOverflow(page);
      await click(page, '#profile-auth-action');await page.locator('#demo-name').fill('名字比较长的十六个汉字体验用户');await click(page, '#demo-login-form button');await noOverflow(page);
      await click(page, '#member-card');await noOverflow(page);await back(page);
      await click(page, '[data-profile-view="library"]');await noOverflow(page);await back(page);
      await tab(page, 'cards');await noOverflow(page);await click(page, '#module-history-open');await noOverflow(page);
      await tab(page, 'profile');await click(page, '[data-profile-view="settings"]');await click(page, '[data-profile-theme="dark"]');
      assert.equal(await page.locator('html').getAttribute('data-theme'), 'dark');
      await noOverflow(page);await back(page);await screenshot(page, '个人空间-深色-' + width + '.png');
      await tab(page, 'cards');await screenshot(page, '卡片-' + width + '.png');
      await context.close();
    });
  }
  await check('无脚本异常与外部网络请求', async () => {
    assert.deepEqual(errors, []);assert.deepEqual(externalRequests, []);
  });
}
run().catch(error => {console.error(error.stack);process.exitCode = 1;}).finally(async () => {
  if (browser) await browser.close();
  await fs.mkdir(output, {recursive:true});
  const sha256 = crypto.createHash('sha256').update(await fs.readFile(html)).digest('hex');
  await fs.writeFile(path.join(output, '原型集成验证.json'), JSON.stringify({html:path.basename(html), sha256, checks, errors, externalRequests}, null, 2) + '\n');
  console.log('Result:', checks.filter(check => check.passed).length + '/' + checks.length, 'Artifacts:', output);
});
