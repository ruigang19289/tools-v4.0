<template>
  <div class="fio-page">
    <PageHeader
      icon="📊"
      title="FIO 性能测试"
    />

    <div class="main-content">
      <div class="left-panel">
        <div class="section">
          <h2 class="section-title">主机配置</h2>

          <div class="form-group">
            <label>主机列表 (每行一个IP或范围):</label>
            <textarea
              v-model="hostsText"
              placeholder="192.168.1.1
192.168.1.10-12
192.168.1.20"
              rows="3"
            ></textarea>
          </div>

          <div class="form-row">
            <div class="form-group">
              <label>用户名:</label>
              <input type="text" v-model="config.username" placeholder="root">
            </div>
            <div class="form-group">
              <label>端口:</label>
              <input type="number" v-model="config.port" placeholder="22">
            </div>
          </div>

          <div class="form-group">
            <label>认证方式:</label>
            <select v-model="config.auth_method">
              <option value="password">密码</option>
              <option value="key">服务器 SSH 密钥</option>
            </select>
          </div>
          <div v-if="config.auth_method === 'password'" class="form-group">
            <label>密码:</label>
            <input type="password" v-model="config.password" placeholder="******">
          </div>

          <button class="btn btn-primary btn-full" @click="validateHosts" :disabled="!canValidate">
            验证连接
          </button>

          <div v-if="validationResults.length > 0" class="validation-summary">
            <span class="badge success" v-if="validationResults.every(r => r.status === 'success' && r.has_fio)">
              ✅ 全部连接成功
            </span>
            <span class="badge warning" v-else-if="validationResults.some(r => r.status === 'success' || r.status === 'warning')">
              ⚠️ 部分可用 ({{ validationResults.filter(r => r.status === 'success' && r.has_fio).length }}/{{ getValidHosts().length }})
            </span>
            <span class="badge error" v-else>
              ❌ 连接失败
            </span>
          </div>
        </div>

        <div class="section">
          <h2 class="section-title">测试类型</h2>

          <div class="test-type-grid">
            <label class="radio-card" :class="{ active: params.rw === 'randread' }">
              <input type="radio" v-model="params.rw" value="randread">
              <span class="radio-icon">📖</span>
              <span class="radio-name">随机读</span>
            </label>
            <label class="radio-card" :class="{ active: params.rw === 'randwrite' }">
              <input type="radio" v-model="params.rw" value="randwrite">
              <span class="radio-icon">📝</span>
              <span class="radio-name">随机写</span>
            </label>
            <label class="radio-card" :class="{ active: params.rw === 'randrw' }">
              <input type="radio" v-model="params.rw" value="randrw">
              <span class="radio-icon">🔄</span>
              <span class="radio-name">混合读写</span>
            </label>
            <label class="radio-card" :class="{ active: params.rw === 'read' }">
              <input type="radio" v-model="params.rw" value="read">
              <span class="radio-icon">📑</span>
              <span class="radio-name">顺序读</span>
            </label>
            <label class="radio-card" :class="{ active: params.rw === 'write' }">
              <input type="radio" v-model="params.rw" value="write">
              <span class="radio-icon">✏️</span>
              <span class="radio-name">顺序写</span>
            </label>
          </div>

          <div v-if="params.rw === 'randrw'" class="form-group">
            <label>读写比例 (读% / 写%):</label>
            <div class="mix-slider">
              <input type="range" v-model="params.rwmixread" min="10" max="90">
              <span class="mix-label">{{ params.rwmixread }}% / {{ 100 - params.rwmixread }}%</span>
            </div>
          </div>
        </div>

        <div class="section">
          <h2 class="section-title">测试引擎</h2>

          <div class="test-type-grid">
            <label class="radio-card" :class="{ active: params.ioengine === 'libaio' }">
              <input type="radio" v-model="params.ioengine" value="libaio">
              <span class="radio-icon">⚙️</span>
              <span class="radio-name">libaio</span>
            </label>
            <label class="radio-card" :class="{ active: params.ioengine === 'rbd' }">
              <input type="radio" v-model="params.ioengine" value="rbd">
              <span class="radio-icon">💾</span>
              <span class="radio-name">rbd</span>
            </label>
          </div>
        </div>

        <div class="section">
          <h2 class="section-title">测试参数</h2>

          <div v-if="params.ioengine === 'rbd'" class="form-row">
            <div class="form-group">
              <label>卷名称:</label>
              <input type="text" v-model="params.filename" placeholder="rbdtest1">
            </div>
            <div class="form-group"></div>
          </div>

          <template v-else>
            <div class="form-group">
              <div class="device-label-row">
                <label>测试盘符:</label>
                <label v-if="scannedDevices.length > 0" class="device-select-all">
                  <span>全选</span>
                  <input type="checkbox" :checked="allScannedDevicesSelected" @change="toggleAllScannedDevices">
                </label>
              </div>
              <div v-if="scannedDevices.length > 0" class="device-list">
                <label v-for="dev in scannedDevices" :key="dev.path" class="device-item" :class="{ active: selectedDevices.includes(dev.path) }">
                  <div class="device-info">
                    <span>{{ dev.path }}</span>
                    <small>{{ dev.alias }} {{ dev.size ? `| ${dev.size}` : '' }}</small>
                  </div>
                  <input type="checkbox" :value="dev.path" v-model="selectedDevices">
                </label>
              </div>
              <div v-else class="device-empty">未扫描到 multipath dm-* 盘，可手动填写普通裸设备</div>
              <input class="device-manual-input" type="text" v-model="manualDevices" placeholder="手动输入: /dev/sdb 或 /dev/sdb:/dev/sdc">
            </div>
          </template>

          <div v-if="params.ioengine === 'rbd'" class="form-group">
            <label>存储池名称:</label>
            <input type="text" v-model="params.pool" placeholder="pool-rbdtest1">
          </div>

          <div class="params-grid">
            <div class="form-group">
              <label>块大小:</label>
              <select v-model="params.bs">
                <option value="4k">4 KB</option>
                <option value="8k">8 KB</option>
                <option value="16k">16 KB</option>
                <option value="32k">32 KB</option>
                <option value="64k">64 KB</option>
                <option value="128k">128 KB</option>
                <option value="256k">256 KB</option>
                <option value="512k">512 KB</option>
                <option value="1m">1 MB</option>
                <option value="4m">4 MB</option>
                <option value="10m">10 MB</option>
              </select>
            </div>

            <div class="form-group">
              <label>IO 深度:</label>
              <select v-model="params.iodepth">
                <option value="1">1</option>
                <option value="2">2</option>
                <option value="4">4</option>
                <option value="8">8</option>
                <option value="16">16</option>
                <option value="32">32</option>
                <option value="64">64</option>
                <option value="128">128</option>
                <option value="256">256</option>
              </select>
            </div>

            <div class="form-group">
              <label>线程数:</label>
              <select v-model="params.numjobs">
                <option value="1">1</option>
                <option value="2">2</option>
                <option value="4">4</option>
                <option value="8">8</option>
                <option value="16">16</option>
                <option value="32">32</option>
                <option value="64">64</option>
                <option value="128">128</option>
                <option value="256">256</option>
              </select>
            </div>

            <div class="form-group">
              <label>测试时长 (秒):</label>
              <input type="number" v-model="params.runtime" min="10" max="3600">
            </div>

            <div class="form-group">
              <label>CPU 核心（自动检测）:</label>
              <input type="text" v-model="params.cpus_allowed" placeholder="无匹配核心时不绑定">
            </div>
          </div>
        </div>

        <div class="section actions-section">
          <button class="btn btn-primary btn-full" @click="startTest" :disabled="!canStart || isTesting">
            {{ isTesting ? '测试中...' : '开始测试' }}
          </button>

          <button v-if="isTesting" class="btn btn-danger btn-full" @click="stopTest" :disabled="isStopping">
            {{ isStopping ? '正在停止源端 FIO...' : '停止测试' }}
          </button>
        </div>
      </div>

      <div class="right-panel">
        <div class="top-row">
          <div class="summary-cards">
            <div class="card">
              <div class="card-icon">⚡</div>
              <div class="card-content">
                <div class="card-label">平均 I/O 速率</div>
                <div class="card-value">{{ avgIops }}</div>
                <div v-if="mixStatsMode" class="card-subvalue">读 {{ avgReadIops }} / 写 {{ avgWriteIops }}</div>
                <div v-else class="card-subvalue card-subvalue-placeholder">&nbsp;</div>
                <div class="card-unit">IOPS</div>
              </div>
            </div>
            <div class="card">
              <div class="card-icon">⏱️</div>
              <div class="card-content">
                <div class="card-label">响应时间</div>
                <div class="card-value">{{ avgLat }}</div>
                <div class="card-subvalue card-subvalue-placeholder">&nbsp;</div>
                <div class="card-unit">ms</div>
              </div>
            </div>
            <div class="card">
              <div class="card-icon">📈</div>
              <div class="card-content">
                <div class="card-label">吞吐量</div>
                <div class="card-value">{{ avgBw }}</div>
                <div v-if="mixStatsMode" class="card-subvalue">读 {{ avgReadBw }} / 写 {{ avgWriteBw }}</div>
                <div v-else class="card-subvalue card-subvalue-placeholder">&nbsp;</div>
                <div class="card-unit">MB/s</div>
              </div>
            </div>
            <div class="card card-empty">
              <div class="card-icon">⏰</div>
              <div class="card-content">
                <div class="card-label">运行时长</div>
                <div class="card-value">{{ elapsedTime }}</div>
                <div class="card-subvalue card-subvalue-placeholder">&nbsp;</div>
                <div class="card-unit"></div>
              </div>
            </div>
          </div>

          <div class="chart-wrapper">
            <h3>吞吐量 (MB/s)</h3>
            <canvas ref="bwChart"></canvas>
          </div>
        </div>

        <div class="middle-row">
          <div class="chart-wrapper">
            <h3>I/O 速率 (IOPS)</h3>
            <canvas ref="iopsChart"></canvas>
          </div>
          <div class="chart-wrapper">
            <h3>响应时间 (ms)</h3>
            <canvas ref="latChart"></canvas>
          </div>
        </div>

        <div class="terminal-section">
          <div class="terminal-header">
            <div class="terminal-title-group">
              <span class="terminal-title">终端输出</span>
            </div>
            <div class="terminal-controls">
              <button class="btn btn-compact" @click="clearOutput">清空</button>
              <button class="btn btn-compact" @click="downloadOutput">下载</button>
            </div>
          </div>

          <div v-if="isTesting" class="progress-container">
            <div class="progress-bar">
              <div class="progress-fill" :style="{ width: progressPercent + '%' }"></div>
            </div>
            <div class="progress-text">{{ elapsedTime }} / {{ params.runtime }}s ({{ progressPercent.toFixed(0) }}%)</div>
          </div>

          <div v-if="connectedHosts.length > 0" class="host-tabs">
            <div
              v-for="(host, index) in connectedHosts"
              :key="index"
              class="host-tab"
              :class="{ active: activeHostTab === host }"
              @click="activeHostTab = host"
            >
              {{ host }}
            </div>
          </div>

          <div class="terminal-window" ref="terminalWindow">
            <div v-if="getCurrentTerminalLines().length === 0" class="terminal-empty">
              配置参数后点击"开始测试"...
            </div>
            <div
              v-for="(line, index) in getCurrentTerminalLines()"
              :key="index"
              class="terminal-line"
              :class="line.type"
            >
              {{ line.text }}
            </div>
          </div>
        </div>

        <div class="command-section">
          <div class="command-header">
            <span class="command-title">FIO 完整运行命令</span>
            <button v-if="displayFioCommand" class="btn btn-compact" @click="copyDisplayCommand">复制</button>
          </div>
          <div v-if="!displayFioCommand" class="command-empty">启动任务后显示实际执行的完整命令</div>
          <div v-else class="command-item">
            <code>{{ displayFioCommand }}</code>
          </div>
        </div>

        <div class="result-section">
          <div class="result-header">
            <div>
              <h2 class="result-title">测试结果汇总</h2>
              <p>按块大小和读写类型展示，保留最近 10 条测试记录</p>
            </div>
            <button class="btn btn-compact" :disabled="!hasCopyableResult" @click="copyTestResult">复制结果</button>
          </div>
          <div class="result-table-wrap">
            <table class="result-table">
              <thead>
                <tr>
                  <th>测试类型</th>
                  <th>numjobs</th>
                  <th>iodepth</th>
                  <th>平均 IOPS</th>
                  <th>平均吞吐</th>
                  <th>平均响应时间</th>
                </tr>
              </thead>
              <tbody>
                <tr v-for="row in resultRows" :key="row.id" :class="{ 'result-row-current': row.current }">
                  <td>{{ row.label }}</td>
                  <td>{{ row.numjobs }}</td>
                  <td>{{ row.iodepth }}</td>
                  <td>{{ row.iops }}</td>
                  <td>{{ row.bw }}</td>
                  <td>{{ row.lat }}</td>
                </tr>
              </tbody>
            </table>
          </div>
        </div>
      </div>
    </div>

    <div class="notification" :class="notification.type" v-if="notification.show">
      {{ notification.message }}
    </div>
  </div>
