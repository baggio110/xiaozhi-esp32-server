import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import test from 'node:test'

import {
  DEFAULT_FAMILY_MEMORY_SETTINGS,
  FAMILY_MEMORY_ACTIONS,
  canEnableFamilyMemory,
  checkTagType,
  officialVoiceprintOptions,
  settingsFingerprint
} from '../src/views/familyMemoryModel.mjs'

test('家庭记忆默认关闭且使用项目内数据库路径', () => {
  assert.equal(DEFAULT_FAMILY_MEMORY_SETTINGS.enabled, false)
  assert.equal(DEFAULT_FAMILY_MEMORY_SETTINGS.family_id, '')
  assert.equal(
    DEFAULT_FAMILY_MEMORY_SETTINGS.database_path,
    'data/family_identity.db'
  )
})

test('只有当前配置通过无FAIL预检后才允许开启', () => {
  const settings = {
    family_id: 'family_a',
    database_path: 'data/family_identity.db'
  }
  const fingerprint = settingsFingerprint(settings)

  assert.equal(canEnableFamilyMemory(
    { can_enable: true, overall: 'PASS' },
    fingerprint,
    settings
  ), true)
  assert.equal(canEnableFamilyMemory(
    { can_enable: false, overall: 'FAIL' },
    fingerprint,
    settings
  ), false)
  assert.equal(canEnableFamilyMemory(
    { can_enable: true, overall: 'PASS' },
    fingerprint,
    { ...settings, family_id: 'family_b' }
  ), false)
})

test('环境检查等级映射为清晰的Element UI状态', () => {
  assert.equal(checkTagType('PASS'), 'success')
  assert.equal(checkTagType('WARN'), 'warning')
  assert.equal(checkTagType('FAIL'), 'danger')
  assert.equal(checkTagType('SKIP'), 'info')
})

test('官方声纹列表稳定使用id且不伪造缺失id', () => {
  assert.deepEqual(officialVoiceprintOptions([
    { id: 'voice_a', sourceName: '成员甲' },
    { id: '', sourceName: '无效项' },
    { sourceName: '缺少ID' },
    { id: 'voice_b' }
  ]), [
    { value: 'voice_a', label: '成员甲（voice_a）' },
    { value: 'voice_b', label: 'voice_b' }
  ])
})

test('页面动作不包含人员物理删除或记忆删除', () => {
  assert.deepEqual(FAMILY_MEMORY_ACTIONS.person, [
    'create', 'rename', 'enable', 'disable', 'show'
  ])
  assert.deepEqual(FAMILY_MEMORY_ACTIONS.voiceprint, [
    'bind', 'revoke', 'replace', 'list'
  ])
  assert.equal(JSON.stringify(FAMILY_MEMORY_ACTIONS).includes('delete'), false)
})

test('家庭记忆路由受登录保护且页面保留手工voiceprint_id兜底', async () => {
  const router = await readFile('src/router/index.js', 'utf8')
  const page = await readFile('src/views/FamilyMemory.vue', 'utf8')

  assert.match(router, /name:\s*'FamilyMemory'/)
  assert.match(router, /protectedRoutes[\s\S]*'FamilyMemory'/)
  assert.match(page, /allow-create/)
  assert.match(page, /手工输入官方接口响应中的voiceprint_id/)
  assert.match(page, /Api\.familyMemory\.getPerson/)
  assert.doesNotMatch(page, /删除PowerMem|物理删除/)
})

test('危险操作具有确认提示和重复提交保护', async () => {
  const page = await readFile('src/views/FamilyMemory.vue', 'utf8')

  assert.match(page, /确认成员状态/)
  assert.match(page, /确认撤销/)
  assert.match(page, /actionLoading:\s*false/)
  assert.match(page, /:disabled="actionLoading"/)
  assert.match(page, /finishWithError\('actionLoading'\)/)
  assert.match(page, /配置将在重启xiaozhi-server后生效/)
})

test('浏览器源码不包含server.secret且只通过manager-api调用管理操作', async () => {
  const page = await readFile('src/views/FamilyMemory.vue', 'utf8')
  const api = await readFile('src/apis/module/familyMemory.js', 'utf8')
  const source = `${page}\n${api}`

  assert.doesNotMatch(source, /server\.secret/i)
  assert.doesNotMatch(source, /family_identity\.db.*sqlite/i)
  assert.match(api, /\/admin\/family-memory/)
  assert.doesNotMatch(api, /new WebSocket|ws:\/\/|wss:\/\//)
})
