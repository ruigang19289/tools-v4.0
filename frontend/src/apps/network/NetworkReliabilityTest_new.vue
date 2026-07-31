<template>
  <div class="bandwidth-test-page">
    <PageHeader
      icon="🚀"
      title="网络带宽测试"
    />

    <div class="main-content">
      <!-- Left Panel: Configuration -->
      <div class="left-panel">
        <!-- Host Configuration -->
        <div class="section">
          <h2 class="section-title">主机配置</h2>

          <div class="form-group">
            <label>主机列表 (每行一个IP或范围):</label>
            <textarea
              v-model="config.hostsText"
              rows="4"
              placeholder="192.168.1.1
192.168.1.2
192.168.1.10-20"
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

          <button class="btn btn-primary btn-full" @click="validateHosts" :disabled="!canValidate || validating">
            验证连接
          </button>

          <div v-if="validationResults.length > 0" class="validation-summary">
            <span class="badge success" v-if="validationResults.every(r => r.status === 'success')">
              ✅ 全部连接成功
            </span>
            <span class="badge warning" v-else-if="validationResults.some(r => r.status === 'success')">
              ⚠️ 部分成功 ({{ validationResults.filter(r => r.status === 'success').length }}/{{ hosts.length }})
            </span>
            <span class="badge error" v-else>
              ❌ 连接失败
            </span>
          </div>

          <!-- 已验证主机列表 -->
          <div class="host-list-section" v-if="validatedHosts.length > 0">
            <h3 class="host-list-title">已验证主机 ({{ validatedHosts.length }})</h3>
            <div class="host-list">
              <div
                v-for="host in validatedHosts"
                :key="host.ip"
                :class="['host-item', { selected: selectedHosts.includes(host.ip) }]"
                @click="toggleHost(host.ip)"
              >
                <span class="host-ip">{{ host.ip }}</span>
                <span :class="['host-status', host.status]">{{ host.status === 'success' ? '✓' : '✗' }}</span>
              </div>
            </div>
            <div class="host-list-actions">
              <button class="btn btn-secondary btn-compact" @click="selectAll">全选</button>
              <button class="btn btn-secondary btn-compact" @click="clearSelection">清空</button>
            </div>
          </div>
        </div>

        <!-- Test Configuration -->
        <div class="section">
          <h2 class="section-title">测试配置</h2>

          <div class="form-row">
            <div class="form-group">
              <label>测试网段:</label>
              <input type="text" v-model="config.testNetwork" placeholder="192.168.34.0/24">
            </div>
            <div class="form-group">
              <label>测试模式:</label>
              <select v-model="config.testMode">
                <option value="one2one">One2One</option>
                <option value="roundrobin">RoundRobin</option>
                <option value="alltest">AllTest</option>
              </select>
            </div>
          </div>

          <div class="form-row-three">
            <div class="form-group">
              <label>起始端口:</label>
              <input type="number" v-model.number="config.portMin" min="1024" max="65535">
            </div>
            <div class="form-group">
              <label>并发数:</label>
              <input type="number" v-model.number="config.cnum" min="1" max="16">
            </div>
            <div class="form-group">
              <label>测试时长 (秒):</label>
              <input type="number" v-model.number="config.duration" min="5" max="300">
            </div>
          </div>

          <div class="form-group checkbox-group">
            <label class="checkbox-label">
              <input type="checkbox" v-model="config.useCpuBinding">
              <span>启用 CPU 绑定 (taskset)</span>
            </label>
          </div>

          <div class="form-group">
            <label>起始 CPU 核心:</label>
            <input type="number" v-model.number="config.coreMin" min="0" max="128" :disabled="!config.useCpuBinding">
          </div>

          <button
            class="btn btn-primary btn-full"
            @click="startTest"
            :disabled="loading || !canStartTest"
          >
            {{ loading ? '测试中...' : '开始测试' }}
          </button>

          <button
            v-if="loading"
            class="btn btn-danger btn-full"
            @click="stopTest"
            style="margin-top: 10px;"
          >
            停止测试
          </button>
        </div>
      </div>

      <!-- Right Panel: Results -->
      <div class="right-panel">
        <!-- Terminal Output -->
        <div class="section terminal-section">
          <div class="terminal-header">
            <h2 class="section-title">终端输出</h2>
            <div class="terminal-controls">
              <button class="btn btn-compact" @click="clearLog">清空</button>
              <button class="btn btn-compact" @click="downloadLog">下载</button>
              <label class="detailed-log-toggle">
                <input type="checkbox" v-model="showDetailedLog">
                <span>详细日志</span>
              </label>
            </div>
          </div>
          <div class="terminal-output" ref="logOutput">
            <div v-if="filteredLog.length === 0" class="empty-log">
              等待测试开始...
            </div>
            <div v-else>
              <div v-for="(line, idx) in filteredLog" :key="idx" class="log-line">
                {{ line }}
              </div>
            </div>
          </div>
        </div>

        <!-- Test Results -->
        <div class="section results-section-fixed">
          <div class="terminal-header">
            <h2 class="section-title">测试结果</h2>
            <div class="terminal-controls">
              <button
                v-if="testResults && (testResults.one2one.length > 0 || testResults.roundrobin.length > 0)"
                class="btn btn-compact"
                @click="downloadResults"
              >
                下载
              </button>
              <button
                v-if="testResults && (testResults.one2one.length > 0 || testResults.roundrobin.length > 0)"
                class="btn btn-compact"
                @click="clearResults"
              >
                清空
              </button>
            </div>
          </div>
          <div class="results-output">
            <div v-if="!testResults || (testResults.one2one.length === 0 && testResults.roundrobin.length === 0)" class="empty-results">
              等待测试完成...
            </div>
            <div v-else class="results-table-container">
              <table class="results-table">
                <thead>
                  <tr>
                    <th>客户端</th>
                    <th>服务端</th>
                    <th>发送传输</th>
                    <th>发送带宽</th>
                    <th>接收传输</th>
                    <th>接收带宽</th>
                    <th>重传数</th>
                  </tr>
                </thead>
                <tbody>
                  <template v-if="testResults.one2one && testResults.one2one.length > 0">
                    <tr v-for="(result, idx) in testResults.one2one" :key="'one2one-' + idx">
                      <td>{{ result.client }}</td>
                      <td>{{ result.server }}</td>
                      <td>{{ result.sender_transfer_gb }} GB</td>
                      <td class="bandwidth">{{ result.sender_bandwidth_gbps }} Gb/s</td>
                      <td>{{ result.receiver_transfer_gb }} GB</td>
                      <td class="bandwidth">{{ result.receiver_bandwidth_gbps }} Gb/s</td>
                      <td>{{ result.retransmissions }}</td>
                    </tr>
                  </template>
                  <template v-if="testResults.roundrobin && testResults.roundrobin.length > 0">
                    <tr v-for="(result, idx) in testResults.roundrobin" :key="'roundrobin-' + idx">
                      <td>{{ result.client }}</td>
                      <td>{{ result.server }}</td>
                      <td>{{ result.sender_transfer_gb }} GB</td>
                      <td class="bandwidth">{{ result.sender_bandwidth_gbps }} Gb/s</td>
                      <td>{{ result.receiver_transfer_gb }} GB</td>
                      <td class="bandwidth">{{ result.receiver_bandwidth_gbps }} Gb/s</td>
                      <td>{{ result.retransmissions }}</td>
                    </tr>
                  </template>
                </tbody>
              </table>
            </div>
          </div>
        </div>
      </div>
    </div>

    <!-- Notification -->
    <div class="notification" :class="notification.type" v-if="notification.show">
      {{ notification.message }}
    </div>
  </div>
