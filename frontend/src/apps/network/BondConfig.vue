<template>
  <div class="bond-page">
    <PageHeader
      icon="🔗"
      title="网络聚合配置"
    />

    <!-- Main Content -->
    <div class="main-content">
      <!-- Left Panel: Config -->
      <div class="left-panel">
        <!-- Host Config -->
        <div class="section">
          <h2 class="section-title">主机配置</h2>

          <div class="form-group">
            <label>主机列表 (每行一个IP或范围):</label>
            <textarea
              v-model="hostsText"
              rows="4"
              placeholder="192.168.1.1
192.168.1.2
192.168.1.10-20"
            ></textarea>
          </div>

          <div class="form-row">
            <div class="form-group">
              <label>用户名:</label>
              <input type="text" v-model="username" placeholder="root">
            </div>
            <div class="form-group">
              <label>端口:</label>
              <input type="number" v-model="port" placeholder="22">
            </div>
          </div>

          <div class="form-group">
            <label>认证方式:</label>
            <select v-model="authMethod">
              <option value="password">密码</option>
              <option value="key">服务器 SSH 密钥</option>
            </select>
          </div>

          <div v-if="authMethod === 'password'" class="form-group">
            <label>密码:</label>
            <input type="password" v-model="password" placeholder="******" @keyup.enter="loadConfig">
          </div>

          <button class="btn btn-primary btn-full" @click="loadConfig" :disabled="loading || !isFormValid">
            {{ loading ? '连接中...' : '验证连接' }}
          </button>

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

        <!-- Bond Config Control -->
        <div v-if="hasConnectedHosts" class="section">
          <h2 class="section-title">Bond配置</h2>

          <div class="btn-group">
            <button class="btn btn-primary btn-full" @click="clearAllBonds">
              清除Bond
            </button>
            <button class="btn btn-primary btn-full" @click="addBondGroup">
              增加Bond
            </button>
            <button class="btn btn-primary btn-full" @click="applyConfiguration">
              应用配置
            </button>
          </div>

          <!-- Bond Mode Info -->
          <div class="info-box" style="margin-top: 15px;">
            <h4>Bond模式说明</h4>
            <ul>
              <li><strong>Mode 0 (balance-rr)</strong>: 轮询模式，提供负载均衡和容错能力</li>
              <li><strong>Mode 1 (active-backup)</strong>: 主备模式，只有一个网卡工作，提供冗余</li>
              <li><strong>Mode 4 (802.3ad/LACP)</strong>: 动态链路聚合，需要交换机支持LACP</li>
              <li><strong>Mode 6 (balance-alb)</strong>: 自适应负载均衡，不需要交换机特殊配置</li>
            </ul>
          </div>
        </div>
      </div>

      <!-- Right Panel: Results -->
      <div class="right-panel">
        <!-- Network Status -->
        <div v-if="hasConnectedHosts" class="section">
          <div class="section-header">
            <h2 class="section-title">当前网络状态</h2>
            <div class="header-buttons">
              <button class="btn btn-small" @click="refreshNetworkStatus">
                刷新
              </button>
              <button v-if="connectedHostCount > 1" class="btn btn-small" @click="toggleAllServers">
                {{ allExpanded ? '折叠全部' : '展开全部' }}
              </button>
            </div>
          </div>

          <div v-for="(server, index) in serversData" :key="index" class="server-section" :class="{ collapsed: server.collapsed }">
            <div class="server-header" @click="toggleServer(index)">
              <div class="server-header-left">
                <span class="server-header-icon">▼</span>
                <span>🖥️ {{ server.host }} ({{ server.interfaces?.length || 0 }} 个网卡)</span>
              </div>
              <span class="status-badge" :class="server.status">
                {{ server.status === 'success' ? '✓ 已连接' : '✗ 失败' }}
              </span>
            </div>
            <div class="server-content">
              <table v-if="server.status === 'success'" class="network-table">
                <thead>
                  <tr>
                    <th>网卡名</th>
                    <th>类型</th>
                    <th>物理口</th>
                    <th>速率</th>
                    <th>IP地址</th>
                    <th>MAC地址</th>
                    <th>状态</th>
                  </tr>
                </thead>
                <tbody>
                  <tr v-for="iface in server.interfaces" :key="iface.name">
                    <td>{{ iface.name }}</td>
                    <td><span class="type-badge" :class="iface.type">{{ iface.type }}</span></td>
                    <td>{{ iface.type === 'physical' ? '是' : (iface.type === 'bond' && iface.slaves ? iface.slaves.join(' ') : '-') }}</td>
                    <td>{{ iface.speed || '-' }}</td>
                    <td>{{ iface.ip || '-' }}</td>
                    <td>{{ iface.mac || '-' }}</td>
                    <td>{{ iface.status || '-' }}</td>
                  </tr>
                </tbody>
              </table>
              <div v-else class="error-message">
                错误: {{ server.error }}
              </div>
            </div>
          </div>
        </div>

        <!-- Bond Config -->
        <div v-if="hasConnectedHosts" class="section">
          <h2 class="section-title">Bond配置</h2>

          <!-- Bond Groups -->
          <div v-for="(bond, index) in bondConfigs" :key="bond.id" class="bond-group" :class="{ collapsed: bond.collapsed }">
            <div class="bond-group-header" @click="toggleBondGroup(index)">
              <div class="bond-group-left">
                <span class="bond-group-icon">▼</span>
                <div class="bond-group-title">Bond配置 #{{ index + 1 }}</div>
              </div>
              <button class="btn btn-remove-small" @click.stop="removeBondGroup(index)">删除</button>
            </div>

            <div class="bond-group-content">

            <!-- 第一行：Bond接口名称 + Bond模式 + 网卡接口 -->
            <div class="form-row-three">
              <div class="form-group">
                <label>Bond接口名称:</label>
                <input type="text" v-model="bond.name" placeholder="bond0">
              </div>

              <div class="form-group">
                <label>Bond模式:</label>
                <select v-model="bond.mode">
                  <option value="1">Mode 1 - 主备模式</option>
                  <option value="0">Mode 0 - 轮询模式</option>
                  <option value="4">Mode 4 - LACP聚合</option>
                  <option value="6">Mode 6 - 自适应负载均衡</option>
                </select>
              </div>

              <div class="form-group">
                <label>网卡接口 (可多选):</label>
                <div class="dropdown-checkbox" v-click-outside="() => bond.showInterfaces = false">
                  <div class="dropdown-header" @click="bond.showInterfaces = !bond.showInterfaces">
                    <span>{{ bond.slaves.length > 0 ? bond.slaves.join(', ') : '请选择网卡' }}</span>
                    <span class="dropdown-arrow" :class="{ expanded: bond.showInterfaces }">▼</span>
                  </div>
                  <div v-if="bond.showInterfaces" class="checkbox-list">
                    <label v-for="iface in getAvailableInterfacesForBond(index)" :key="iface" class="checkbox-item" :class="{ selected: bond.slaves.includes(iface) }">
                      <input type="checkbox" :value="iface" v-model="bond.slaves" class="hidden-checkbox">
                      <span>{{ iface }}</span>
                      <span class="check-icon">✓</span>
                    </label>
                  </div>
                </div>
              </div>
            </div>

            <!-- 第二行：IP地址 + 网关 + DNS服务器 -->
            <div class="form-row-three">
              <div class="form-group">
                <label>IP地址 (CIDR格式):</label>
                <input
                  type="text"
                  v-model="bond.ip"
                  :class="{ 'input-error': bond.ip && !validateIPFormat(bond.ip) }"
                  :placeholder="connectedHostCount > 1 ? `范围: 192.168.2.1-192.168.2.${connectedHostCount}/24 (${connectedHostCount}个IP)` : '192.168.1.100/24'"
                >
                <small v-if="bond.ip && !validateIPFormat(bond.ip)" class="error-hint">
                  {{ getIPErrorMessage(bond.ip) }}
                </small>
              </div>

              <div class="form-group">
                <label>网关:</label>
                <input
                  type="text"
                  v-model="bond.gateway"
                  placeholder="可不配置"
                >
              </div>

              <div class="form-group">
                <label>DNS服务器:</label>
                <input type="text" v-model="bond.dns" placeholder="可不配置">
              </div>
            </div>
            </div>
          </div>
        </div>

        <!-- Logs -->
        <div v-if="logs.length > 0" class="section">
          <div class="section-header">
            <h2 class="section-title">配置日志</h2>
            <button class="btn btn-small" @click="clearLogs">清空日志</button>
          </div>
          <div class="log-output">
            <div v-for="(log, index) in logs" :key="index" :class="['log-entry', log.type]">
              [{{ log.time }}] {{ log.message }}
            </div>
          </div>
        </div>

        <!-- Empty State -->
        <div v-if="!hasConnectedHosts" class="section empty-section">
          <div class="empty-state">
            <span class="empty-icon">🔗</span>
            <p>输入 SSH 连接信息后点击"连接"</p>
          </div>
        </div>
      </div>
    </div>

    <!-- Confirmation Dialog -->
    <div v-if="confirmDialog.show" class="confirmation-overlay" @click="cancelConfirmation">
      <div class="confirmation-dialog" :class="confirmDialog.type" @click.stop>
        <h3>{{ confirmDialog.title }}</h3>
        <div class="confirmation-content">{{ confirmDialog.message }}</div>
        <div class="confirmation-actions">
          <button class="btn btn-confirm" @click="proceedConfirmation">✓ 确定继续</button>
          <button class="btn btn-cancel" @click="cancelConfirmation">✗ 取消</button>
        </div>
      </div>
    </div>

    <div class="notification" :class="notification.type" v-if="notification.show">
      {{ notification.message }}
    </div>
  </div>