</template>

<script setup>
import { ref, reactive, computed, onMounted, onUnmounted, nextTick, watch, toRaw } from 'vue'
import Chart from 'chart.js/auto'
import PageHeader from '@/components/common/PageHeader.vue'

const API_BASE = `/api/v1/perf/fio`

let ws = null
const wsConnected = ref(false)
let wsConnectPromise = null
let pendingJoinTaskId = null

const hostsText = ref('')

const config = reactive({
  username: 'root',
  auth_method: 'password',
  password: '',
  port: 22
})

const params = reactive({
  rw: 'randread',
  rwmixread: 70,
  bs: '4k',
  iodepth: 64,
  numjobs: 4,
  runtime: 60,
  ioengine: 'libaio',
  filename: '',
  pool: 'pool-rbdtest1',
  cpus_allowed: ''
})

const rwLabels = {
  randread: '随机读',
  randwrite: '随机写',
  randrw: '混合读写',
  read: '顺序读',
  write: '顺序写'
}

const isTesting = ref(false)
const isStopping = ref(false)
const testCompleted = ref(false)
const finishHandledTaskId = ref('')
const taskRunToken = ref(0)
const taskId = ref('')
const pollTimer = ref(null)
const statusPollTimer = ref(null)
const elapsedSeconds = ref(0)