</template>

<script setup>
import { ref, reactive, computed, nextTick } from 'vue'
import api from '@/api'
import PageHeader from '@/components/common/PageHeader.vue'
import { usePageStatePersistence } from '@/apps/common/usePageStatePersistence'

const API_BASE = `/network/iperf3`

// Configuration
const config = reactive({
  hostsText: '',
  port: 22,
  username: 'root',
  auth_method: 'password',
  password: '',
  testNetwork: '',
  testMode: 'one2one',
  portMin: 15000,
  cnum: 2,
  duration: 10,
  coreMin: 0,
  useCpuBinding: false
})

// State
const loading = ref(false)
const validating = ref(false)
const validationResults = ref([])
const validatedHosts = ref([])
const selectedHosts = ref([])
const taskId = ref('')
const testLog = ref([])
const testSummary = ref('')
const testResults = ref(null)
const logOutput = ref(null)
const showDetailedLog = ref(false)
let currentWebSocket = null

const notification = reactive({ show: false, message: '', type: 'info' })

// Computed
const parseIPRange = (input) => {
  const ips = []
  const lines = input.split('\n').map(l => l.trim()).filter(l => l)

  for (const line of lines) {
    if (line.includes('/')) {
      const match = line.match(/^(\d+\.\d+\.\d+\.)(\d+)\/(\d+)$/)
      if (match) {
        const prefix = match[1]
        const base = parseInt(match[2])
        const bits = parseInt(match[3])
        const count = Math.pow(2, 32 - bits)
        for (let i = 0; i < count; i++) {
          ips.push(prefix + (base + i))
        }
      }
      continue
    }

    const rangeMatch = line.match(/^(\d+\.\d+\.\d+\.)(\d+)-(\d+)$/)
    if (rangeMatch) {
      const prefix = rangeMatch[1]
      const start = parseInt(rangeMatch[2])
      const end = parseInt(rangeMatch[3])
      for (let i = start; i <= end; i++) {
        ips.push(prefix + i)
      }
      continue
    }

    ips.push(line)
  }

  return ips
}