</template>

<script setup>
import { ref, reactive, computed, onMounted, onUnmounted } from 'vue'
import api from '@/api'
import PageHeader from '@/components/common/PageHeader.vue'

// Click outside directive
const vClickOutside = {
  mounted(el, binding) {
    el.clickOutsideEvent = (event) => {
      if (!(el === event.target || el.contains(event.target))) {
        binding.value()
      }
    }
    document.addEventListener('click', el.clickOutsideEvent)
  },
  unmounted(el) {
    document.removeEventListener('click', el.clickOutsideEvent)
  }
}

const API_BASE = `/network/bond`

const hostsText = ref('')
const port = ref(22)
const username = ref('root')
const password = ref('')
const authMethod = ref('password')
const loading = ref(false)
const refreshing = ref(false)
const applying = ref(false)
const clearing = ref(false)
const connectedServers = ref([])
const validatedHosts = ref([])
const selectedHosts = ref([])
const serversData = ref([])
const availableInterfaces = ref([])
const bondConfigs = ref([])
const logs = ref([])
const allExpanded = ref(false)
const bondGroupCounter = ref(0)

const notification = reactive({ show: false, message: '', type: 'info' })
const confirmDialog = reactive({
  show: false,
  title: '',
  message: '',
  type: 'warning',
  action: null
})

