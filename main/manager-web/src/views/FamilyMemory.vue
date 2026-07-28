<template>
  <div class="family-memory-page">
    <HeaderBar />
    <main class="page-body">
      <div class="page-heading">
        <div>
          <h2>家庭记忆</h2>
          <p>个人记忆按稳定成员身份隔离；页面默认存在，但功能默认关闭。</p>
        </div>
        <el-select v-model="targetWs" placeholder="请选择目标服务端" @change="handleServerChange">
          <el-option v-for="server in servers" :key="server" :label="server" :value="server" />
        </el-select>
      </div>

      <el-alert
        title="首次使用：设置家庭ID → 环境检查 → 新增成员 → 绑定官方声纹 → 开启家庭记忆 → 重启server"
        type="info"
        :closable="false"
        show-icon
      />

      <section class="card-grid">
        <el-card shadow="never" class="panel">
          <div slot="header" class="panel-title">
            <span>1. 基本设置</span>
            <el-tag :type="settings.enabled ? 'success' : 'info'">
              {{ settings.enabled ? '已配置启用' : '默认关闭' }}
            </el-tag>
          </div>
          <el-form ref="settingsForm" :model="settings" :rules="settingsRules" label-width="110px">
            <el-form-item label="家庭记忆">
              <el-switch
                v-model="settings.enabled"
                :disabled="settings.enabled ? false : !canEnable"
                active-text="开启"
                inactive-text="关闭"
              />
              <div v-if="!settings.enabled && !canEnable" class="field-help">
                请先对当前家庭ID和数据库路径执行无FAIL的环境检查。
              </div>
            </el-form-item>
            <el-form-item label="家庭ID" prop="family_id">
              <el-input v-model="settings.family_id" placeholder="例如 family_001" />
              <div class="field-help">永久固定；修改会产生新的 PowerMem 用户命名空间。</div>
            </el-form-item>
            <el-collapse>
              <el-collapse-item title="高级设置" name="advanced">
                <el-form-item label="数据库路径" prop="database_path">
                  <el-input v-model="settings.database_path" />
                  <div class="field-help">必须是 server 项目内相对路径，默认 data/family_identity.db。</div>
                </el-form-item>
              </el-collapse-item>
            </el-collapse>
            <div class="status-strip">
              <span>配置状态：{{ settings.configured ? '已填写' : '待填写' }}</span>
              <el-tag v-if="restartRequired" type="warning">
                配置将在重启xiaozhi-server后生效
              </el-tag>
              <el-tag v-else type="success">运行配置已同步</el-tag>
            </div>
            <div class="button-row">
              <el-button :loading="preflightLoading" :disabled="!targetWs" @click="runPreflight">
                环境检查
              </el-button>
              <el-button
                type="primary"
                :loading="settingsLoading"
                :disabled="settingsLoading"
                @click="saveSettings"
              >
                保存设置
              </el-button>
            </div>
          </el-form>
        </el-card>

        <el-card shadow="never" class="panel">
          <div slot="header" class="panel-title">
            <span>2. 环境检查</span>
            <el-tag v-if="preflight" :type="checkTagType(preflight.overall)">
              {{ preflight.overall }}
            </el-tag>
          </div>
          <el-empty v-if="!preflight" description="尚未执行环境检查" />
          <template v-else>
            <div class="metric-row">
              <div><strong>{{ preflight.member_count }}</strong><span>成员数量</span></div>
              <div><strong>{{ preflight.active_voiceprint_count }}</strong><span>有效声纹</span></div>
              <div><strong>{{ preflight.summary && preflight.summary.FAIL || 0 }}</strong><span>FAIL</span></div>
            </div>
            <div class="check-list">
              <div v-for="check in preflight.checks" :key="check.name" class="check-item">
                <el-tag size="mini" :type="checkTagType(check.level)">{{ check.level }}</el-tag>
                <div>
                  <strong>{{ check.name }}</strong>
                  <p>{{ check.message }}</p>
                </div>
              </div>
            </div>
            <el-alert
              :title="preflight.can_enable ? '总体可以启用' : '存在FAIL，禁止启用'"
              :type="preflight.can_enable ? 'success' : 'error'"
              :closable="false"
              show-icon
            />
          </template>
        </el-card>
      </section>

      <el-card shadow="never" class="panel wide-panel">
        <div slot="header" class="panel-title">
          <span>3. 家庭成员</span>
          <el-button type="primary" size="small" :disabled="!managementReady" @click="openCreatePerson">
            新增成员
          </el-button>
        </div>
        <div class="table-wrap">
          <el-table :data="persons" v-loading="peopleLoading" empty-text="暂无家庭成员">
            <el-table-column prop="display_name" label="显示名称" min-width="130" />
            <el-table-column prop="person_id" label="person_id" min-width="150" />
            <el-table-column prop="memory_user_id" label="memory_user_id" min-width="210" />
            <el-table-column label="状态" width="90">
              <template slot-scope="{ row }">
                <el-tag :type="row.enabled ? 'success' : 'info'">{{ row.enabled ? '启用' : '停用' }}</el-tag>
              </template>
            </el-table-column>
            <el-table-column label="有效声纹" width="100">
              <template slot-scope="{ row }">{{ row.active_voiceprint_ids.length }}</template>
            </el-table-column>
            <el-table-column label="操作" min-width="235" fixed="right">
              <template slot-scope="{ row }">
                <el-button type="text" :disabled="actionLoading" @click="showPerson(row)">详情</el-button>
                <el-button type="text" :disabled="actionLoading" @click="openRenamePerson(row)">改名</el-button>
                <el-button type="text" :disabled="actionLoading" @click="togglePerson(row)">
                  {{ row.enabled ? '停用' : '启用' }}
                </el-button>
                <el-button type="text" :disabled="actionLoading" @click="openBind(row)">绑定声纹</el-button>
              </template>
            </el-table-column>
          </el-table>
        </div>
      </el-card>

      <el-card shadow="never" class="panel wide-panel">
        <div slot="header" class="panel-title">
          <span>4. 声纹绑定</span>
          <el-button type="primary" size="small" :disabled="!managementReady" @click="openBind()">
            绑定已有voiceprint_id
          </el-button>
        </div>
        <el-alert
          title="官方声纹列表按agentId查询；若无法取得列表，可手工输入官方接口响应中的voiceprint_id。"
          type="info"
          :closable="false"
          show-icon
        />
        <div class="table-wrap">
          <el-table :data="voiceprints" v-loading="voiceprintsLoading" empty-text="暂无声纹绑定">
            <el-table-column prop="voiceprint_id" label="voiceprint_id" min-width="210" />
            <el-table-column prop="person_id" label="person_id" min-width="160" />
            <el-table-column label="状态" width="100">
              <template slot-scope="{ row }">
                <el-tag :type="row.active ? 'success' : 'info'">
                  {{ row.active ? '有效' : '已撤销' }}
                </el-tag>
              </template>
            </el-table-column>
            <el-table-column prop="revoked_at" label="撤销时间" min-width="190" />
            <el-table-column label="操作" width="150" fixed="right">
              <template slot-scope="{ row }">
                <template v-if="row.active">
                  <el-button type="text" :disabled="actionLoading" @click="openReplace(row)">替换</el-button>
                  <el-button type="text" class="danger-link" :disabled="actionLoading"
                    @click="revokeVoiceprint(row)">撤销</el-button>
                </template>
                <span v-else>仅保留历史</span>
              </template>
            </el-table-column>
          </el-table>
        </div>
      </el-card>
    </main>

    <el-dialog :title="personDialog.mode === 'create' ? '新增家庭成员' : '修改显示名称'"
      :visible.sync="personDialog.visible" width="min(520px, 92vw)">
      <el-form :model="personDialog.form" label-width="100px">
        <el-form-item label="person_id">
          <el-input v-model="personDialog.form.personId" :disabled="personDialog.mode !== 'create'" />
        </el-form-item>
        <el-form-item label="显示名称">
          <el-input v-model="personDialog.form.displayName" />
        </el-form-item>
      </el-form>
      <span slot="footer">
        <el-button @click="personDialog.visible = false">取消</el-button>
        <el-button type="primary" :loading="personDialog.loading" @click="submitPerson">保存</el-button>
      </span>
    </el-dialog>

    <el-dialog title="成员详情" :visible.sync="detailDialog.visible" width="min(600px, 92vw)">
      <el-descriptions v-if="detailDialog.person" v-loading="detailDialog.loading" :column="1" border>
        <el-descriptions-item label="显示名称">{{ detailDialog.person.display_name }}</el-descriptions-item>
        <el-descriptions-item label="person_id">{{ detailDialog.person.person_id }}</el-descriptions-item>
        <el-descriptions-item label="memory_user_id">{{ detailDialog.person.memory_user_id }}</el-descriptions-item>
        <el-descriptions-item label="有效声纹">
          {{ detailDialog.person.active_voiceprint_ids.join('、') || '无' }}
        </el-descriptions-item>
        <el-descriptions-item label="已撤销声纹">
          {{ detailDialog.person.revoked_voiceprint_ids.join('、') || '无' }}
        </el-descriptions-item>
      </el-descriptions>
    </el-dialog>

    <el-dialog :title="voiceDialog.mode === 'replace' ? '替换声纹绑定' : '绑定已有声纹'"
      :visible.sync="voiceDialog.visible" width="min(600px, 92vw)">
      <el-form :model="voiceDialog.form" label-width="120px">
        <el-form-item label="家庭成员">
          <el-select v-model="voiceDialog.form.personId" filterable style="width:100%">
            <el-option v-for="person in persons" :key="person.person_id"
              :label="`${person.display_name}（${person.person_id}）`" :value="person.person_id" />
          </el-select>
        </el-form-item>
        <el-form-item v-if="voiceDialog.mode === 'replace'" label="旧voiceprint_id">
          <el-input v-model="voiceDialog.form.oldVoiceprintId" disabled />
        </el-form-item>
        <el-form-item label="官方agentId">
          <div class="inline-field">
            <el-input v-model="officialAgentId" placeholder="可选：用于加载官方声纹列表" />
            <el-button :loading="officialLoading" @click="loadOfficialVoiceprints">加载</el-button>
          </div>
        </el-form-item>
        <el-form-item :label="voiceDialog.mode === 'replace' ? '新voiceprint_id' : 'voiceprint_id'">
          <el-select
            v-model="voiceDialog.form.voiceprintId"
            filterable
            allow-create
            default-first-option
            placeholder="从官方列表选择或手工输入"
            style="width:100%"
          >
            <el-option v-for="option in officialVoiceprints" :key="option.value"
              :label="option.label" :value="option.value" />
          </el-select>
        </el-form-item>
      </el-form>
      <span slot="footer">
        <el-button @click="voiceDialog.visible = false">取消</el-button>
        <el-button type="primary" :loading="voiceDialog.loading" @click="submitVoiceprint">
          {{ voiceDialog.mode === 'replace' ? '确认替换' : '确认绑定' }}
        </el-button>
      </span>
    </el-dialog>

    <el-footer><VersionFooter /></el-footer>
  </div>