const hosts = computed(() => {
  return parseIPRange(config.hostsText)
})

const canStartTest = computed(() => {
  return selectedHosts.value.length >= 2 && config.username && (config.auth_method === 'key' || config.password) && config.testNetwork.trim() !== ''
})

const canValidate = computed(() => {
  return hosts.value.length >= 1 && config.username && (config.auth_method === 'key' || config.password)
})

const testModeDescription = computed(() => {
  const descriptions = {
    'one2one': '第一台主机作为服务端，其他主机依次向其发送数据',
    'roundrobin': '每台主机互相发送数据（环形测试）',
    'alltest': '依次执行 One2One 和 RoundRobin 测试'
  }
  return descriptions[config.testMode] || ''
})

const filteredLog = computed(() => {
  if (showDetailedLog.value) {
    return testLog.value
  }

  // 简化模式：过滤掉配置和检查信息
  return testLog.value.filter(line => {
    // 保留的行：不以这些前缀开头的行
    const excludePrefixes = [
      '[测试]',
      '[配置]',
      '[连接]',
      '[检查]',
      '[one2one] 开始测试',
      '[one2one] 在所有主机上启动服务端',
      '[roundrobin] 开始测试',
      '[roundrobin] 在所有主机上启动服务端'
    ]

    // 检查是否应该排除
    for (const prefix of excludePrefixes) {
      if (line.startsWith(prefix)) {
        return false
      }
    }

    // 排除服务端启动命令（包含 -s -p 的命令）
    if (line.includes('[命令]') && line.includes('iperf3 -s')) {
      return false
    }

    return true
  })
})

// Methods
const showNotification = (message, type = 'info') => {
  notification.message = message
  notification.type = type
  notification.show = true
  setTimeout(() => { notification.show = false }, 3000)
}

const addLog = (message) => {
  testLog.value.push(message)
  nextTick(() => {
    if (logOutput.value) {
      logOutput.value.scrollTop = logOutput.value.scrollHeight
    }
  })
}

