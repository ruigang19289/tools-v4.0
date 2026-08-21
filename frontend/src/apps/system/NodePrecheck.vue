<template>
  <div class="precheck-page">
    <PageHeader icon="🧪" title="节点预检" />
    <div class="layout">
      <section class="panel config-panel">
        <h2>预检配置</h2>
        <p class="hint">每行一个节点，格式：管理IP SSH用户 SSH密码 IPMI_IP IPMI用户 IPMI密码 role。密码仅用于本次执行，不会保存。</p>
        <textarea v-model="configText" class="config-editor" spellcheck="false"></textarea>
        <div class="form-row">
          <label>超时（秒）<input v-model.number="timeout" type="number" min="10" max="3600"></label>
          <label>并行数<input v-model.number="parallel" type="number" min="1" max="50"></label>
        </div>
        <label class="check-line"><input v-model="verbose" type="checkbox"> 显示详细过程</label>
        <button class="btn btn-primary" :disabled="running" @click="run">{{ running ? '执行中...' : '开始预检' }}</button>
      </section>

      <section class="panel mode-panel">
        <h2>执行模式</h2>
        <label>检查模式
          <select v-model="mode">
            <option value="all">全量巡检</option>
            <option value="dry-run">Dry Run</option>
            <option value="collect-info">信息采集</option>
            <option value="disk-perf">磁盘性能测试</option>
            <option value="network-perf">网络性能测试</option>
            <option value="steady-perf">稳态磁盘测试（破坏性）</option>
          </select>
        </label>
        <div v-if="mode === 'all' || mode === 'dry-run'" class="only-list">
          <span class="label">检查项</span>
          <label v-for="item in categories" :key="item.value" class="check-line"><input v-model="selectedOnly" type="checkbox" :value="item.value"> {{ item.label }}</label>
        </div>
        <div v-if="mode === 'steady-perf'" class="danger-box">
          <strong>危险操作</strong>
          <p>稳态测试可能执行 blkdiscard 和全盘写入，会破坏目标磁盘上的数据。</p>
          <label class="check-line"><input v-model="destructiveConfirmed" type="checkbox"> 我确认目标磁盘无重要数据</label>
          <label>目标节点<input v-model="steadyNode" placeholder="默认使用第一个存储节点"></label>
          <label>目标磁盘<input v-model="steadyDisk" placeholder="例如 /dev/nvme1n1"></label>
        </div>
        <div v-if="artifacts.length" class="artifacts">
          <span class="label">本次生成文件</span>
          <a v-for="item in artifacts" :key="item.url" class="artifact" :href="item.url" target="_blank" rel="noopener">{{ item.name }}</a>
        </div>
      </section>

      <section class="panel output-panel">
        <div class="output-header"><h2>执行输出</h2><button class="btn btn-secondary" @click="output = ''">清空</button></div>
        <pre class="output">{{ output || '等待执行...' }}</pre>
      </section>
    </div>
    <div v-if="notice" :class="['notice', noticeType]">{{ notice }}</div>
  </div>
</template>

<script setup>
import { ref, computed } from 'vue'
import PageHeader from '@/components/common/PageHeader.vue'

const API_BASE = '/api/v1/system/node-precheck'
const configText = ref(`NODES=(
    10.10.16.82 root - 10.10.6.82 admin - storage
)

STORNETS=(
    net1:10.10.147.0/24
    net2:10.10.148.0/24
)

PTNETS=(
    net1
    net2
)`)
const mode = ref('dry-run')
const selectedOnly = ref([])
const timeout = ref(30)
const parallel = ref(10)
const verbose = ref(true)
const steadyNode = ref('')
const steadyDisk = ref('')
const destructiveConfirmed = ref(false)
const running = ref(false)
const output = ref('')
const artifacts = ref([])
const notice = ref('')
const noticeType = ref('info')
const categories = [
  { value: 'bios', label: 'BIOS' },
  { value: 'os', label: '操作系统' },
  { value: 'hardware', label: '硬件' },
  { value: 'network', label: '网络' },
  { value: 'disk', label: '磁盘' },
]
const only = computed(() => selectedOnly.value.join(','))

const showNotice = (message, type = 'info') => {
  notice.value = message
  noticeType.value = type
  window.setTimeout(() => { notice.value = '' }, 3500)
}