const currentStats = reactive({ iops: 0, bw: 0, lat: 0, readIops: 0, writeIops: 0, readBw: 0, writeBw: 0 })
const avgIops = ref('--')
const avgBw = ref('--')
const avgLat = ref('--')
const avgReadIops = ref('--')
const avgWriteIops = ref('--')
const avgReadBw = ref('--')
const avgWriteBw = ref('--')
const mixStatsMode = computed(() => params.rw === 'randrw')
const maxStats = reactive({ iops: 0, bw: 0, lat: 0 })
const minStats = reactive({ iops: 999999999, bw: 999999999, lat: 999999999 })
const finalStats = reactive({ iops: 0, bw: 0, lat: 0 })

const hostStats = ref({})
const fioCommands = ref({})
let statsAggregationTimer = null

const iopsChart = ref(null)
const bwChart = ref(null)
const latChart = ref(null)
let charts = { iops: null, bw: null, lat: null }
const chartData = reactive({
  iops: [],
  bw: [],
  lat: [],
  labels: []
})

const terminalLines = ref([])
const terminalWindow = ref(null)
const hostTerminals = ref({})
const activeHostTab = ref('')

const validationResults = ref([])
const connectedHosts = ref([])
const cpusAllowedByHost = ref({})
const scannedDevices = ref([])
const selectedDevices = ref([])
const manualDevices = ref('')

const notification = reactive({
  show: false,
  message: '',
  type: 'info'
})

watch(() => params.ioengine, (newEngine) => {
  if (newEngine === 'rbd') {
    if (!params.filename) {
      params.filename = 'rbdtest1'
    }
  } else {
    if (params.filename === 'rbdtest1') {
      params.filename = ''
    }
  }
})

const canValidate = computed(() => {
  const validHosts = getValidHosts()
  return validHosts.length > 0 && config.username &&
    (config.auth_method === 'key' || config.password)
})

const canStart = computed(() => {
  const validHosts = getValidHosts()
  const targetReady = params.ioengine === 'rbd' ? !!params.filename : getSelectedBlockDevices().length > 0
  return targetReady && validHosts.length > 0 && validationResults.value.length > 0 &&
    validationResults.value.every(r => r.status === 'success' && r.has_fio)
})

const elapsedTime = computed(() => {
  const mins = Math.floor(elapsedSeconds.value / 60)
  const secs = elapsedSeconds.value % 60
  return `${mins.toString().padStart(2, '0')}:${secs.toString().padStart(2, '0')}`
})

const progressPercent = computed(() => {
  return Math.min(100, (elapsedSeconds.value / params.runtime) * 100)
})

const supportedResultTypes = ['randwrite', 'randread', 'randrw', 'write', 'read']
const resultHistory = reactive([])
const blockSizeLabel = value => {
  const text = String(value || '').trim()
  return text.replace(/k$/i, 'K').replace(/m$/i, 'M').replace(/g$/i, 'G')
}
const resultTypeLabel = (bs, rw) => `${blockSizeLabel(bs)}${rwLabels[rw] || rw}`
const blockSizeToMiB = value => {
  const match = String(value || '').trim().match(/^(\d+(?:\.\d+)?)([kKmMgG])$/)
  if (!match) return 0
  const number = Number(match[1])
  const unit = match[2].toLowerCase()
  if (unit === 'k') return number / 1024
  if (unit === 'm') return number
  if (unit === 'g') return number * 1024
  return 0
}
const bandwidthFromIops = (iops, bs) => Number(iops) * blockSizeToMiB(bs)
const activeTestConfig = ref(null)
const hasTestResult = computed(() => chartData.iops.length > 0)
const hasCopyableResult = computed(() => resultHistory.length > 0 || (isTesting.value && hasTestResult.value))
const resultRows = computed(() => {
  const rows = resultHistory.map(row => ({ ...row, current: false }))
  if (isTesting.value && hasTestResult.value && supportedResultTypes.includes(params.rw)) {
    rows.unshift({
      id: 'current',
      label: resultTypeLabel(params.bs, params.rw),
      iops: currentStats.iops.toFixed(0),
      bw: `${bandwidthFromIops(currentStats.iops, params.bs).toFixed(1)} MiB/s`,
      lat: `${currentStats.lat.toFixed(2)} ms`,
      numjobs: params.numjobs,
      iodepth: params.iodepth,
      current: true
    })
  }
  return rows.slice(0, 10)
})
const testResultText = computed(() => [
  '测试类型\tnumjobs\tiodepth\t平均 IOPS\t平均吞吐\t平均响应时间',
  ...resultRows.value.map(row => `${row.label}\t${row.numjobs}\t${row.iodepth}\t${row.iops}\t${row.bw}\t${row.lat}`)
].join('\n'))

const parseIPRange = (text) => {
  const ips = []
  text.split('\n').forEach(line => {
    line = line.trim()
    if (!line) return

    const cidrMatch = line.match(/^(\d+\.\d+\.\d+\.)(\d+)\/(\d+)$/)
    if (cidrMatch) {
      const prefix = cidrMatch[1]
      const base = parseInt(cidrMatch[2])
      const bits = parseInt(cidrMatch[3])
      const count = Math.pow(2, 32 - bits)
      for (let i = 0; i < count; i++) {
        ips.push(prefix + (base + i))
      }
      return
    }

    const rangeMatch = line.match(/^(\d+\.\d+\.\d+\.)(\d+)-(\d+)$/)
    if (rangeMatch) {
      const prefix = rangeMatch[1]
      const start = parseInt(rangeMatch[2])
      const end = parseInt(rangeMatch[3])
      for (let i = start; i <= end; i++) {
        ips.push(prefix + i)
      }
      return
    }

    ips.push(line)
  })
  return ips
}

const getValidHosts = () => parseIPRange(hostsText.value)

const getSelectedBlockDevices = () => {
  const devices = [...selectedDevices.value]
  manualDevices.value
    .split(/[:\s,，]+/)
    .map(d => d.trim())
    .filter(Boolean)
    .forEach(d => {
      if (!devices.includes(d)) devices.push(d)
    })
  return devices
}

const allScannedDevicesSelected = computed(() =>
  scannedDevices.value.length > 0 &&
  scannedDevices.value.every(dev => selectedDevices.value.includes(dev.path))
)