const validateHosts = async () => {
  if (hosts.value.length < 2) {
    showNotification('至少需要 2 台主机', 'error')
    return
  }

  validating.value = true
  validatedHosts.value = []
  selectedHosts.value = []
  addLog('[验证] 开始验证主机连接...')

  try {
    const response = await api.post(`${API_BASE}/validate`, {
      hosts: hosts.value,
      username: config.username,
      auth_method: config.auth_method,
      password: config.password,
      port: config.port
    })

    if (response.status === 'success') {
      validationResults.value = response.results

      response.results.forEach(result => {
        const icon = result.status === 'success' ? '✅' : '❌'
        validatedHosts.value.push({ ip: result.host, status: result.status })
        addLog(`[验证] ${icon} ${result.host}: ${result.message}`)
      })

      selectAll()

      const successCount = response.results.filter(r => r.status === 'success').length
      if (successCount === hosts.value.length) {
        showNotification('所有主机连接成功', 'success')
      } else {
        showNotification(`${successCount}/${hosts.value.length} 主机连接成功`, 'warning')
      }
    }
  } catch (error) {
    addLog(`[错误] 验证失败: ${error.message}`)
    showNotification('验证失败', 'error')
  } finally {
    validating.value = false
  }
}

const toggleHost = (ip) => {
  const idx = selectedHosts.value.indexOf(ip)
  if (idx > -1) {
    selectedHosts.value.splice(idx, 1)
  } else {
    selectedHosts.value.push(ip)
  }
}

const selectAll = () => {
  selectedHosts.value = validatedHosts.value.filter(h => h.status === 'success').map(h => h.ip)
}

const clearSelection = () => {
  selectedHosts.value = []
}

const startTest = async () => {
  if (!canStartTest.value) {
    showNotification('请完成配置并验证主机连接', 'error')
    return
  }

  loading.value = true

  // 初始化测试结果结构（如果是第一次测试）
  if (!testResults.value) {
    testResults.value = { one2one: [], roundrobin: [] }
  }

  addLog('[测试] 启动带宽测试...')
  addLog(`[配置] 测试模式: ${config.testMode}`)
  addLog(`[配置] 主机数量: ${selectedHosts.value.length}`)
  addLog(`[配置] 测试网段: ${config.testNetwork || '使用 SSH 地址'}`)
  addLog(`[配置] 端口范围: ${config.portMin} - ${config.portMin + config.cnum - 1}`)
  addLog(`[配置] 并发数: ${config.cnum}`)
  addLog(`[配置] 测试时长: ${config.duration}秒`)
  addLog(`[配置] CPU 绑定: ${config.useCpuBinding ? '启用 (核心 ' + config.coreMin + ' 起)' : '禁用'}`)

  // 使用 WebSocket 连接
  const wsUrl = `ws://${window.location.hostname}:6500/api/v1/network/iperf3/ws`
  const ws = new WebSocket(wsUrl)
  currentWebSocket = ws

  ws.onopen = () => {
    addLog('[连接] WebSocket 已连接')

    // 发送测试配置
    ws.send(JSON.stringify({
      action: 'start_test',
      hosts: selectedHosts.value,
      test_network: config.testNetwork,
      test_mode: config.testMode,
      port_min: config.portMin,
      cnum: config.cnum,
      duration: config.duration,
      core_min: config.coreMin,
      use_cpu_binding: config.useCpuBinding,
      username: config.username,
      auth_method: config.auth_method,
      password: config.password,
      port: config.port
    }))
  }

  ws.onmessage = (event) => {
    const data = JSON.parse(event.data)

    if (data.type === 'log') {
      // Split by newlines and add each line individually
      const lines = data.content.split('\n')
      lines.forEach(line => {
        if (line) {  // Only add non-empty lines
          addLog(line)
        }
      })
    } else if (data.type === 'output') {
      const lines = data.content.split('\n')
      lines.forEach(line => {
        if (line) {
          addLog(line)
        }
      })
    } else if (data.type === 'summary') {
      // 添加到测试输出
      testSummary.value = data.content
    } else if (data.type === 'result') {
      // 添加结果到表格
      const result = data.content
      if (result.test_mode === 'one2one') {
        if (!testResults.value.one2one) testResults.value.one2one = []
        testResults.value.one2one.push(result)
      } else if (result.test_mode === 'roundrobin') {
        if (!testResults.value.roundrobin) testResults.value.roundrobin = []
        testResults.value.roundrobin.push(result)
      }
    } else if (data.type === 'completed') {
      addLog('[完成] ' + data.content)
      showNotification('测试完成', 'success')
      loading.value = false
      ws.close()
    } else if (data.type === 'error') {
      addLog('[错误] ' + data.content)
      showNotification('测试失败', 'error')
      loading.value = false
      ws.close()
    }
  }

  ws.onerror = (error) => {
    addLog(`[错误] WebSocket 错误: ${error}`)
    showNotification('连接失败', 'error')
    loading.value = false
    currentWebSocket = null
  }

  ws.onclose = () => {
    addLog('[连接] WebSocket 已断开')
    if (loading.value) {
      loading.value = false
    }
    currentWebSocket = null
  }
}