const isFormValid = computed(() => hostsText.value.trim() && username.value.trim() && (authMethod.value === 'key' || password.value))
const successfulHosts = computed(() => validatedHosts.value.filter(h => h.status === 'success').map(h => h.ip))
const hasConnectedHosts = computed(() => successfulHosts.value.length > 0)
const connectedHostCount = computed(() => connectedServers.value.length || successfulHosts.value.length)

// 验证IP地址格式
const validateIPFormat = (ip) => {
  if (!ip || !ip.trim()) return true // 空值不验证

  const serverCount = connectedHostCount.value

  if (serverCount === 1) {
    // 单台主机：必须是单个IP格式 192.168.1.24/24
    const singleIPPattern = /^\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}\/\d{1,2}$/
    return singleIPPattern.test(ip)
  } else if (serverCount > 1) {
    // 多台主机：必须是范围格式 192.168.2.1-192.168.2.20/24
    const rangeIPPattern = /^(\d{1,3})\.(\d{1,3})\.(\d{1,3})\.(\d{1,3})-(\d{1,3})\.(\d{1,3})\.(\d{1,3})\.(\d{1,3})\/(\d{1,2})$/
    const match = ip.match(rangeIPPattern)

    if (!match) return false

    // 提取起始和结束IP的各个部分
    const startIP = [parseInt(match[1]), parseInt(match[2]), parseInt(match[3]), parseInt(match[4])]
    const endIP = [parseInt(match[5]), parseInt(match[6]), parseInt(match[7]), parseInt(match[8])]

    // 检查IP是否在同一网段（前3段必须相同）
    if (startIP[0] !== endIP[0] || startIP[1] !== endIP[1] || startIP[2] !== endIP[2]) {
      return false
    }

    // 计算IP范围数量
    const ipCount = endIP[3] - startIP[3] + 1

    // IP数量必须等于主机数量
    if (ipCount !== serverCount) {
      return false
    }

    // 检查IP是否连续（已经通过计算验证）
    return true
  }

  return true
}

// 判断是否需要网关（范围格式必须有网关）
const needsGateway = (ip) => {
  if (!ip || !ip.trim()) return false
  return ip.includes('-') // 范围格式需要网关
}

// 获取IP地址错误提示信息
const getIPErrorMessage = (ip) => {
  const serverCount = connectedHostCount.value

  if (serverCount === 1) {
    return '单台主机格式：192.168.1.24/24'
  }

  const rangeIPPattern = /^(\d{1,3})\.(\d{1,3})\.(\d{1,3})\.(\d{1,3})-(\d{1,3})\.(\d{1,3})\.(\d{1,3})\.(\d{1,3})\/(\d{1,2})$/
  const match = ip.match(rangeIPPattern)

  if (!match) {
    return `多台主机格式：192.168.2.1-192.168.2.${serverCount}/24`
  }

  const startIP = [parseInt(match[1]), parseInt(match[2]), parseInt(match[3]), parseInt(match[4])]
  const endIP = [parseInt(match[5]), parseInt(match[6]), parseInt(match[7]), parseInt(match[8])]

  if (startIP[0] !== endIP[0] || startIP[1] !== endIP[1] || startIP[2] !== endIP[2]) {
    return '起始和结束IP必须在同一网段'
  }

  const ipCount = endIP[3] - startIP[3] + 1
  if (ipCount !== serverCount) {
    return `IP数量(${ipCount})必须等于主机数量(${serverCount})`
  }

  return ''
}

// 获取指定Bond配置可用的网卡列表（排除已被其他Bond占用的网卡）
const getAvailableInterfacesForBond = (currentIndex) => {
  const usedInterfaces = new Set()

  // 收集其他Bond配置已使用的网卡
  bondConfigs.value.forEach((bond, index) => {
    if (index !== currentIndex) {
      bond.slaves.forEach(slave => usedInterfaces.add(slave))
    }
  })

  // 返回未被占用的网卡
  return availableInterfaces.value.filter(iface => !usedInterfaces.has(iface))
}