const toggleAllScannedDevices = () => {
  if (allScannedDevicesSelected.value) {
    selectedDevices.value = []
  } else {
    selectedDevices.value = scannedDevices.value.map(dev => dev.path)
  }
}

const showNotification = (message, type = 'info') => {
  notification.message = message
  notification.type = type
  notification.show = true
  setTimeout(() => {
    notification.show = false
  }, 3000)
}

const addTerminalLine = (type, text, host = null) => {
  if (host && hostTerminals.value[host]) {
    hostTerminals.value[host].push({ type, text })
  } else {
    terminalLines.value.push({ type, text })
  }

  nextTick(() => {
    if (terminalWindow.value) {
      terminalWindow.value.scrollTop = terminalWindow.value.scrollHeight
    }
  })
}

const copyText = async (text) => {
  try {
    await navigator.clipboard.writeText(text)
    showNotification('命令已复制', 'success')
  } catch (error) {
    const textarea = document.createElement('textarea')
    textarea.value = text
    document.body.appendChild(textarea)
    textarea.select()
    document.execCommand('copy')
    document.body.removeChild(textarea)
    showNotification('命令已复制', 'success')
  }
}

const formatFioCommand = (command) => {
  const index = command.indexOf('fio ')
  return index >= 0 ? command.slice(index) : command
}

const displayFioCommand = computed(() => {
  const command = Object.values(fioCommands.value)[0]
  return command ? formatFioCommand(command) : ''
})

const copyDisplayCommand = () => copyText(displayFioCommand.value)

const copyTestResult = async () => {
  try {
    await navigator.clipboard.writeText(testResultText.value)
  } catch (error) {
    const textarea = document.createElement('textarea')
    textarea.value = testResultText.value
    document.body.appendChild(textarea)
    textarea.select()
    document.execCommand('copy')
    document.body.removeChild(textarea)
  }
  showNotification('测试结果已复制', 'success')
}

const clearOutput = () => {
  if (activeHostTab.value && hostTerminals.value[activeHostTab.value]) {
    hostTerminals.value[activeHostTab.value] = []
  } else {
    terminalLines.value = []
  }
}

const downloadOutput = () => {
  const lines = getCurrentTerminalLines()
  if (lines.length === 0) {
    alert('没有可下载的输出')
    return
  }

  const content = lines.map(line => line.text).join('\n')
  const blob = new Blob([content], { type: 'text/plain;charset=utf-8' })
  const url = URL.createObjectURL(blob)
  const link = document.createElement('a')
  link.href = url
  const hostname = activeHostTab.value || 'all'
  link.download = `fio_test_output_${hostname}_${new Date().toISOString().replace(/[:.]/g, '-')}.txt`
  document.body.appendChild(link)
  link.click()
  document.body.removeChild(link)
  URL.revokeObjectURL(url)
}

const getCurrentTerminalLines = () => {
  if (activeHostTab.value && hostTerminals.value[activeHostTab.value]) {
    return hostTerminals.value[activeHostTab.value]
  }
  return terminalLines.value
}

const connectWebSocket = () => {
  if (ws && wsConnected.value) {
    return Promise.resolve()
  }

  if (wsConnectPromise) {
    return wsConnectPromise
  }

  const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
  const wsUrl = `${protocol}//${window.location.host}/api/v1/perf/fio/ws`

  wsConnectPromise = new Promise((resolve, reject) => {
    ws = new WebSocket(wsUrl)

    ws.onopen = () => {
      wsConnected.value = true
      if (pendingJoinTaskId) {
        joinTask(pendingJoinTaskId)
        pendingJoinTaskId = null
      }
      resolve()
    }

    ws.onmessage = (event) => {
      try {
        const message = JSON.parse(event.data)
        handleWebSocketMessage(message)
      } catch (error) {
        console.error('WebSocket message parse error:', error)
      }
    }

    ws.onerror = (error) => {
      wsConnected.value = false
      wsConnectPromise = null
      reject(new Error('WebSocket 连接失败'))
    }

    ws.onclose = () => {
      wsConnected.value = false
      wsConnectPromise = null
    }
  })

  return wsConnectPromise
}

const handleWebSocketMessage = (message) => {
  const { type, data, stats, host, command } = message

  if (type === 'joined') {
    return
  } else if (type === 'output') {
    if (data) {
      let targetHost = host
      if (!targetHost && data.includes('[')) {
        const match = data.match(/\[([^\]]+)\]/)
        if (match) {
          targetHost = match[1]
        }
      }

      if (!targetHost && activeHostTab.value) {
        targetHost = activeHostTab.value
      }

      const lines = data.split('\n')
      lines.forEach(line => {
        if (line.trim()) {
          addTerminalLine('info', line, targetHost)
        }
      })
    }
  } else if (type === 'command') {
    if (host && command) {
      fioCommands.value = { ...fioCommands.value, [host]: command }
    }
  } else if (type === 'stats') {
    if (stats && host) {
      hostStats.value[host] = {
        iops: stats.iops || 0,
        bw: stats.bw_mb || 0,
        lat: (stats.latency_us / 1000) || 0,
        readIops: stats.read_iops || 0,
        writeIops: stats.write_iops || 0,
        readBw: stats.read_bw_mb || 0,
        writeBw: stats.write_bw_mb || 0,
        timestamp: Date.now()
      }

      if (statsAggregationTimer) {
        clearTimeout(statsAggregationTimer)
      }

      statsAggregationTimer = setTimeout(() => {
        aggregateAndUpdateStats()
      }, 200)
    }
  } else if (type === 'completed') {
    finishTest(message.status || 'completed')
  } else if (type === 'error') {
    addTerminalLine('error', message.error || '未知错误', host)
    finishTest('error')
  }
}

const joinTask = (taskId) => {
  if (ws && wsConnected.value) {
    ws.send(JSON.stringify({
      action: 'join',
      task_id: taskId
    }))
    return true
  }

  pendingJoinTaskId = taskId
  return false
}

const disconnectWebSocket = () => {
  pendingJoinTaskId = null
  wsConnectPromise = null
  if (ws) {
    ws.close()
    ws = null
    wsConnected.value = false
  }
}

const initCharts = () => {
  const createChart = (ref, key, color) => {
    if (!ref) return null
    return new Chart(ref, {
      type: 'line',
      data: {
        labels: chartData.labels,
        datasets: [{
          data: chartData[key],
          borderColor: color,
          backgroundColor: color + '20',
          borderWidth: 2,
          fill: true,
          tension: 0.4,
          pointRadius: 0
        }]
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        animation: false,
        plugins: { legend: { display: false } },
        scales: {
          x: { display: true, grid: { display: false } },
          y: { display: true, beginAtZero: true, grid: { color: '#f0f0f0' } }
        }
      }
    })
  }

  charts.iops = createChart(iopsChart.value, 'iops', '#6B5DD3')
  charts.bw = createChart(bwChart.value, 'bw', '#4CAF50')
  charts.lat = createChart(latChart.value, 'lat', '#ff9800')
}