const run = async () => {
  if (!configText.value.trim()) {
    showNotice('请填写节点预检配置', 'error')
    return
  }
  if (mode.value === 'steady-perf' && !destructiveConfirmed.value) {
    showNotice('请先确认稳态测试的破坏性风险', 'error')
    return
  }
  running.value = true
  output.value = `开始执行：${mode.value}\n`
  artifacts.value = []
  try {
    const response = await fetch(`${API_BASE}/run`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        config_text: configText.value,
        mode: mode.value,
        only: only.value,
        timeout: timeout.value,
        parallel: parallel.value,
        verbose: verbose.value,
        steady_node: steadyNode.value,
        steady_disk: steadyDisk.value,
        confirmed_destructive: destructiveConfirmed.value,
      }),
    })
    const data = await response.json()
    if (!response.ok || data.status === 'error') throw new Error(data.error || '执行失败')
    output.value = data.output || '(工具没有输出)'
    artifacts.value = data.artifacts || []
    showNotice(data.returncode === 0 ? '节点预检完成' : `节点预检结束，返回码 ${data.returncode}`, data.returncode === 0 ? 'success' : 'warning')
  } catch (error) {
    output.value += `\n错误：${error.message}`
    showNotice(error.message, 'error')
  } finally {
    running.value = false
  }
}
</script>

<style scoped>
.precheck-page { min-height: 100vh; padding: 20px; background: #f3f6fa; color: #1f2937; }
.layout { display: grid; grid-template-columns: minmax(360px, 1fr) minmax(280px, .7fr) minmax(420px, 1.4fr); gap: 16px; align-items: start; }
.panel { background: #fff; border: 1px solid #dbe3ec; border-radius: 8px; padding: 18px; box-shadow: 0 2px 8px rgba(15, 23, 42, .05); }
h2 { margin: 0 0 14px; font-size: 17px; }
.hint { color: #64748b; font-size: 13px; line-height: 1.6; margin: 0 0 12px; }
.config-editor { width: 100%; min-height: 360px; resize: vertical; box-sizing: border-box; padding: 10px; border: 1px solid #cbd5e1; border-radius: 5px; font: 13px/1.5 monospace; }
.form-row { display: flex; gap: 10px; margin: 14px 0; }
label { display: flex; flex-direction: column; gap: 6px; color: #475569; font-size: 13px; margin-bottom: 13px; }
.form-row label { flex: 1; }
input, select { min-height: 34px; padding: 6px 9px; border: 1px solid #cbd5e1; border-radius: 5px; box-sizing: border-box; background: #fff; }
.check-line { flex-direction: row; align-items: center; gap: 8px; margin: 9px 0; }
.check-line input { min-height: auto; }
.btn { border: 0; border-radius: 5px; padding: 9px 15px; cursor: pointer; }
.btn:disabled { opacity: .55; cursor: not-allowed; }
.btn-primary { background: #2563eb; color: #fff; width: 100%; margin-top: 8px; }
.btn-secondary { background: #e2e8f0; color: #334155; }
.only-list { border-top: 1px solid #e2e8f0; padding-top: 14px; margin-top: 12px; }
.label { display: block; color: #64748b; font-size: 12px; margin-bottom: 8px; }
.danger-box { border: 1px solid #f59e0b; background: #fffbeb; border-radius: 5px; padding: 12px; color: #92400e; }
.danger-box p { font-size: 13px; line-height: 1.5; }
.artifacts { border-top: 1px solid #e2e8f0; margin-top: 15px; padding-top: 12px; }
.artifact { font: 12px monospace; color: #475569; padding: 3px 0; word-break: break-all; }
.output-panel { min-height: 610px; }
.output-header { display: flex; justify-content: space-between; align-items: center; }
.output { min-height: 540px; max-height: 70vh; overflow: auto; background: #0f172a; color: #dbeafe; border-radius: 5px; padding: 14px; margin: 0; white-space: pre-wrap; word-break: break-word; font: 12px/1.55 monospace; }
.notice { position: fixed; right: 24px; bottom: 24px; padding: 12px 16px; border-radius: 5px; color: #fff; box-shadow: 0 4px 14px rgba(0,0,0,.18); }
.notice.info { background: #2563eb; }.notice.success { background: #16a34a; }.notice.warning { background: #d97706; }.notice.error { background: #dc2626; }
@media (max-width: 1100px) { .layout { grid-template-columns: 1fr 1fr; }.output-panel { grid-column: 1 / -1; } }
@media (max-width: 700px) { .layout { grid-template-columns: 1fr; }.output-panel { grid-column: auto; } }
</style>