const showNotification = (message, type = 'info') => {
  notification.message = message
  notification.type = type
  notification.show = true
  setTimeout(() => { notification.show = false }, 3000)
}

const addLog = (message, type = 'info') => {
  const time = new Date().toLocaleTimeString()
  logs.value.push({ time, message, type })
}

const clearLogs = () => {
  logs.value = []
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
  selectedHosts.value = successfulHosts.value
}

const clearSelection = () => {
  selectedHosts.value = []
}

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

const loadConfig = async () => {
  if (!isFormValid.value) return showNotification('请填写完整连接信息', 'error')

  loading.value = true
  clearLogs()
  validatedHosts.value = []
  connectedServers.value = []
  selectedHosts.value = []
  bondConfigs.value = []

  const ips = parseIPRange(hostsText.value)
  addLog(`开始验证 ${ips.length} 台服务器...`, 'info')

  serversData.value = []

  for (const host of ips) {
    try {
      const response = await api.post(`${API_BASE}/get-nics`, {
        host, port: port.value, username: username.value, auth_method: authMethod.value, password: password.value
      })

      if (response.status === 'success') {
        validatedHosts.value.push({ ip: host, status: 'success' })
        serversData.value.push({
          host,
          interfaces: response.interfaces || [],
          nics: response.nics || [],
          status: 'success',
          collapsed: true
        })
        addLog(`${host}: 连接成功`, 'success')
      } else {
        validatedHosts.value.push({ ip: host, status: 'error' })
        serversData.value.push({
          host,
          status: 'failed',
          error: response.error || '连接失败',
          collapsed: true
        })
        addLog(`${host}: 连接失败 - ${response.error}`, 'error')
      }
    } catch (error) {
      const errorMessage = error.response?.data?.error || error.message
      validatedHosts.value.push({ ip: host, status: 'error' })
      serversData.value.push({
        host,
        status: 'failed',
        error: errorMessage,
        collapsed: true
      })
      addLog(`${host}: 连接失败 - ${errorMessage}`, 'error')
    }
  }

  const successCount = validatedHosts.value.filter(h => h.status === 'success').length
  const failCount = validatedHosts.value.length - successCount

  addLog(`验证完成: ${successCount} 台成功` + (failCount > 0 ? `, ${failCount} 台失败` : ''), successCount > 0 ? 'success' : 'error')

  const firstSuccess = serversData.value.find(s => s.status === 'success')
  if (firstSuccess) {
    availableInterfaces.value = firstSuccess.nics || []
    connectedServers.value = successfulHosts.value
    selectAll()
    addBondGroup()
  } else {
    showNotification('所有服务器连接失败', 'error')
  }

  loading.value = false
}

const addBondGroup = () => {
  bondGroupCounter.value++
  bondConfigs.value.push({
    id: bondGroupCounter.value,
    name: `bond${bondConfigs.value.length}`,
    mode: '1',
    slaves: [],
    ip: '',
    gateway: '',
    dns: '',
    showInterfaces: false,
    collapsed: false
  })
}

const removeBondGroup = (index) => {
  bondConfigs.value.splice(index, 1)
}

const toggleBondGroup = (index) => {
  bondConfigs.value[index].collapsed = !bondConfigs.value[index].collapsed
}

const toggleServer = (index) => {
  serversData.value[index].collapsed = !serversData.value[index].collapsed
}

const toggleAllServers = () => {
  allExpanded.value = !allExpanded.value
  serversData.value.forEach(server => {
    server.collapsed = !allExpanded.value
  })
}

const refreshNetworkStatus = async () => {
  const targets = selectedHosts.value.length > 0 ? selectedHosts.value : successfulHosts.value
  if (targets.length === 0) {
    return showNotification('请先选择主机', 'error')
  }

  refreshing.value = true
  addLog('刷新网络状态...', 'info')

  try {
    const promises = targets.map(async (host) => {
      try {
        const response = await api.post(`${API_BASE}/get-nics`, {
          host, port: port.value, username: username.value, auth_method: authMethod.value, password: password.value
        })

        if (response.status === 'success') {
          return {
            host,
            interfaces: response.interfaces || [],
            nics: response.nics || [],
            status: 'success',
            collapsed: true
          }
        } else {
          return {
            host,
            status: 'failed',
            error: response.error || '连接失败',
            collapsed: true
          }
        }
      } catch (error) {
        return {
          host,
          status: 'failed',
          error: error.message,
          collapsed: true
        }
      }
    })

    serversData.value = await Promise.all(promises)

    const successCount = serversData.value.filter(s => s.status === 'success').length
    addLog(`刷新完成: ${successCount} 台成功`, successCount > 0 ? 'success' : 'error')
    showNotification('网络状态已刷新', 'success')

    // Update available interfaces
    const firstSuccess = serversData.value.find(s => s.status === 'success')
    if (firstSuccess) {
      availableInterfaces.value = firstSuccess.nics || []
    }
  } catch (e) {
    addLog(`刷新失败: ${e.message}`, 'error')
    showNotification('刷新失败', 'error')
  } finally {
    refreshing.value = false
  }
}