</template>

<script>
import Api from '@/apis/api'
import HeaderBar from '@/components/HeaderBar.vue'
import VersionFooter from '@/components/VersionFooter.vue'
import {
  DEFAULT_FAMILY_MEMORY_SETTINGS,
  canEnableFamilyMemory,
  checkTagType,
  officialVoiceprintOptions,
  settingsFingerprint
} from './familyMemoryModel.mjs'

export default {
  name: 'FamilyMemory',
  components: { HeaderBar, VersionFooter },
  data() {
    return {
      settings: { ...DEFAULT_FAMILY_MEMORY_SETTINGS, configured: false },
      servers: [],
      targetWs: '',
      preflight: null,
      checkedFingerprint: '',
      restartRequired: false,
      settingsLoading: false,
      preflightLoading: false,
      peopleLoading: false,
      voiceprintsLoading: false,
      actionLoading: false,
      persons: [],
      voiceprints: [],
      officialAgentId: '',
      officialVoiceprints: [],
      officialLoading: false,
      settingsRules: {
        family_id: [{ required: true, message: '请输入长期固定的家庭ID', trigger: 'blur' }],
        database_path: [{ required: true, message: '请输入数据库相对路径', trigger: 'blur' }]
      },
      personDialog: {
        visible: false,
        loading: false,
        mode: 'create',
        form: { personId: '', displayName: '' }
      },
      detailDialog: { visible: false, loading: false, person: null },
      voiceDialog: {
        visible: false,
        loading: false,
        mode: 'bind',
        form: { personId: '', voiceprintId: '', oldVoiceprintId: '' }
      }
    }
  },
  computed: {
    canEnable() {
      return canEnableFamilyMemory(this.preflight, this.checkedFingerprint, this.settings)
    },
    managementReady() {
      return Boolean(this.targetWs && this.settings.configured && this.settings.family_id)
    }
  },
  created() {
    this.loadSettings()
    this.loadServers()
  },
  methods: {
    checkTagType,
    loadSettings() {
      Api.familyMemory.getSettings(({ data }) => {
        if (data.code !== 0) return this.showError(data.msg)
        this.settings = { ...DEFAULT_FAMILY_MEMORY_SETTINGS, ...data.data }
      })
    },
    loadServers() {
      Api.admin.getWsServerList({}, ({ data }) => {
        if (data.code !== 0) return this.showError(data.msg)
        this.servers = data.data || []
        if (this.servers.length === 1) {
          this.targetWs = this.servers[0]
          this.handleServerChange()
        }
      })
    },
    handleServerChange() {
      this.preflight = null
      this.checkedFingerprint = ''
      if (this.managementReady) this.refreshManagementData()
    },
    runPreflight() {
      this.$refs.settingsForm.validate(valid => {
        if (!valid || !this.targetWs) return
        this.preflightLoading = true
        Api.familyMemory.preflight({
          enabled: true,
          familyId: this.settings.family_id,
          databasePath: this.settings.database_path,
          targetWs: this.targetWs
        }, ({ data }) => {
          this.preflightLoading = false
          if (data.code !== 0) return this.showError(data.msg)
          this.preflight = data.data
          this.checkedFingerprint = settingsFingerprint(this.settings)
          this.restartRequired = Boolean(data.data.runtime && data.data.runtime.restart_required)
        }, this.finishWithError('preflightLoading'))
      })
    },
    saveSettings() {
      this.$refs.settingsForm.validate(valid => {
        if (!valid) return
        if (this.settings.enabled && !this.canEnable) {
          return this.showError('当前配置尚未通过环境检查，不能启用。')
        }
        const execute = () => {
          this.settingsLoading = true
          Api.familyMemory.saveSettings({
            enabled: this.settings.enabled,
            familyId: this.settings.family_id,
            databasePath: this.settings.database_path,
            targetWs: this.targetWs
          }, ({ data }) => {
            this.settingsLoading = false
            if (data.code !== 0) return this.showError(data.msg)
            this.settings.configured = true
            this.restartRequired = Boolean(data.data.restart_required)
            this.$message.success('家庭记忆设置已保存；配置将在重启xiaozhi-server后生效')
            this.refreshManagementData()
          }, this.finishWithError('settingsLoading'))
        }
        if (this.settings.enabled) {
          this.$confirm('配置将在重启xiaozhi-server后生效，确认保存并开启？', '确认开启', {
            type: 'warning'
          }).then(execute).catch(() => {})
        } else {
          execute()
        }
      })
    },
    refreshManagementData() {
      this.loadPersons()
      this.loadVoiceprints()
    },
    loadPersons() {
      if (!this.managementReady) return
      this.peopleLoading = true
      Api.familyMemory.listPersons(this.targetWs, ({ data }) => {
        this.peopleLoading = false
        if (data.code !== 0) return this.showError(data.msg)
        this.persons = data.data.persons || []
      }, this.finishWithError('peopleLoading'))
    },
    loadVoiceprints() {
      if (!this.managementReady) return
      this.voiceprintsLoading = true
      Api.familyMemory.listVoiceprints(this.targetWs, '', ({ data }) => {
        this.voiceprintsLoading = false
        if (data.code !== 0) return this.showError(data.msg)
        this.voiceprints = data.data.voiceprints || []
      }, this.finishWithError('voiceprintsLoading'))
    },
    openCreatePerson() {
      this.personDialog = {
        visible: true,
        loading: false,
        mode: 'create',
        form: { personId: '', displayName: '' }
      }
    },
    openRenamePerson(person) {
      this.personDialog = {
        visible: true,
        loading: false,
        mode: 'rename',
        form: { personId: person.person_id, displayName: person.display_name }
      }
    },
    submitPerson() {
      const form = this.personDialog.form
      if (!form.personId || !form.displayName) return this.showError('person_id和显示名称不能为空')
      this.personDialog.loading = true
      const payload = {
        targetWs: this.targetWs,
        personId: form.personId,
        displayName: form.displayName
      }
      const done = ({ data }) => {
        this.personDialog.loading = false
        if (data.code !== 0) return this.showError(data.msg)
        this.personDialog.visible = false
        this.$message.success('成员信息已保存')
        this.refreshManagementData()
      }
      if (this.personDialog.mode === 'create') {
        Api.familyMemory.createPerson(payload, done, this.finishWithError('personDialog.loading'))
      } else {
        Api.familyMemory.renamePerson(form.personId, payload, done, this.finishWithError('personDialog.loading'))
      }
    },
    showPerson(person) {
      this.detailDialog = { visible: true, loading: true, person }
      Api.familyMemory.getPerson(this.targetWs, person.person_id, ({ data }) => {
        this.detailDialog.loading = false
        if (data.code !== 0) return this.showError(data.msg)
        const persons = data.data.persons || []
        if (persons.length !== 1) return this.showError('成员详情不存在')
        this.detailDialog.person = persons[0]
      }, this.finishWithError('detailDialog.loading'))
    },
    togglePerson(person) {
      const enabled = !person.enabled
      this.$confirm(
        `${enabled ? '启用' : '停用'}成员“${person.display_name}”？`,
        '确认成员状态',
        { type: 'warning' }
      ).then(() => {
        this.actionLoading = true
        Api.familyMemory.setPersonEnabled(person.person_id, {
          targetWs: this.targetWs,
          enabled
        }, ({ data }) => {
          this.actionLoading = false
          if (data.code !== 0) return this.showError(data.msg)
          this.$message.success(enabled ? '成员已启用' : '成员已停用')
          this.loadPersons()
        }, this.finishWithError('actionLoading'))
      }).catch(() => {})
    },
    openBind(person) {
      this.voiceDialog = {
        visible: true,
        loading: false,
        mode: 'bind',
        form: {
          personId: person ? person.person_id : '',
          voiceprintId: '',
          oldVoiceprintId: ''
        }
      }
    },
    openReplace(binding) {
      this.voiceDialog = {
        visible: true,
        loading: false,
        mode: 'replace',
        form: {
          personId: binding.person_id,
          voiceprintId: '',
          oldVoiceprintId: binding.voiceprint_id
        }
      }
    },
    submitVoiceprint() {
      const form = this.voiceDialog.form
      if (!form.personId || !form.voiceprintId) return this.showError('成员和voiceprint_id不能为空')
      const execute = () => {
        this.voiceDialog.loading = true
        const done = ({ data }) => {
          this.voiceDialog.loading = false
          if (data.code !== 0) return this.showError(data.msg)
          this.voiceDialog.visible = false
          this.$message.success(this.voiceDialog.mode === 'replace' ? '声纹已替换' : '声纹已绑定')
          this.refreshManagementData()
        }
        if (this.voiceDialog.mode === 'replace') {
          Api.familyMemory.replaceVoiceprint({
            targetWs: this.targetWs,
            personId: form.personId,
            oldVoiceprintId: form.oldVoiceprintId,
            newVoiceprintId: form.voiceprintId
          }, done, this.finishWithError('voiceDialog.loading'))
        } else {
          Api.familyMemory.bindVoiceprint({
            targetWs: this.targetWs,
            personId: form.personId,
            voiceprintId: form.voiceprintId
          }, done, this.finishWithError('voiceDialog.loading'))
        }
      }
      if (this.voiceDialog.mode === 'replace') {
        this.$confirm('替换会撤销旧声纹，且旧voiceprint_id不能重新绑定，确认继续？', '确认替换', {
          type: 'warning'
        }).then(execute).catch(() => {})
      } else {
        execute()
      }
    },
    revokeVoiceprint(binding) {
      this.$confirm('撤销后该voiceprint_id不能重新绑定，确认继续？', '确认撤销', {
        type: 'warning'
      }).then(() => {
        this.actionLoading = true
        Api.familyMemory.revokeVoiceprint({
          targetWs: this.targetWs,
          voiceprintId: binding.voiceprint_id
        }, ({ data }) => {
          this.actionLoading = false
          if (data.code !== 0) return this.showError(data.msg)
          this.$message.success('声纹绑定已撤销')
          this.refreshManagementData()
        }, this.finishWithError('actionLoading'))
      }).catch(() => {})
    },
    loadOfficialVoiceprints() {
      if (!this.officialAgentId) return this.showError('请输入官方声纹所属agentId')
      this.officialLoading = true
      Api.agent.getAgentVoicePrintList(this.officialAgentId, ({ data }) => {
        this.officialLoading = false
        if (data.code !== 0) {
          this.officialVoiceprints = []
          return this.showError(`${data.msg || '官方声纹列表不可用'}；请手工输入voiceprint_id。`)
        }
        this.officialVoiceprints = officialVoiceprintOptions(data.data)
      })
    },
    finishWithError(flag) {
      return error => {
        const parts = flag.split('.')
        if (parts.length === 1) this[flag] = false
        else this[parts[0]][parts[1]] = false
        this.showError(error && error.data && error.data.msg)
      }
    },
    showError(message) {
      this.$message.error(message || '操作失败，请检查服务端状态')
    }
  }
}
</script>