const aggregateAndUpdateStats = () => {
  const hosts = Object.keys(hostStats.value)
  if (hosts.length === 0) return

  let totalIops = 0
  let totalBw = 0
  let totalLat = 0
  let totalReadIops = 0
  let totalWriteIops = 0
  let totalReadBw = 0
  let totalWriteBw = 0
  let latCount = 0

  hosts.forEach(host => {
    const stats = hostStats.value[host]
    totalIops += stats.iops
    totalBw += stats.bw
    totalReadIops += stats.readIops || 0
    totalWriteIops += stats.writeIops || 0
    totalReadBw += stats.readBw || 0
    totalWriteBw += stats.writeBw || 0
    if (stats.lat > 0) {
      totalLat += stats.lat
      latCount++
    }
  })

  currentStats.readIops = totalReadIops
  currentStats.writeIops = totalWriteIops
  currentStats.readBw = totalReadBw
  currentStats.writeBw = totalWriteBw

  const avgLatency = latCount > 0 ? totalLat / latCount : 0

  currentStats.iops = totalIops
  // Keep the displayed bandwidth mathematically consistent with IOPS and block size.
  currentStats.bw = bandwidthFromIops(totalIops, params.bs)
  currentStats.lat = avgLatency

  updateCharts()
}

const updateCharts = () => {
  chartData.labels.push(elapsedTime.value)
  chartData.iops.push(currentStats.iops)
  chartData.bw.push(currentStats.bw)
  chartData.lat.push(currentStats.lat)

  if (charts.iops) {
    charts.iops.data.labels = toRaw(chartData.labels)
    charts.iops.data.datasets[0].data = toRaw(chartData.iops)
    charts.iops.update('none')
  }

  if (charts.bw) {
    charts.bw.data.labels = toRaw(chartData.labels)
    charts.bw.data.datasets[0].data = toRaw(chartData.bw)
    charts.bw.update('none')
  }

  if (charts.lat) {
    charts.lat.data.labels = toRaw(chartData.labels)
    charts.lat.data.datasets[0].data = toRaw(chartData.lat)
    charts.lat.update('none')
  }
}

const validateHosts = async () => {
  const validHosts = getValidHosts()
  addTerminalLine('info', `验证 ${validHosts.length} 台主机连接...`)

  try {
    const response = await fetch(`${API_BASE}/validate-hosts`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        hosts: validHosts,
        username: config.username,
        auth_method: config.auth_method,
        password: config.password,
        port: config.port
      })
    })

    const data = await response.json()
    validationResults.value = data.results

    connectedHosts.value = data.results
      .filter(r => r.status === 'success' || r.status === 'warning')
      .map(r => r.host)

    connectedHosts.value.forEach(host => {
      if (!hostTerminals.value[host]) {
        hostTerminals.value[host] = []
      }
    })

    if (connectedHosts.value.length > 0 && !activeHostTab.value) {
      activeHostTab.value = connectedHosts.value[0]
    }

    const detectedCpuMap = {}
    data.results.forEach(r => {
      const icon = r.status === 'success' ? '✅' : r.status === 'warning' ? '⚠️' : '❌'
      addTerminalLine(r.status === 'error' ? 'error' : 'info', `${icon} ${r.host}: ${r.message}`)
      if (r.devices && r.devices.length > 0) {
        addTerminalLine('info', `${r.host} 盘符: ${r.devices.map(d => d.path).join(', ')}`)
      }
      detectedCpuMap[r.host] = r.recommended_cpus || ''
      addTerminalLine('info', `${r.host} CPU绑定: ${r.recommended_cpus || '不绑定'}`)
    })
    cpusAllowedByHost.value = detectedCpuMap
    const detectedRanges = Array.from(new Set(Object.values(detectedCpuMap).filter(Boolean)))
    params.cpus_allowed = detectedRanges.length === 1 ? detectedRanges[0] : ''

    const deviceMap = new Map()
    data.results.forEach(r => {
      ;(r.devices || []).forEach(d => {
        if (!deviceMap.has(d.path)) deviceMap.set(d.path, d)
      })
    })
    scannedDevices.value = Array.from(deviceMap.values()).sort((a, b) => a.path.localeCompare(b.path, undefined, { numeric: true }))
    selectedDevices.value = selectedDevices.value.filter(path => deviceMap.has(path))
    if (selectedDevices.value.length === 0 && scannedDevices.value.length > 0) {
      selectedDevices.value = [scannedDevices.value[0].path]
    }

    const hasError = data.results.some(r => r.status === 'error')
    const hasWarning = data.results.some(r => r.status === 'warning')

    if (data.results.every(r => r.status === 'success')) {
      showNotification('所有主机连接成功', 'success')
    } else if (hasError && hasWarning) {
      showNotification('部分主机连接失败，部分主机未安装 FIO', 'warning')
    } else if (hasError) {
      showNotification('部分主机连接失败', 'warning')
    } else if (hasWarning) {
      showNotification('部分主机未安装 FIO', 'warning')
    }
  } catch (error) {
    addTerminalLine('error', `验证失败: ${error.message}`)
    showNotification(`验证失败: ${error.message}`, 'error')
  }
}