const stopTest = () => {
  if (currentWebSocket) {
    addLog('[操作] 用户停止测试')
    currentWebSocket.close()
    currentWebSocket = null
  }
  loading.value = false
  showNotification('测试已停止', 'info')
}

const clearLog = () => {
  testLog.value = []
  showNotification('终端输出已清空', 'info')
}

const downloadLog = () => {
  if (testLog.value.length === 0) {
    showNotification('没有可下载的日志', 'warning')
    return
  }

  const content = testLog.value.join('\n')
  const blob = new Blob([content], { type: 'text/plain;charset=utf-8' })
  const url = URL.createObjectURL(blob)
  const link = document.createElement('a')
  link.href = url
  link.download = `network_bandwidth_test_log_${new Date().toISOString().replace(/[:.]/g, '-')}.txt`
  document.body.appendChild(link)
  link.click()
  document.body.removeChild(link)
  URL.revokeObjectURL(url)
  showNotification('日志已下载', 'success')
}

const clearResults = () => {
  testResults.value = { one2one: [], roundrobin: [] }
  showNotification('测试结果已清空', 'info')
}

const downloadResults = () => {
  if (!testResults.value || (testResults.value.one2one.length === 0 && testResults.value.roundrobin.length === 0)) {
    showNotification('没有可下载的测试结果', 'warning')
    return
  }

  // 生成 CSV 内容
  let csv = '客户端,服务端,发送传输(GB),发送带宽(Gb/s),接收传输(GB),接收带宽(Gb/s),重传数\n'

  // 添加 One2One 结果
  if (testResults.value.one2one && testResults.value.one2one.length > 0) {
    testResults.value.one2one.forEach(result => {
      csv += `${result.client},${result.server},${result.sender_transfer_gb},${result.sender_bandwidth_gbps},${result.receiver_transfer_gb},${result.receiver_bandwidth_gbps},${result.retransmissions}\n`
    })
  }

  // 添加 RoundRobin 结果
  if (testResults.value.roundrobin && testResults.value.roundrobin.length > 0) {
    testResults.value.roundrobin.forEach(result => {
      csv += `${result.client},${result.server},${result.sender_transfer_gb},${result.sender_bandwidth_gbps},${result.receiver_transfer_gb},${result.receiver_bandwidth_gbps},${result.retransmissions}\n`
    })
  }

  const blob = new Blob([csv], { type: 'text/csv;charset=utf-8' })
  const url = URL.createObjectURL(blob)
  const link = document.createElement('a')
  link.href = url
  link.download = `network_bandwidth_test_results_${new Date().toISOString().replace(/[:.]/g, '-')}.csv`
  document.body.appendChild(link)
  link.click()
  document.body.removeChild(link)
  URL.revokeObjectURL(url)
  showNotification('测试结果已下载', 'success')
}