const applyConfiguration = () => {
  if (bondConfigs.value.length === 0) {
    return showNotification('请至少添加一个Bond配置', 'error')
  }

  for (const bond of bondConfigs.value) {
    if (bond.slaves.length === 0) {
      return showNotification('每个Bond至少需要选择1个网卡', 'error')
    }
  }

  const targets = selectedHosts.value.length > 0 ? selectedHosts.value : successfulHosts.value
  confirmDialog.title = '⚠️ 确认配置'
  confirmDialog.message = `即将开始配置Bond网络聚合：\n\n服务器数量: ${targets.length} 台\nBond配置: ${bondConfigs.value.length} 个\n\n点击"确定继续"开始配置，点击"取消"放弃配置。\n\n⚠️ 注意：配置过程中请勿关闭SSH连接！`
  confirmDialog.type = 'warning'
  confirmDialog.action = 'apply'
  confirmDialog.show = true
}

const clearAllBonds = () => {
  const targets = selectedHosts.value.length > 0 ? selectedHosts.value : successfulHosts.value
  confirmDialog.title = '⚠️ 确认清除Bond配置'
  confirmDialog.message = `即将清除 ${targets.length} 台服务器上的Bond配置：\n\n⚠️ 警告：\n- 此操作将删除所有Bond接口\n- 此操作将删除所有Bond配置文件\n- 此操作可能导致网络连接中断\n\n点击"确定继续"开始清除，点击"取消"放弃操作。`
  confirmDialog.type = 'warning'
  confirmDialog.action = 'clear'
  confirmDialog.show = true
}

const resetAll = () => {
  confirmDialog.title = '⚠️ 确认重置配置'
  confirmDialog.message = `即将重置所有配置：\n\n- 清空所有Bond配置\n- 断开SSH连接\n- 清空网络状态显示\n\n点击"确定继续"开始重置，点击"取消"放弃操作。`
  confirmDialog.type = 'warning'
  confirmDialog.action = 'reset'
  confirmDialog.show = true
}

const proceedConfirmation = async () => {
  const action = confirmDialog.action
  confirmDialog.show = false

  if (action === 'apply') {
    await executeApplyConfiguration()
  } else if (action === 'clear') {
    await executeClearBonds()
  } else if (action === 'reset') {
    executeReset()
  }
}

const cancelConfirmation = () => {
  confirmDialog.show = false
}

const executeApplyConfiguration = async () => {
  const targets = selectedHosts.value.length > 0 ? selectedHosts.value : successfulHosts.value
  if (targets.length === 0) {
    showNotification('请先选择主机', 'error')
    return
  }

  applying.value = true
  clearLogs()

  addLog(`开始配置 ${targets.length} 台服务器...`, 'info')
  addLog(`Bond配置数量: ${bondConfigs.value.length}`, 'info')

  try {
    let totalSuccess = 0
    let totalError = 0

    for (let serverIndex = 0; serverIndex < targets.length; serverIndex++) {
      const server = targets[serverIndex]
      addLog(`\n========== 配置服务器 #${serverIndex + 1}: ${server} ==========`, 'info')

      try {
        const response = await api.post(`${API_BASE}/apply-bond`, {
          servers: [server],
          server_index: serverIndex,
          username: username.value,
          auth_method: authMethod.value,
          password: password.value,
          bond_configs: bondConfigs.value
        })

        if (response.status === 'completed' && response.results.length > 0) {
          const serverResult = response.results[0]

          if (serverResult.status === 'error') {
            addLog(`✗ 连接失败: ${serverResult.error}`, 'error')
            totalError++
          } else {
            let serverSuccess = 0
            let serverFailed = 0

            serverResult.bonds.forEach(bond => {
              if (bond.success) {
                addLog(`✓ ${bond.message}`, 'success')
                serverSuccess++
              } else {
                addLog(`✗ ${bond.name}: ${bond.message}`, 'error')
                serverFailed++
              }
            })

            if (serverFailed === 0) {
              totalSuccess++
            } else {
              totalError++
            }
          }
        } else {
          throw new Error(response.error || '配置失败')
        }
      } catch (error) {
        addLog(`✗ 服务器 ${server} 配置失败: ${error.message}`, 'error')
        totalError++
      }
    }

    addLog('\n========== 汇总 ==========', 'info')
    addLog(`总计: ${targets.length} 台服务器`, 'info')
    addLog(`成功: ${totalSuccess} 台`, 'success')
    if (totalError > 0) {
      addLog(`失败: ${totalError} 台`, 'error')
    }

    if (totalSuccess > 0) {
      showNotification('配置完成', 'success')
      await refreshNetworkStatus()
    }
  } catch (error) {
    addLog(`错误: ${error.message}`, 'error')
    showNotification('配置失败', 'error')
  } finally {
    applying.value = false
  }
}