<style lang="scss" scoped>
.family-memory-page {
  min-height: 100vh;
  background: #eff4ff;
}
.page-body {
  max-width: 1440px;
  margin: 0 auto;
  padding: 20px 22px 36px;
}
.page-heading, .panel-title, .button-row, .status-strip, .inline-field {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}
.page-heading {
  margin-bottom: 16px;
}
.page-heading h2 {
  margin: 0 0 6px;
  font-size: 26px;
}
.page-heading p, .field-help, .check-item p {
  margin: 0;
  color: #7a8498;
  font-size: 13px;
}
.card-grid {
  display: grid;
  grid-template-columns: minmax(0, 1fr) minmax(0, 1fr);
  gap: 18px;
  margin-top: 18px;
}
.panel {
  border: 0;
  border-radius: 12px;
}
.wide-panel {
  margin-top: 18px;
}
.panel-title {
  font-size: 18px;
  font-weight: 600;
}
.status-strip {
  flex-wrap: wrap;
  padding: 12px;
  margin: 12px 0;
  border-radius: 8px;
  background: #f7f9fd;
}
.button-row {
  justify-content: flex-end;
  margin-top: 16px;
}
.metric-row {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: 10px;
  margin-bottom: 14px;
}
.metric-row div {
  padding: 12px;
  text-align: center;
  border-radius: 8px;
  background: #f7f9fd;
}
.metric-row strong, .metric-row span {
  display: block;
}
.metric-row strong {
  font-size: 22px;
  color: #3959d9;
}
.metric-row span {
  margin-top: 4px;
  font-size: 12px;
  color: #7a8498;
}
.check-list {
  max-height: 310px;
  margin-bottom: 14px;
  overflow: auto;
}
.check-item {
  display: grid;
  grid-template-columns: 58px 1fr;
  gap: 10px;
  padding: 9px 0;
  border-bottom: 1px solid #edf0f6;
}
.table-wrap {
  width: 100%;
  overflow-x: auto;
}
.danger-link {
  color: #f56c6c;
}
.inline-field {
  justify-content: stretch;
}
.inline-field .el-input {
  flex: 1;
}
@media (max-width: 860px) {
  .page-body {
    padding: 12px;
  }
  .page-heading {
    align-items: stretch;
    flex-direction: column;
  }
  .card-grid {
    grid-template-columns: 1fr;
  }
  .button-row {
    align-items: stretch;
    flex-direction: column;
  }
  .button-row .el-button {
    width: 100%;
    margin-left: 0;
  }
}
</style>