usePageStatePersistence('network_bandwidth_test_page_state', () => ({
  config: { ...config },
  validationResults: validationResults.value,
  validatedHosts: validatedHosts.value,
  selectedHosts: selectedHosts.value,
  taskId: taskId.value,
  testLog: testLog.value,
  testSummary: testSummary.value,
  testResults: testResults.value,
  showDetailedLog: showDetailedLog.value
}), {
  hydrate: (saved) => {
    Object.assign(config, {
      hostsText: '',
      port: 22,
      username: 'root',
      auth_method: 'password',
      password: '',
      testNetwork: '',
      testMode: 'one2one',
      portMin: 15000,
      cnum: 2,
      duration: 10,
      coreMin: 0,
      useCpuBinding: false,
      ...(saved.config || {})
    })
    validationResults.value = Array.isArray(saved.validationResults) ? saved.validationResults : []
    validatedHosts.value = Array.isArray(saved.validatedHosts) ? saved.validatedHosts : []
    selectedHosts.value = Array.isArray(saved.selectedHosts) ? saved.selectedHosts : []
    taskId.value = saved.taskId || ''
    testLog.value = Array.isArray(saved.testLog) ? saved.testLog : []
    testSummary.value = saved.testSummary || ''
    testResults.value = saved.testResults || null
    showDetailedLog.value = Boolean(saved.showDetailedLog)
    loading.value = false
    validating.value = false
  }
})
</script>