const executeClearBonds = async () => {
  const targets = selectedHosts.value.length > 0 ? selectedHosts.value : successfulHosts.value
  if (targets.length === 0) {
    showNotification('请先选择主机', 'error')
    return
  }

  clearing.value = true
  clearLogs()

  addLog(`开始清除 ${targets.length} 台服务器的Bond配置...`, 'info')

  try {
    let totalSuccess = 0
    let totalError = 0

    for (const server of targets) {
      addLog(`\n========== 清除服务器: ${server} ==========`, 'info')

      try {
        const response = await api.post(`${API_BASE}/clear-bonds`, {
          servers: [server],
          username: username.value,
          auth_method: authMethod.value,
          password: password.value
        })

        if (response.status === 'completed' && response.results.length > 0) {
          const serverResult = response.results[0]

          if (serverResult.status === 'error') {
            addLog(`✗ 连接失败: ${serverResult.error}`, 'error')
            totalError++
          } else if (serverResult.status === 'failed') {
            addLog(`✗ ${serverResult.message}`, 'error')
            totalError++
          } else {
            addLog(`✓ ${serverResult.message}`, 'success')
            totalSuccess++
          }
        } else {
          throw new Error(response.error || '清除失败')
        }
      } catch (error) {
        addLog(`✗ 服务器 ${server} 清除失败: ${error.message}`, 'error')
        totalError++
      }
    }

    addLog('\n========== 汇总 ==========', 'info')
    addLog(`总计: ${targets.length} 台服务器`, 'info')
    addLog(`成功: ${totalSuccess} 台`, 'success')
    if (totalError > 0) {
      addLog(`失败: ${totalError} 台`, 'error')
    }

    if (totalSuccess > 0) {
      showNotification('清除完成', 'success')
      await refreshNetworkStatus()
    }
  } catch (error) {
    addLog(`错误: ${error.message}`, 'error')
    showNotification('清除失败', 'error')
  } finally {
    clearing.value = false
  }
}

const executeReset = () => {
  bondConfigs.value = []
  connectedServers.value = []
  validatedHosts.value = []
  selectedHosts.value = []
  serversData.value = []
  availableInterfaces.value = []
  clearLogs()
  bondGroupCounter.value = 0
  showNotification('已重置所有配置', 'info')
}
</script>