const startTest = async () => {
  const validHosts = getValidHosts()
  taskRunToken.value++
  finishHandledTaskId.value = ''
  activeTestConfig.value = {
    rw: params.rw,
    bs: params.bs,
    numjobs: params.numjobs,
    iodepth: params.iodepth
  }

  let fioCommand = 'fio --direct=1'

  if (params.ioengine === 'rbd') {
    fioCommand += ` --ioengine=rbd --pool=${params.pool || 'pool-rbdtest1'} --rbdname=${params.filename || 'rbdtest1'}`
  } else {
    fioCommand += ` --filename=${getSelectedBlockDevices().join(':')} --ioengine=libaio`
  }

  fioCommand += ` --iodepth=${params.iodepth} --numjobs=${params.numjobs} --rw=${params.rw} --bs=${params.bs}`
  if (params.rw === 'randrw') {
    fioCommand += ` --rwmixread=${params.rwmixread}`
  }
  fioCommand += ` --group_reporting --name=mytest`
  fioCommand += ' --status-interval=1'

  if (params.runtime) {
    fioCommand += ` --runtime=${params.runtime} --time_based`
  }

  if (params.cpus_allowed && params.cpus_allowed.trim()) {
    fioCommand += ` --cpus_allowed=${params.cpus_allowed} --cpus_allowed_policy=split`
  }

  currentStats.iops = 0
  currentStats.bw = 0
  currentStats.lat = 0
  currentStats.readIops = 0
  currentStats.writeIops = 0
  currentStats.readBw = 0
  currentStats.writeBw = 0
  avgIops.value = '--'
  avgBw.value = '--'
  avgLat.value = '--'
  avgReadIops.value = '--'
  avgWriteIops.value = '--'
  avgReadBw.value = '--'
  avgWriteBw.value = '--'
  maxStats.iops = 0
  maxStats.bw = 0
  elapsedSeconds.value = 0
  chartData.iops = []
  chartData.bw = []
  chartData.lat = []
  chartData.labels = []
  hostStats.value = {}
  fioCommands.value = {}

  addTerminalLine('info', '='.repeat(50))
  addTerminalLine('info', '开始 FIO 测试')
  addTerminalLine('info', `类型: ${rwLabels[params.rw]}, 块大小: ${params.bs}, IO深度: ${params.iodepth}`)
  addTerminalLine('info', `线程: ${params.numjobs}, 时长: ${params.runtime}s, 主机数: ${validHosts.length}`)
  addTerminalLine('info', `命令: ${fioCommand}`)

  try {
    const response = await fetch(`${API_BASE}/start-test`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        hosts: validHosts,
        username: config.username,
        auth_method: config.auth_method,
        password: config.password,
        port: config.port,
        params: {
          rw: params.rw,
          rwmixread: params.rwmixread,
          bs: params.bs,
          iodepth: params.iodepth,
          numjobs: params.numjobs,
          runtime: params.runtime,
          ioengine: params.ioengine,
          filename: params.ioengine === 'rbd' ? params.filename : getSelectedBlockDevices().join(':'),
          selected_devices: params.ioengine === 'rbd' ? [] : getSelectedBlockDevices(),
          pool: params.pool,
          cpus_allowed: params.cpus_allowed,
          cpus_allowed_by_host: cpusAllowedByHost.value
        }
      })
    })

    const data = await response.json()

    if (data.status === 'success') {
      taskId.value = data.task_id
      isTesting.value = true
      testCompleted.value = false

      await connectWebSocket()
      joinTask(taskId.value)
      startElapsedTimer()
      startStatusPolling()

      showNotification('测试已启动', 'success')
    } else {
      addTerminalLine('error', `启动失败: ${data.error}`)
      showNotification(`启动失败: ${data.error}`, 'error')
    }
  } catch (error) {
    addTerminalLine('error', `启动失败: ${error.message}`)
    showNotification(`启动失败: ${error.message}`, 'error')
  }
}

const stopTest = async () => {
  if (!taskId.value || isStopping.value) return
  isStopping.value = true
  addTerminalLine('warning', '正在停止所有源端 FIO 进程...')

  try {
    const response = await fetch(`${API_BASE}/stop-test`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ task_id: taskId.value })
    })
    const data = await response.json()
    if (!response.ok || data.status !== 'success') {
      const details = (data.results || [])
        .filter(result => !result.success)
        .map(result => `${result.host}: ${result.error || '停止失败'}`)
        .join('；')
      throw new Error(details || data.error || '停止失败')
    }

    isTesting.value = false
    if (pollTimer.value) {
      clearInterval(pollTimer.value)
      pollTimer.value = null
    }
    if (statusPollTimer.value) {
      clearInterval(statusPollTimer.value)
      statusPollTimer.value = null
    }

    data.results.forEach(result => addTerminalLine('success', `${result.host}: FIO 已停止`))
    addTerminalLine('warning', '测试已手动停止')
    showNotification('所有源端 FIO 已停止', 'success')
  } catch (error) {
    addTerminalLine('error', `停止失败: ${error.message}`)
    showNotification(`停止失败: ${error.message}`, 'error')
  } finally {
    isStopping.value = false
  }
}

const startStatusPolling = () => {
  if (statusPollTimer.value) {
    clearInterval(statusPollTimer.value)
  }

  statusPollTimer.value = setInterval(async () => {
    if (!taskId.value || !isTesting.value) return
    try {
      const response = await fetch(`${API_BASE}/get-output?task_id=${taskId.value}`)
      if (!response.ok) return
      const data = await response.json()
      if (data.commands && Object.keys(data.commands).length > 0) {
        fioCommands.value = { ...data.commands }
      }
      if (data.host_stats) {
        Object.entries(data.host_stats).forEach(([host, stats]) => {
          hostStats.value[host] = {
            iops: stats.iops || 0,
            bw: stats.bw_mb || 0,
            lat: (stats.latency_us / 1000) || 0,
            readIops: stats.read_iops || 0,
            writeIops: stats.write_iops || 0,
            readBw: stats.read_bw_mb || 0,
            writeBw: stats.write_bw_mb || 0,
            timestamp: Date.now()
          }
        })
        if (Object.keys(data.host_stats).length > 0) {
          aggregateAndUpdateStats()
        }
      } else if (data.stats) {
        currentStats.iops = data.stats.iops || currentStats.iops
        currentStats.bw = data.stats.bw_mb || currentStats.bw
        currentStats.lat = data.stats.latency_us ? data.stats.latency_us / 1000 : currentStats.lat
        updateCharts()
      }
      if (['completed', 'partial', 'stopped', 'error'].includes(data.status)) {
        finishTest(data.status)
      }
    } catch (error) {
      console.warn('状态轮询失败:', error)
    }
  }, 2000)
}

const startElapsedTimer = () => {
  if (pollTimer.value) {
    clearInterval(pollTimer.value)
  }

  pollTimer.value = setInterval(() => {
    elapsedSeconds.value++
  }, 1000)
}

const finishTest = (status = 'completed') => {
  // WebSocket and status polling can report completion at the same time.
  if (!taskId.value || finishHandledTaskId.value === taskId.value) return
  finishHandledTaskId.value = taskId.value

  isTesting.value = false
  testCompleted.value = status === 'completed' || status === 'partial'

  if (pollTimer.value) {
    clearInterval(pollTimer.value)
    pollTimer.value = null
  }

  if (statsAggregationTimer) {
    clearTimeout(statsAggregationTimer)
    statsAggregationTimer = null
  }
  if (statusPollTimer.value) {
    clearInterval(statusPollTimer.value)
    statusPollTimer.value = null
  }

  if (chartData.iops.length > 0) {
    const sumIops = chartData.iops.reduce((a, b) => a + b, 0)
    avgIops.value = (sumIops / chartData.iops.length).toFixed(0)
  }

  if (avgIops.value !== '--' && activeTestConfig.value) {
    // Use the same rounded IOPS shown in the table so users can verify BW = IOPS x BS.
    avgBw.value = bandwidthFromIops(avgIops.value, activeTestConfig.value.bs).toFixed(1)
  }

  if (chartData.lat.length > 0) {
    const sumLat = chartData.lat.reduce((a, b) => a + b, 0)
    avgLat.value = (sumLat / chartData.lat.length).toFixed(2)
  }

  if (mixStatsMode.value) {
    avgReadIops.value = currentStats.readIops.toFixed(0)
    avgWriteIops.value = currentStats.writeIops.toFixed(0)
    avgReadBw.value = currentStats.readBw.toFixed(1)
    avgWriteBw.value = currentStats.writeBw.toFixed(1)
  }

  finalStats.iops = parseFloat(avgIops.value) || 0
  finalStats.bw = parseFloat(avgBw.value) || 0
  finalStats.lat = parseFloat(avgLat.value) || 0

  const completedConfig = activeTestConfig.value
  if (completedConfig && supportedResultTypes.includes(completedConfig.rw) && chartData.iops.length > 0 && status !== 'error') {
    resultHistory.unshift({
      id: `${Date.now()}-${completedConfig.rw}`,
      label: resultTypeLabel(completedConfig.bs, completedConfig.rw),
      iops: avgIops.value,
      bw: `${avgBw.value} MiB/s`,
      lat: `${avgLat.value} ms`,
      numjobs: completedConfig.numjobs,
      iodepth: completedConfig.iodepth,
      current: false
    })
    if (resultHistory.length > 10) resultHistory.splice(10)
  }

  const statusText = status === 'partial' ? '测试部分完成' : status === 'stopped' ? '测试已停止' : status === 'error' ? '测试失败' : '测试完成'
  const lineType = status === 'error' ? 'error' : status === 'stopped' ? 'warning' : 'success'
  const notificationType = status === 'error' ? 'error' : status === 'stopped' ? 'info' : status === 'partial' ? 'warning' : 'success'

  addTerminalLine(lineType, '='.repeat(50))
  addTerminalLine(lineType, `${statusText}!`)
  addTerminalLine(lineType, `平均 IOPS: ${avgIops.value}, 平均带宽: ${avgBw.value} MiB/s, 平均延迟: ${avgLat.value} ms`)
  if (mixStatsMode.value) {
    addTerminalLine(lineType, `混合读写拆分: 读 IOPS ${avgReadIops.value}, 写 IOPS ${avgWriteIops.value}, 读带宽 ${avgReadBw.value} MB/s, 写带宽 ${avgWriteBw.value} MB/s`)
  }

  showNotification(statusText, notificationType)
}