<style scoped>
.bandwidth-test-page {
  min-height: 100vh;
  padding: 20px;
  background: linear-gradient(135deg, #6B5DD3 0%, #8B7FE8 50%, #9B8FF8 100%);
  position: relative;
}

.bandwidth-test-page::before {
  content: '';
  position: fixed;
  top: 0;
  left: 0;
  right: 0;
  bottom: 0;
  background: repeating-linear-gradient(45deg, transparent, transparent 35px, rgba(255,255,255,0.05) 35px, rgba(255,255,255,0.05) 70px);
  pointer-events: none;
  z-index: 0;
}

.bandwidth-test-page > * {
  position: relative;
  z-index: 1;
}

.main-content {
  display: grid;
  grid-template-columns: 320px 1fr;
  gap: 20px;
  margin-top: 20px;
}

.left-panel, .right-panel {
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
}

.form-group {
  margin-bottom: 12px;
}

.form-group label {
  display: block;
  font-size: 12px;
  font-weight: 500;
  color: #666;
  margin-bottom: 4px;
}

.form-group input,
.form-group select,
.form-group textarea {
  width: 100%;
  padding: 8px 12px;
  border: 1px solid #ddd;
  border-radius: 6px;
  font-size: 14px;
  font-family: monospace;
}

.form-group textarea {
  resize: vertical;
  min-height: 60px;
}

.form-group input:focus,
.form-group select:focus,
.form-group textarea:focus {
  outline: none;
  border-color: #6B5DD3;
  box-shadow: 0 0 0 3px rgba(107, 93, 211, 0.1);
}

.form-row {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 10px;
  margin-bottom: 12px;
}

.form-row .form-group {
  margin-bottom: 0;
}

.form-row-three {
  display: grid;
  grid-template-columns: 1fr 1fr 1fr;
  gap: 10px;
  margin-bottom: 12px;
}

.form-row-three .form-group {
  margin-bottom: 0;
}

.hint {
  display: block;
  font-size: 11px;
  color: #999;
  margin-top: 4px;
}

.checkbox-group {
  margin-bottom: 12px;
}

.checkbox-label {
  display: flex;
  align-items: center;
  gap: 8px;
  cursor: pointer;
  font-weight: 500;
  font-size: 13px;
  color: #666;
  padding-left: 0;
}

.checkbox-label input[type="checkbox"] {
  width: 16px;
  height: 16px;
  cursor: pointer;
}

.checkbox-label span {
  user-select: none;
}

.host-list-section { margin-top: 12px; padding: 10px; background: #f8f9fa; border-radius: 8px; border: 1px solid #e0e0e0; }
.host-list-title { font-size: 13px; font-weight: 600; color: #333; margin-bottom: 6px; }
.host-list { max-height: 140px; overflow-y: auto; border: 1px solid #eee; border-radius: 6px; background: white; margin-bottom: 6px; }
.host-item { display: flex; justify-content: space-between; align-items: center; padding: 5px 10px; cursor: pointer; border-bottom: 1px solid #f0f0f0; font-size: 13px; }
.host-item:last-child { border-bottom: none; }
.host-item:hover { background: #f0f0f0; }
.host-item.selected { background: #e8f5e9; }
.host-ip { font-family: monospace; font-size: 13px; }
.host-status { font-weight: 600; font-size: 14px; }
.host-status.success { color: #4CAF50; }
.host-status.error { color: #f44336; }
.host-list-actions { display: flex; gap: 8px; }

.validation-summary {
  margin-top: 10px;
  text-align: center;
}

.badge {
  padding: 4px 12px;
  border-radius: 20px;
  font-size: 13px;
}

.badge.success {
  background: #e8f5e9;
  color: #4CAF50;
}

.badge.warning {
  background: #fff3e0;
  color: #ff9800;
}

.badge.error {
  background: #ffebee;
  color: #f44336;
}

.terminal-section {
  height: 395px;
  display: flex;
  flex-direction: column;
}

.terminal-header {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  margin: 0;
  margin-bottom: 15px;
  flex-shrink: 0;
}

.terminal-header .section-title {
  margin: 0;
  padding-bottom: 10px;
  border-bottom: none;
  flex: 1;
}

.terminal-controls {
  display: flex;
  align-items: center;
  gap: 10px;
}

.detailed-log-toggle {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 12px;
  color: #666;
  cursor: pointer;
  user-select: none;
}

.detailed-log-toggle input[type="checkbox"] {
  width: 16px;
  height: 16px;
  cursor: pointer;
}

.detailed-log-toggle:hover {
  color: #6B5DD3;
}

.terminal-output {
  flex: 1;
  background: #1e1e1e;
  border-radius: 8px;
  padding: 15px;
  overflow-y: auto;
  font-family: 'Courier New', monospace;
  font-size: 13px;
  line-height: 1.6;
}

.empty-log {
  color: #666;
  text-align: center;
  padding: 40px;
}

.log-line {
  color: #d4d4d4;
  white-space: pre;
  font-family: 'Courier New', monospace;
}

.results-section-fixed {
  height: 400px;
  display: flex;
  flex-direction: column;
}

.results-output {
  flex: 1;
  background: #f8f9fa;
  border-radius: 8px;
  padding: 10px;
  overflow-y: auto;
}

.empty-results {
  color: #666;
  text-align: center;
  padding: 40px;
}

.results-table-container {
  overflow-x: auto;
}

.results-table {
  width: 100%;
  border-collapse: collapse;
  font-size: 13px;
  background: white;
  border-radius: 6px;
  overflow: hidden;
}

.results-table thead {
  background: #f8f8f8;
  position: sticky;
  top: 0;
  z-index: 1;
}

.results-table th {
  padding: 10px 12px;
  text-align: left;
  font-weight: 600;
  color: #555;
}

.results-table td {
  padding: 10px 12px;
}

.results-table tbody tr:last-child td {
  border-bottom: none;
}

.results-table tbody tr:hover {
  background: #f8f9fa;
}

.test-type-cell {
  background: #f0f0f0;
  font-weight: 600;
  color: #6B5DD3;
  text-align: left;
  vertical-align: middle;
  padding: 10px 12px;
}

.bandwidth {
  color: #6B5DD3;
  font-weight: 600;
  font-family: monospace;
}

.bandwidth {
  color: #6B5DD3;
  font-weight: 600;
  font-family: monospace;
}

.notification {
  position: fixed;
  top: 80px;
  right: 20px;
  padding: 12px 20px;
  border-radius: 8px;
  background: white;
  box-shadow: 0 4px 12px rgba(0, 0, 0, 0.15);
  z-index: 1000;
}

.notification.success {
  background: #4caf50;
  color: white;
}

.notification.error {
  background: #f44336;
  color: white;
}

.notification.warning {
  background: #ff9800;
  color: white;
}

.btn-compact {
  padding: 6px 12px !important;
  font-size: 12px !important;
  width: auto !important;
}
</style>

<style>
/* 非 scoped 样式，确保 placeholder 颜色正确 */
.bandwidth-test-page input::placeholder,
.bandwidth-test-page textarea::placeholder,
.bandwidth-test-page select::placeholder {
  color: #999 !important;
  opacity: 1;
}
</style>