<style scoped>
.bond-page { min-height: 100vh; padding: 20px; background: linear-gradient(135deg, #6B5DD3 0%, #8B7FE8 50%, #9B8FF8 100%); position: relative; }
.bond-page::before { content: ''; position: fixed; top: 0; left: 0; right: 0; bottom: 0; background: repeating-linear-gradient(45deg, transparent, transparent 35px, rgba(255,255,255,0.05) 35px, rgba(255,255,255,0.05) 70px); pointer-events: none; z-index: 0; }
.bond-page > * { position: relative; z-index: 1; }
.main-content { display: grid; grid-template-columns: 320px 1fr; gap: 20px; }
.left-panel, .right-panel { display: flex; flex-direction: column; gap: 15px; }
.section { background: rgba(255,255,255,0.95); border-radius: 12px; padding: 20px; box-shadow: 0 4px 6px rgba(0, 0, 0, 0.1); }
.section-title {
  font-size: 18px;
  font-weight: 600;
  color: #333;
  margin-bottom: 15px;
  padding-bottom: 10px;
  border-bottom: 2px solid #6B5DD3;
}
.section-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 15px;
}
.header-buttons {
  display: flex;
  gap: 10px;
}
.info-box {
  background: #e7f3ff;
  border-left: 4px solid #2196F3;
}
.info-box h4 {
  color: #1976D2;
  margin-bottom: 10px;
  font-size: 16px;
}
.info-box ul {
  margin-left: 20px;
  color: #555;
  font-size: 13px;
}
.info-box li {
  margin: 6px 0;
  line-height: 1.6;
}
.form-group { margin-bottom: 12px; }
.form-group label { display: block; font-size: 12px; color: #666; margin-bottom: 4px; }
.form-group input, .form-group select, .form-group textarea { width: 100%; padding: 8px 12px; border: 1px solid #ddd; border-radius: 6px; font-size: 14px; font-family: monospace; }
.form-group input.input-error { border-color: #dc3545; background-color: #fff5f5; }
.form-group textarea { min-height: 60px; resize: vertical; }
.form-group small { font-size: 11px; color: #999; margin-top: 4px; display: block; }
.form-group small.error-hint { color: #dc3545; font-weight: 500; }
.form-row { display: grid; grid-template-columns: 1fr 1fr; gap: 10px; }
.form-row-three { display: grid; grid-template-columns: 1fr 1fr 1fr; gap: 10px; margin-bottom: 12px; }
.dropdown-checkbox { position: relative; }
.dropdown-header {
  width: 100%;
  padding: 8px 12px;
  border: 1px solid #ddd;
  border-radius: 6px;
  font-size: 14px;
  font-family: monospace;
  background: white;
  cursor: pointer;
  display: flex;
  justify-content: space-between;
  align-items: center;
  user-select: none;
  height: 38px;
  box-sizing: border-box;
}
.dropdown-header:hover {
  border-color: #6B5DD3;
}
.dropdown-arrow {
  font-size: 10px;
  transition: transform 0.2s;
}
.dropdown-arrow.expanded {
  transform: rotate(180deg);
}
.checkbox-list {
  position: absolute;
  top: 100%;
  left: 0;
  right: 0;
  display: flex;
  flex-direction: column;
  gap: 4px;
  max-height: 400px;
  overflow-y: auto;
  padding: 8px;
  background: white;
  border-radius: 6px;
  margin-top: 4px;
  border: 1px solid #ddd;
  box-shadow: 0 4px 12px rgba(0, 0, 0, 0.15);
  z-index: 9999;
}
.checkbox-item {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  font-size: 13px;
  cursor: pointer;
  padding: 6px 8px;
  border-radius: 4px;
  transition: background 0.2s;
}
.checkbox-item:hover {
  background: #f0f0f0;
}
.checkbox-item.selected {
  background: #e7f3ff;
}
.hidden-checkbox {
  display: none;
}
.check-icon {
  width: 16px;
  height: 16px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  font-size: 12px;
  color: transparent;
  font-weight: bold;
  flex-shrink: 0;
}
.checkbox-item.selected .check-icon {
  color: #6B5DD3;
}
.bond-group {
  background: white;
  padding: 15px;
  border-radius: 8px;
  margin-bottom: 15px;
  border: 2px solid #e0e0e0;
}
.bond-group-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 15px;
  cursor: pointer;
  user-select: none;
}
.bond-group-left {
  display: flex;
  align-items: center;
  gap: 10px;
}
.bond-group-icon {
  font-size: 12px;
  transition: transform 0.3s;
}
.bond-group.collapsed .bond-group-icon {
  transform: rotate(-90deg);
}
.bond-group-content {
  max-height: 2000px;
  overflow: visible;
  transition: max-height 0.3s ease-out;
}
.bond-group.collapsed .bond-group-content {
  max-height: 0;
}
.bond-group-title {
  font-size: 15px;
  font-weight: 600;
  color: #6B5DD3;
}
.host-list-section { margin-top: 15px; padding: 12px; background: #f8f9fa; border-radius: 8px; border: 1px solid #e0e0e0; }
.host-list-title { font-size: 14px; font-weight: 600; color: #333; margin-bottom: 8px; }
.host-list { max-height: 160px; overflow-y: auto; border: 1px solid #eee; border-radius: 6px; background: white; margin-bottom: 8px; }
.host-item { display: flex; justify-content: space-between; align-items: center; padding: 6px 10px; cursor: pointer; border-bottom: 1px solid #f0f0f0; font-size: 13px; }
.host-item:last-child { border-bottom: none; }
.host-item:hover { background: #f0f0f0; }
.host-item.selected { background: #e8f5e9; }
.host-ip { font-family: monospace; font-size: 13px; }
.host-status { font-weight: 600; font-size: 14px; }
.host-status.success { color: #4CAF50; }
.host-status.error { color: #f44336; }
.host-list-actions { display: flex; gap: 8px; }
.btn-group { display: flex; flex-direction: column; gap: 8px; margin-top: 15px; }
.btn { padding: 10px 20px; border: none; border-radius: 6px; font-size: 14px; font-weight: 600; cursor: pointer; transition: all 0.2s; }
.btn-full { width: 100%; }
.btn-primary { background: linear-gradient(135deg, #6B5DD3 0%, #8B7FE8 100%); color: white; }
.btn-primary:hover:not(:disabled) { transform: translateY(-2px); box-shadow: 0 5px 15px rgba(107, 93, 211, 0.4); }
.btn-primary:disabled { opacity: 0.6; cursor: not-allowed; }
.btn-success { background: #28a745; color: white; }
.btn-success:hover { background: #218838; }
.btn-danger { background: #dc3545; color: white; }
.btn-danger:hover { background: #c82333; }
.btn-secondary { background: #6c757d; color: white; }
.btn-secondary:hover { background: #5a6268; }
.btn-add { background: #17a2b8; color: white; }
.btn-add:hover { background: #138496; }
.btn-remove-small { background: #dc3545; color: white; padding: 6px 12px; font-size: 12px; border: none; border-radius: 4px; cursor: pointer; }
.btn-remove-small:hover { background: #c82333; }
.btn-small { padding: 6px 12px; font-size: 12px; background: #6c757d; color: white; border: none; border-radius: 4px; cursor: pointer; }
.btn-small:hover { background: #5a6268; }
.server-section {
  margin-bottom: 15px;
  border: 2px solid #e0e0e0;
  border-radius: 8px;
  overflow: hidden;
  background: white;
}
.server-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 12px 15px;
  background: linear-gradient(135deg, #6B5DD3 0%, #8B7FE8 100%);
  color: white;
  cursor: pointer;
  user-select: none;
}
.server-header:hover {
  background: linear-gradient(135deg, #5A4DC2 0%, #7A6FD7 100%);
}
.server-header-left {
  display: flex;
  align-items: center;
  gap: 10px;
  font-size: 14px;
  font-weight: 600;
}
.server-header-icon {
  font-size: 16px;
  transition: transform 0.3s;
}
.server-section.collapsed .server-header-icon {
  transform: rotate(-90deg);
}
.server-content {
  max-height: 1000px;
  overflow: hidden;
  transition: max-height 0.3s ease-out;
}
.server-section.collapsed .server-content {
  max-height: 0;
}
.status-badge {
  padding: 4px 12px;
  border-radius: 4px;
  font-size: 12px;
  font-weight: 600;
}
.status-badge.success {
  background: #d4edda;
  color: #155724;
}
.status-badge.failed {
  background: #f8d7da;
  color: #721c24;
}
.network-table {
  width: 100%;
  border-collapse: collapse;
}
.network-table th {
  background: #f8f9fa;
  padding: 10px;
  text-align: left;
  font-weight: 600;
  font-size: 13px;
  border-bottom: 2px solid #e0e0e0;
}
.network-table td {
  padding: 10px;
  border-bottom: 1px solid #e0e0e0;
  font-size: 13px;
}
.network-table tr:last-child td {
  border-bottom: none;
}
.network-table tr:hover {
  background: #f8f9fa;
}
.type-badge {
  display: inline-block;
  padding: 3px 8px;
  border-radius: 4px;
  font-size: 11px;
  font-weight: 600;
}
.type-badge.bond {
  background: #e7f3ff;
  color: #1976D2;
}
.type-badge.physical {
  background: #e8f5e9;
  color: #2e7d32;
}
.type-badge.virtual {
  background: #fff3e0;
  color: #f57c00;
}
.error-message {
  padding: 15px;
  text-align: center;
  color: #721c24;
  background: #f8d7da;
}
.log-output {
  background: #1e1e1e;
  color: #ddd;
  padding: 15px;
  border-radius: 6px;
  font-family: monospace;
  font-size: 12px;
  max-height: 400px;
  overflow-y: auto;
  white-space: pre-wrap;
}
.log-entry {
  margin-bottom: 4px;
}
.log-entry.success {
  color: #4caf50;
}
.log-entry.error {
  color: #f44336;
}
.log-entry.warning {
  color: #ff9800;
}
.log-entry.info {
  color: #2196F3;
}
.empty-section { flex: 1; display: flex; align-items: center; justify-content: center; min-height: 300px; }
.empty-state { text-align: center; color: #999; }
.empty-icon { font-size: 64px; margin-bottom: 15px; display: block; }
.confirmation-overlay {
  position: fixed;
  top: 0;
  left: 0;
  right: 0;
  bottom: 0;
  background: rgba(0, 0, 0, 0.5);
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: 2000;
}
.confirmation-dialog {
  background: white;
  border-radius: 12px;
  padding: 25px;
  max-width: 500px;
  width: 90%;
  box-shadow: 0 10px 40px rgba(0, 0, 0, 0.3);
}
.confirmation-dialog.warning {
  border-top: 4px solid #ffc107;
}
.confirmation-dialog h3 {
  margin: 0 0 15px 0;
  color: #856404;
  font-size: 18px;
}
.confirmation-content {
  margin-bottom: 20px;
  line-height: 1.8;
  white-space: pre-wrap;
  font-size: 14px;
  color: #333;
}
.confirmation-actions {
  display: flex;
  gap: 10px;
}
.btn-confirm {
  flex: 1;
  background: #28a745;
  color: white;
  padding: 10px 20px;
  border: none;
  border-radius: 6px;
  font-size: 14px;
  font-weight: 600;
  cursor: pointer;
}
.btn-confirm:hover {
  background: #218838;
}
.btn-cancel {
  flex: 1;
  background: #6c757d;
  color: white;
  padding: 10px 20px;
  border: none;
  border-radius: 6px;
  font-size: 14px;
  font-weight: 600;
  cursor: pointer;
}
.btn-cancel:hover {
  background: #5a6268;
}
.notification { position: fixed; top: 80px; right: 20px; padding: 12px 20px; border-radius: 8px; color: white; font-size: 14px; box-shadow: 0 4px 12px rgba(0,0,0,0.3); z-index: 1000; }
.notification.success { background: linear-gradient(135deg, #27ae60 0%, #2ecc71 100%); }
.notification.error { background: linear-gradient(135deg, #c0392b 0%, #e74c3c 100%); }
@media (max-width: 1024px) { .main-content { grid-template-columns: 1fr; } }
</style>