onUnmounted(() => {
  if (pollTimer.value) {
    clearInterval(pollTimer.value)
  }
  if (statusPollTimer.value) {
    clearInterval(statusPollTimer.value)
  }
  Object.values(charts).forEach(chart => {
    if (chart) chart.destroy()
  })
  disconnectWebSocket()
})

onMounted(() => {
  nextTick(() => {
    initCharts()
    Object.values(charts).forEach(chart => {
      if (chart) chart.update('none')
    })
  })
})
</script>

<style scoped>
.fio-page {
  min-height: 100vh;
  padding: 20px;
  background: linear-gradient(135deg, #6B5DD3 0%, #8B7FE8 50%, #9B8FF8 100%);
  position: relative;
}
.fio-page::before { content: ''; position: fixed; top: 0; left: 0; right: 0; bottom: 0; background: repeating-linear-gradient(45deg, transparent, transparent 35px, rgba(255,255,255,0.05) 35px, rgba(255,255,255,0.05) 70px); pointer-events: none; z-index: 0; }
.fio-page > * { position: relative; z-index: 1; }
.fio-page, .main-content, .right-panel, .top-row, .summary-cards, .middle-row, .terminal-section, .chart-wrapper { min-width: 0; }

.main-content {
  display: grid;
  grid-template-columns: 360px minmax(0, 1fr);
  gap: 20px;
}

.left-panel {
  display: flex;
  flex-direction: column;
  gap: 15px;
}

.section {
  background: rgba(255, 255, 255, 0.95);
  border-radius: 12px;
  padding: 20px;
  box-shadow: 0 4px 6px rgba(0, 0, 0, 0.1);
}

.section-title {
  font-size: 18px;
  font-weight: 600;
  color: #333;
  margin-bottom: 15px;
  padding-bottom: 10px;
  border-bottom: 2px solid #6B5DD3;
}

.form-group { margin-bottom: 15px; }
.form-group label { display: block; font-size: 13px; color: #666; margin-bottom: 8px; }
.form-group input,
.form-group select,
.form-group textarea {
  width: 100%;
  padding: 10px 12px;
  border: 1px solid #e0e0e0;
  border-radius: 8px;
  font-size: 14px;
  transition: all 0.3s;
}
.form-group input:focus,
.form-group select:focus,
.form-group textarea:focus { outline: none; border-color: #6B5DD3; box-shadow: 0 0 0 3px rgba(107, 93, 211, 0.1); }
.form-group textarea { resize: vertical; min-height: 70px; font-family: inherit; }
.device-label-row { display: flex; align-items: center; justify-content: space-between; margin-bottom: 8px; padding-right: 14px; box-sizing: border-box; }
.device-label-row > label:first-child { margin-bottom: 0; }
.device-select-all { display: inline-flex !important; align-items: center; gap: 6px; margin: 0 !important; cursor: pointer; color: #555; }
.device-select-all input[type="checkbox"] { width: 16px !important; min-width: 16px; height: 16px; margin: 0; padding: 0; box-shadow: none; }
.device-list { display: grid; gap: 6px; max-height: 150px; overflow-y: auto; padding-right: 4px; }
.device-item { position: relative; display: block; padding: 8px 34px 8px 10px; border: 1px solid #e0e0e0; border-radius: 8px; background: #fff; cursor: pointer; }
.device-item.active { border-color: #6B5DD3; background: #f4f1ff; }
.device-item input[type="checkbox"] { position: absolute; right: 10px; top: 50%; transform: translateY(-50%); width: 16px !important; min-width: 16px; height: 16px; padding: 0; margin: 0; box-shadow: none; }
.device-info { min-width: 0; display: flex; align-items: baseline; gap: 3px; white-space: nowrap; overflow: hidden; padding-right: 4px; }
.device-info span { font-weight: 600; color: #333; flex-shrink: 0; }
.device-info small { color: #777; font-size: 11px; min-width: 0; overflow: hidden; text-overflow: ellipsis; }
.device-empty { padding: 12px; border: 1px dashed #ccc; border-radius: 8px; color: #777; font-size: 12px; background: #fafafa; }
.device-manual-input { margin-top: 10px; }
.form-row { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; }
.btn-full { width: 100%; }
.validation-summary { margin-top: 10px; text-align: center; }
.badge { padding: 4px 12px; border-radius: 20px; font-size: 13px; }
.badge.success { background: #e8f5e9; color: #4CAF50; }
.badge.warning { background: #fff3e0; color: #ff9800; }
.badge.error { background: #ffebee; color: #f44336; }
.test-type-grid { display: grid; grid-template-columns: repeat(3, 1fr); gap: 8px; }
.radio-card { padding: 10px 5px; border: 2px solid #e0e0e0; border-radius: 8px; cursor: pointer; text-align: center; transition: all 0.3s; }
.radio-card:hover { border-color: #6B5DD3; }
.radio-card.active { border-color: #6B5DD3; background: rgba(107, 93, 211, 0.1); }
.radio-card input { display: none; }
.radio-icon { font-size: 24px; display: block; margin-bottom: 5px; }
.radio-name { font-size: 12px; color: #333; }
.mix-slider { display: flex; align-items: center; gap: 10px; }
.mix-slider input { flex: 1; }
.mix-label { font-size: 13px; color: #666; min-width: 80px; }
.params-grid { display: grid; grid-template-columns: repeat(2, 1fr); gap: 12px; }
.actions-section { display: flex; flex-direction: column; gap: 10px; }
.right-panel { display: flex; flex-direction: column; gap: 15px; min-width: 0; }
.top-row { display: grid; grid-template-columns: minmax(0, 1fr) minmax(0, 1fr); gap: 15px; min-width: 0; }
.summary-cards { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 15px; min-width: 0; }
.card { background: white; padding: 20px; border-radius: 12px; box-shadow: 0 4px 6px rgba(0, 0, 0, 0.1); display: flex; align-items: center; gap: 15px; }
.card-icon { font-size: 20px; }
.card-content { flex: 1; min-width: 0; display: flex; flex-direction: column; }
.card-label { font-size: 13px; color: #999; margin-bottom: 5px; }
.card-value { font-size: 16px; line-height: 1.6; font-weight: bold; color: #6B5DD3; }
.card-subvalue { margin-top: 4px; font-size: 12px; color: #666; line-height: 1.4; min-height: 17px; }
.card-subvalue-placeholder { visibility: hidden; }
.card-unit { font-size: 12px; color: #999; margin-top: 2px; }
.middle-row { display: grid; grid-template-columns: minmax(0, 1fr) minmax(0, 1fr); gap: 15px; min-width: 0; }
.chart-wrapper { background: white; padding: 15px; border-radius: 12px; box-shadow: 0 4px 6px rgba(0, 0, 0, 0.1); min-width: 0; overflow: hidden; }
.chart-wrapper h3 { font-size: 14px; color: #333; margin-bottom: 10px; padding-bottom: 8px; border-bottom: 2px solid #f0f0f0; }
.chart-wrapper canvas { display: block; width: 100% !important; max-width: 100% !important; max-height: 200px !important; height: 200px !important; }
.card-empty .card-unit { visibility: hidden; }
.terminal-section { background: rgba(255, 255, 255, 0.95); border-radius: 12px; padding: 20px; box-shadow: 0 4px 6px rgba(0, 0, 0, 0.1); display: flex; flex-direction: column; min-width: 0; overflow: hidden; }
.terminal-header { display: flex; justify-content: space-between; align-items: flex-start; margin: 0; margin-bottom: 15px; flex-shrink: 0; }
.terminal-title { font-size: 16px; font-weight: 600; color: #333; }
.terminal-controls { display: flex; gap: 10px; }
.progress-container { margin-bottom: 15px; }
.progress-bar { height: 6px; background: #e0e0e0; border-radius: 3px; overflow: hidden; }
.progress-fill { height: 100%; background: linear-gradient(90deg, #6B5DD3, #8B7FE8); transition: width 1s linear; }
.progress-text { margin-top: 5px; font-size: 12px; color: #666; text-align: center; }
.host-tabs { display: flex; gap: 8px; margin-bottom: 10px; flex-wrap: wrap; }
.host-tab { padding: 6px 12px; background: #f0f0f0; border-radius: 20px; font-size: 12px; cursor: pointer; transition: all 0.3s; }
.host-tab.active { background: #6B5DD3; color: white; }
.terminal-window { background: #1a1a1a; color: #00ff00; font-family: 'Courier New', monospace; font-size: 12px; padding: 15px; border-radius: 8px; height: 300px; overflow-y: auto; overflow-x: auto; white-space: pre-wrap; line-height: 1.5; max-width: 100%; box-sizing: border-box; }
.terminal-empty { color: #666; text-align: center; padding: 40px; }
.terminal-line.error { color: #ff6b6b; }
.terminal-line.success { color: #51cf66; }
.command-section { background: rgba(255, 255, 255, 0.95); border-radius: 12px; padding: 16px 20px; box-shadow: 0 4px 6px rgba(0, 0, 0, 0.1); min-width: 0; }
.command-header { display: flex; align-items: center; justify-content: space-between; gap: 12px; margin-bottom: 10px; }
.command-title { font-size: 16px; font-weight: 600; color: #333; }
.command-empty { color: #888; font-size: 13px; padding: 14px; border: 1px dashed #ccc; border-radius: 8px; }
.command-list { display: grid; gap: 10px; }
.command-item { min-width: 0; background: #171923; border-radius: 8px; padding: 10px 12px; }
.command-host-row { display: flex; align-items: center; justify-content: space-between; margin-bottom: 6px; }
.command-host { color: #8bdbff; font-size: 12px; font-weight: 700; }
.command-copy { border: 0; border-radius: 5px; padding: 3px 9px; color: white; background: #6B5DD3; cursor: pointer; font-size: 11px; }
.command-item code { display: block; color: #d7f9df; font-size: 12px; line-height: 1.55; white-space: pre-wrap; overflow-wrap: anywhere; user-select: text; }
.result-section { background: rgba(255, 255, 255, 0.95); border-radius: 12px; padding: 20px; box-shadow: 0 4px 8px rgba(0, 0, 0, 0.1); min-width: 0; }
.result-header { display: flex; align-items: center; justify-content: space-between; gap: 16px; margin-bottom: 18px; }
.result-title { margin: 0 0 4px; color: #302944; font-size: 18px; }
.result-header p { margin: 0; color: #8a8395; font-size: 12px; }
.result-table-wrap { overflow-x: auto; border: 1px solid #b9c8e8; border-radius: 8px; }
.result-table { width: 100%; min-width: 720px; border-collapse: collapse; color: #34394a; font-size: 14px; }
.result-table th, .result-table td { padding: 15px 14px; text-align: center; border-right: 1px solid #d6def0; border-bottom: 1px solid #d6def0; font-variant-numeric: tabular-nums; }
.result-table th:last-child, .result-table td:last-child { border-right: 0; }
.result-table tbody tr:last-child td { border-bottom: 0; }
.result-table th { background: #bdcdec; color: #25304a; font-weight: 700; white-space: nowrap; }
.result-table td { background: #eef3fc; }
.result-table td:first-child { text-align: left; font-weight: 600; }
.result-table tbody tr:nth-child(even) td { background: #e7eefb; }
.result-table tbody tr.result-row-current td { background: #fff4d8; }
.notification { position: fixed; bottom: 20px; right: 20px; padding: 12px 20px; border-radius: 8px; color: white; font-size: 14px; box-shadow: 0 4px 12px rgba(0, 0, 0, 0.2); z-index: 9999; animation: slideIn 0.3s ease; }
.notification.success { background: #4CAF50; }
.notification.error { background: #f44336; }
.notification.info { background: #2196F3; }
.notification.warning { background: #ff9800; }
@keyframes slideIn { from { transform: translateX(100%); opacity: 0; } to { transform: translateX(0); opacity: 1; } }
@media (max-width: 768px) {
  .main-content { grid-template-columns: 1fr; }
  .top-row, .middle-row, .summary-cards { grid-template-columns: 1fr; }
  .form-row, .params-grid, .summary-cards { grid-template-columns: 1fr; }
  .test-type-grid { grid-template-columns: repeat(2, 1fr); }
}
</style>
