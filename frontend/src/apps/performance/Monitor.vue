<template>
  <div class="monitor-page">
    <!-- 头部 -->
    <header class="header">
      <div style="display: flex; align-items: center; gap: 15px;">
        <router-link to="/" class="back-btn">🏠</router-link>
        <h1>系统监控</h1>
      </div>
      <div class="header-controls">
        <label for="server-address">远程主机:</label>
        <input type="text" id="server-address" v-model="serverAddress" placeholder="单IP或IP段" title="支持单个 IP、10.3.11.61-63、10.3.11.61-10.3.11.63，以及逗号或换行分隔" @keyup.enter="connect">
        <label for="username">用户名:</label>
        <input type="text" id="username" v-model="username" placeholder="root" @keyup.enter="connect">
        <label for="port">端口:</label>
        <input type="number" id="port" v-model="port" placeholder="22" style="width: 60px;" @keyup.enter="connect">
        <label for="auth-method">认证:</label>
        <select id="auth-method" v-model="authMethod">
          <option value="password">密码</option>
          <option value="key">服务器 SSH 密钥</option>
        </select>
        <template v-if="authMethod === 'password'">
          <label for="password">密码:</label>
          <input type="password" id="password" v-model="password" placeholder="******" @keyup.enter="connect">
        </template>
        <button id="connect-btn" class="btn-compact" @click="connect" :disabled="isConnecting || !canConnect">
          {{ isConnecting ? '连接中...' : '连接' }}
        </button>
      </div>
    </header>

    <!-- 主机标签页 -->
    <div class="tabs-bar" id="tabs-bar">
      <div class="tabs-container" id="tabs-container" ref="tabsContainer">
        <div v-if="Object.keys(connections).length === 0" class="tabs-bar-empty">
          暂无连接的主机，请在上方输入服务器信息并点击连接
        </div>
        <div
          v-for="(conn, host) in connections"
          :key="host"
          :class="['tab', { active: activeHost === host, dragging: draggingTab === host }]"
          :data-host="host"
          draggable="true"
          @dragstart="onDragStart($event, host)"
          @dragend="onDragEnd"
          @dragover="onDragOver($event, host)"
          @dragleave="onDragLeave"
          @drop="onDrop($event, host)"
          @click="selectHost(host)"
        >
          <span>📡</span>
          <span>{{ host }}</span>
          <span class="status-icon"></span>
          <span class="close-btn" @click.stop="disconnect(host)">×</span>
        </div>
      </div>
      <div class="tabs-info">
        <div class="current-time">{{ currentTime }}</div>
        <button
          :class="['collect-btn', 'btn-compact', { collecting: isCollecting }]"
          @click="toggleCollect"
          :disabled="!activeHost"
        >
          {{ isCollecting ? '停止收集' : '开始收集' }}
        </button>
      </div>
    </div>

    <!-- 监控面板区域 -->
    <div class="monitor-container">
      <!-- 第一列：CPU监控 -->
      <div class="monitor-panel">
        <div class="panel-header">
          <div class="panel-title">
            <span class="panel-icon">💻</span>
            <span>CPU 监控</span>
          </div>
          <div class="panel-actions">
            <button class="panel-btn" @click="refreshData">刷新</button>
          </div>
        </div>
        <div class="panel-content" v-html="cpuContent"></div>
      </div>

      <!-- 第二列：磁盘监控（iostat） -->
      <div class="monitor-panel disk-panel">
        <div class="panel-header">
          <div class="panel-title">
            <span class="panel-icon">💾</span>
            <span>磁盘监控 (iostat)</span>
          </div>
          <div class="panel-actions">
            <label class="checkbox-label">
              <input type="checkbox" v-model="excludeSystemDisk" @change="refreshData">
              <span>排除系统盘</span>
            </label>
          </div>
        </div>
        <div class="panel-content" v-html="diskContent"></div>
      </div>
    </div>

    <!-- 保存对话框 -->
    <div v-if="showSaveDialog" class="dialog-overlay" @click.self="showSaveDialog = false">
      <div class="dialog">
        <h3>收集完成</h3>
        <p>共收集 {{ collectedDataCount }} 条数据</p>
        <div class="form-group">
          <label>文件名:</label>
          <input type="text" v-model="saveFilename" id="save-filename">
        </div>
        <div class="dialog-actions">
          <button class="btn-primary" @click="saveToLocal">保存到本地</button>
          <button class="btn-secondary" @click="discardData">丢弃</button>
        </div>
      </div>
    </div>

    <!-- 重连提示对话框 -->
    <ReconnectDialog
      api-endpoint="/api/v1/perf/monitor"
      storage-key="monitor"
      :on-reconnect="handleReconnect"
      :auto-reconnect="true"
    />

    <!-- 通知消息 -->
    <Transition name="slide">
      <div v-if="notification.show" :class="['notification', notification.type]">
        {{ notification.message }}
      </div>
    </Transition>
  </div>
</template>

<script setup>
import { ref, computed, onMounted, onUnmounted } from 'vue'
import api from '@/api'
import ReconnectDialog from '@/components/ReconnectDialog.vue'
import { usePageStatePersistence } from '@/apps/common/usePageStatePersistence'

// 状态
const serverAddress = ref('')
const username = ref('root')
const port = ref(22)
const password = ref('')
const authMethod = ref('password')
const isConnecting = ref(false)
const activeHost = ref(null)
const currentTime = ref('--:--:--')
const excludeSystemDisk = ref(true)
const isCollecting = ref(false)
const collectingHost = ref(null)
const showSaveDialog = ref(false)
const collectedDataCount = ref(0)
const saveFilename = ref('')
const draggingTab = ref(null)
const dragOverTab = ref(null)

// Computed
const canConnect = computed(() => {
  return serverAddress.value.trim() && username.value.trim() &&
    (authMethod.value === 'key' || password.value)
})

// 处理重新连接
const handleReconnect = async (history) => {
  showNotification(`正在重新连接 ${history.length} 个主机...`, 'info')

  // 并发连接所有主机
  for (const conn of history) {
    await connectToServer(conn.host, conn.username, conn.password, conn.port, conn.auth_method)
  }

  // 恢复收集状态
  try {
    const savedCollecting = localStorage.getItem('monitor_collecting_hosts')
    if (savedCollecting) {
      const collectingHosts = JSON.parse(savedCollecting)
      await new Promise(resolve => setTimeout(resolve, 1000))

      for (const host of collectingHosts) {
        if (connections.value[host]) {
          toggleCollect(host)
        }
      }
    }
  } catch (e) {
    console.error('恢复收集状态失败:', e)
  }

  showNotification(`已成功连接 ${history.length} 个主机`, 'success')
}

// 连接和监控数据
const connections = ref({})
const monitoringIntervals = ref({})
const collectionIntervals = ref({})
const collections = ref({}) // { host: { data: [], startTime: null } }

// 通知
const notification = ref({ show: false, message: '', type: 'info' })

// 显示内容
const cpuContent = ref('')
const diskContent = ref('')

// 更新时间
const updateTime = () => {
  const now = new Date()
  currentTime.value = now.toLocaleTimeString('zh-CN', { hour12: false })
}

let timeInterval = null

// 解析地址输入：支持单 IP、逗号/换行分隔，以及同网段连续 IP 段。
const parseAddresses = (input) => {
  const addresses = []
  const seen = new Set()

  const addAddress = (address) => {
    if (!seen.has(address)) {
      seen.add(address)
      addresses.push(address)
    }
  }

  for (const entry of input.split(/[\n,]+/).map(item => item.trim()).filter(Boolean)) {
    const shortRange = entry.match(/^(\d+\.\d+\.\d+\.)(\d+)-(\d+)$/)
    const fullRange = entry.match(/^(\d+\.\d+\.\d+\.)(\d+)-(\d+\.\d+\.\d+\.)(\d+)$/)

    let prefix = ''
    let start = 0
    let end = 0
    if (shortRange) {
      [, prefix, start, end] = shortRange
    } else if (fullRange && fullRange[1] === fullRange[3]) {
      prefix = fullRange[1]
      start = fullRange[2]
      end = fullRange[4]
    } else {
      addAddress(entry)
      continue
    }

    start = Number(start)
    end = Number(end)
    if (!Number.isInteger(start) || !Number.isInteger(end) || start < 0 || end > 255 || start > end) {
      addAddress(entry)
      continue
    }
    for (let host = start; host <= end; host++) {
      addAddress(`${prefix}${host}`)
    }
  }

  return addresses
}

// 显示通知
const showNotification = (message, type = 'info') => {
  notification.value = { show: true, message, type }
  setTimeout(() => {
    notification.value.show = false
  }, 3000)
}

// 连接到单个服务器
const connectToServer = async (host, user, pwd, port = 22, auth_method = authMethod.value) => {
  try {
    const response = await api.post('/perf/monitor/connect', {
      host: host,
      username: user,
      auth_method,
      password: pwd,
      port: port
    })

    if (response.status === 'success') {
      connections.value[host] = {
        connection_id: response.connection_id,
        host: host,
        username: user,
        auth_method,
        password: pwd,
        port: port,
        system_info: response.system_info,
        data: null
      }

      // 初始化收集状态
      if (!collections.value[host]) {
        collections.value[host] = { data: [], startTime: null }
      }

      // 自动选择第一个连接
      if (!activeHost.value) {
        activeHost.value = host
      }

      // 开始监控
      startMonitoring(host)

      showNotification(`成功连接到 ${host}`, 'success')

      // 保存连接历史（包含后端启动时间）
      saveConnectionHistory(response.server_start_time)

      return true
    } else {
      showNotification(`连接失败: ${response.error}`, 'error')
      return false
    }
  } catch (error) {
    showNotification(`连接错误: ${error.message}`, 'error')
    return false
  }
}

// 开始监控
const startMonitoring = (host) => {
  // 立即更新一次
  updateSystemInfo(host)

  // 每1秒更新一次
  monitoringIntervals.value[host] = setInterval(() => {
    updateSystemInfo(host)
  }, 1000)
}

// 更新系统信息
const updateSystemInfo = async (host) => {
  if (!connections.value[host]) return

  try {
    const response = await api.post('/perf/monitor/system-info', {
      connection_id: connections.value[host].connection_id
    })

    console.log('CPU data response:', response)

    if (response.status === 'success') {
      connections.value[host].data = response.data
      console.log('CPU info:', response.data?.cpu)

      // Sample only after a successful response so collection never records
      // an empty snapshot while the asynchronous request is still pending.
      if (collections.value[host]?.isCollecting) {
        collectCurrentData(host, response.data)
      }

      if (activeHost.value === host) {
        updateDisplay(host, response.data)
      }
    } else if (response.status === 'error') {
      if (response.error === '连接不存在或已断开') {
        showNotification(`连接 ${host} 已失效，请重新连接`, 'error')
        if (monitoringIntervals.value[host]) {
          clearInterval(monitoringIntervals.value[host])
          delete monitoringIntervals.value[host]
        }
      } else {
        console.error('API error:', response.error)
      }
    }
  } catch (error) {
    console.error('获取系统信息错误:', error)
  }
}

// 连接到服务器
const connect = async () => {
  const addressInput = serverAddress.value.trim()
  const user = username.value.trim()
  const pwd = password.value
  const portNum = port.value || 22

  if (!addressInput || !user || (authMethod.value === 'password' && !pwd)) {
    showNotification('请填写完整的连接信息', 'error')
    return
  }

  isConnecting.value = true

  try {
    const addresses = parseAddresses(addressInput)
    let successCount = 0

    for (const host of addresses) {
      const success = await connectToServer(host, user, pwd, portNum, authMethod.value)
      if (success) successCount++
      await new Promise(resolve => setTimeout(resolve, 100))
    }

    if (addresses.length > 1) {
      showNotification(`批量连接完成: ${successCount}/${addresses.length} 成功`,
        successCount > 0 ? 'success' : 'error')
    }

    password.value = ''
  } finally {
    isConnecting.value = false
  }
}

// 刷新当前数据
const refreshData = () => {
  if (activeHost.value && connections.value[activeHost.value]?.data) {
    updateDisplay(activeHost.value, connections.value[activeHost.value].data)
  }
}

// 选择主机
const selectHost = (host) => {
  activeHost.value = host
  if (connections.value[host]?.data) {
    updateDisplay(host, connections.value[host].data)
  }
  updateCollectButton()
}

// 断开连接
const disconnect = async (host) => {
  if (!connections.value[host]) return

  // 停止监控
  if (monitoringIntervals.value[host]) {
    clearInterval(monitoringIntervals.value[host])
    delete monitoringIntervals.value[host]
  }

  // Stop collection state if its bound host is disconnected.
  if (collectingHost.value === host) {
    collections.value[host].isCollecting = false
    collections.value[host].interval = null
    collectingHost.value = null
    isCollecting.value = false
  }
  if (collectionIntervals.value[host]) {
    clearInterval(collectionIntervals.value[host])
    delete collectionIntervals.value[host]
  }

  try {
    await api.post('/perf/monitor/disconnect', {
      connection_id: connections.value[host].connection_id
    })
  } catch (error) {
    console.error('断开连接错误:', error)
  }

  delete connections.value[host]

  // 如果断开的是当前活动主机，切换到其他
  if (activeHost.value === host) {
    const hosts = Object.keys(connections.value)
    if (hosts.length > 0) {
      activeHost.value = hosts[0]
      updateDisplay(activeHost.value, connections.value[activeHost.value]?.data)
    } else {
      activeHost.value = null
    }
  }

  // 保存连接历史
  saveConnectionHistory()

  showNotification(`已断开与 ${host} 的连接`, 'info')
}

// 生成CPU条形图
const generateCpuBar = (usage, maxWidth = 20) => {
  const blocks = ['▏', '▎', '▍', '▌', '▋', '▊', '▉', '█']
  const fullBlocks = Math.floor(usage / 100 * maxWidth)
  const remainder = (usage / 100 * maxWidth) - fullBlocks
  const partialBlock = remainder > 0 ? blocks[Math.floor(remainder * 8)] : ''

  let bar = '█'.repeat(fullBlocks) + partialBlock
  const emptyBlocks = maxWidth - fullBlocks - (partialBlock ? 1 : 0)
  bar += '░'.repeat(Math.max(0, emptyBlocks))

  let color = '#4CAF50'
  if (usage > 80) color = '#f44336'
  else if (usage > 60) color = '#FF9800'

  return `<span style="color: ${color}; font-family: monospace;">${bar}</span>`
}

// 更新显示
const updateDisplay = (host, data) => {
  if (!data) {
    cpuContent.value = `
      <div class="placeholder">
        <div class="placeholder-icon">📊</div>
        <div class="placeholder-text">CPU 监控数据将显示在这里</div>
      </div>
    `
    diskContent.value = `
      <div class="placeholder">
        <div class="placeholder-icon">💿</div>
        <div class="placeholder-text">磁盘 I/O 统计将显示在这里</div>
      </div>
    `
    return
  }

  // CPU显示
  if (data.cpu?.numa_nodes && data.cpu.numa_nodes.length > 0) {
    cpuContent.value = `
      <div style="padding: 10px; overflow: auto; height: 100%; font-size: 12px;">
        <div style="margin-bottom: 10px; padding: 10px; background: #f5f5f5; border-radius: 8px;">
          <span style="color: #666;">总体使用率: </span>
          <span style="font-size: 18px; font-weight: bold; color: #667eea;">${data.cpu.usage?.toFixed(1) || 'N/A'}%</span>
          <span style="color: #666; margin-left: 20px;">核心数: ${data.cpu.cores || 'N/A'}</span>
          <span style="color: #666; margin-left: 20px;">负载: ${data.cpu.load_1min?.toFixed(2) || 'N/A'} / ${data.cpu.load_5min?.toFixed(2) || 'N/A'} / ${data.cpu.load_15min?.toFixed(2) || 'N/A'}</span>
        </div>
        <table style="width: 100%; color: #333; border-collapse: collapse;">
          <thead>
            <tr style="border-bottom: 2px solid #e0e0e0; background: #f5f5f0;">
              <th style="padding: 8px 10px; text-align: left; position: sticky; left: 0; background: #f5f5f5; border-right: 1px solid #e0e0e0; width: 100px;">NUMA 节点</th>
              <th style="padding: 8px 10px; text-align: left;">CPU 核心统计</th>
            </tr>
          </thead>
          <tbody>
            ${data.cpu.numa_nodes.map(node => `
              <tr style="border-bottom: 1px solid #f0f0f0; vertical-align: top;">
                <td style="padding: 12px 10px; font-weight: bold; position: sticky; left: 0; background: white; border-right: 1px solid #e0e0e0; color: #667eea;">
                  Node ${node.node}
                </td>
                <td style="padding: 8px 10px;">
                  <div style="display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 6px;">
                    ${node.cpus.map(cpu => {
                      const usage = 100 - cpu.id
                      let usageColor = '#4CAF50'
                      if (usage > 80) usageColor = '#f44336'
                      else if (usage > 60) usageColor = '#FF9800'

                      return `
                        <div style="min-width: 0; padding: 4px 6px; background: #fafafa; border-left: 3px solid ${usageColor}; border-radius: 4px; font-size: 10px; line-height: 1.5; overflow: hidden;">
                          <div style="font-weight: bold; color: #333; display: flex; align-items: center; gap: 4px; min-width: 0;">
                            <span style="min-width: 58px; flex-shrink: 0;">CPU${cpu.cpu}: <span style="color: ${usageColor};">${usage.toFixed(1)}%</span></span>
                            <span style="min-width: 0; overflow: hidden;">${generateCpuBar(usage, 10)}</span>
                          </div>
                          <div style="color: #666; font-size: 9px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis;">
                            us:${cpu.us?.toFixed(1)} sy:${cpu.sy?.toFixed(1)} wa:${cpu.wa?.toFixed(1)} hi:${cpu.hi?.toFixed(1)} si:${cpu.si?.toFixed(1)}
                          </div>
                        </div>
                      `
                    }).join('')}
                  </div>
                </td>
              </tr>
            `).join('')}
          </tbody>
        </table>
      </div>
    `
  } else if (data.cpu?.usage !== undefined) {
    cpuContent.value = `
      <div style="padding: 20px;">
        <div style="font-size: 48px; font-weight: bold; color: #4CAF50; margin-bottom: 20px;">
          ${data.cpu.usage.toFixed(1)}%
        </div>
        <div style="color: #666;">
          <div>核心数: ${data.cpu.cores || 'N/A'}</div>
          <div>负载: ${data.cpu.load_1min?.toFixed(2) || 'N/A'} / ${data.cpu.load_5min?.toFixed(2) || 'N/A'} / ${data.cpu.load_15min?.toFixed(2) || 'N/A'}</div>
        </div>
      </div>
    `
  }

  // 磁盘显示
  if (data.disk?.iostat && data.disk.iostat.length > 0) {
    let disks = data.disk.iostat
    if (excludeSystemDisk.value) {
      const systemDisks = new Set((data.disk.system_disks || []).map(device => device.toLowerCase()))
      disks = disks.filter(disk => !systemDisks.has(disk.device.toLowerCase()))
    }

    diskContent.value = `
      <div style="padding: 10px; overflow: auto; height: 100%; font-size: 13px;">
        <table style="width: 100%; color: #333; border-collapse: collapse; white-space: nowrap;">
          <thead>
            <tr style="border-bottom: 2px solid #e0e0e0; background: #f5f5f5;">
              <th style="padding: 8px 10px; text-align: left; position: sticky; left: 0; background: #f5f5f5; border-right: 1px solid #e0e0e0;">设备</th>
              <th style="padding: 8px 10px; text-align: right;">r/s<br><span style="font-size: 11px; color: #999;">读/秒</span></th>
              <th style="padding: 8px 10px; text-align: right;">w/s<br><span style="font-size: 11px; color: #999;">写/秒</span></th>
              <th style="padding: 8px 10px; text-align: right;">rMB/s<br><span style="font-size: 11px; color: #999;">读MB/秒</span></th>
              <th style="padding: 8px 10px; text-align: right;">wMB/s<br><span style="font-size: 11px; color: #999;">写MB/秒</span></th>
              <th style="padding: 8px 10px; text-align: right;">avgqu-sz<br><span style="font-size: 11px; color: #999;">队列长度</span></th>
              <th style="padding: 8px 10px; text-align: right;">await<br><span style="font-size: 11px; color: #999;">等待(ms)</span></th>
              <th style="padding: 8px 10px; text-align: right;">r_await<br><span style="font-size: 11px; color: #999;">读等待(ms)</span></th>
              <th style="padding: 8px 10px; text-align: right;">w_await<br><span style="font-size: 11px; color: #999;">写等待(ms)</span></th>
              <th style="padding: 8px 10px; text-align: right;">%util<br><span style="font-size: 11px; color: #999;">利用率</span></th>
            </tr>
          </thead>
          <tbody>
            ${disks.map(disk => {
              let utilColor = '#4CAF50'
              if (disk.util > 80) utilColor = '#f44336'
              else if (disk.util > 60) utilColor = '#FF9800'

              let awaitColor = '#4CAF50'
              if (disk.await > 50) awaitColor = '#f44336'
              else if (disk.await > 20) awaitColor = '#FF9800'

              let queueColor = '#4CAF50'
              if (disk.avgqu_sz > 2) queueColor = '#f44336'
              else if (disk.avgqu_sz > 1) queueColor = '#FF9800'

              return `
                <tr style="border-bottom: 1px solid #f0f0f0;">
                  <td style="padding: 8px 10px; font-weight: bold; position: sticky; left: 0; background: white; border-right: 1px solid #e0e0e0;">${disk.device}</td>
                  <td style="padding: 8px 10px; text-align: right;">${disk.r_s?.toFixed(1)}</td>
                  <td style="padding: 8px 10px; text-align: right;">${disk.w_s?.toFixed(1)}</td>
                  <td style="padding: 8px 10px; text-align: right;">${disk.rMB_s?.toFixed(2)}</td>
                  <td style="padding: 8px 10px; text-align: right;">${disk.wMB_s?.toFixed(2)}</td>
                  <td style="padding: 8px 10px; text-align: right; color: ${queueColor};">${disk.avgqu_sz?.toFixed(2)}</td>
                  <td style="padding: 8px 10px; text-align: right; color: ${awaitColor}; font-weight: bold;">${disk.await?.toFixed(1)}</td>
                  <td style="padding: 8px 10px; text-align: right; color: ${awaitColor};">${disk.r_await?.toFixed(1)}</td>
                  <td style="padding: 8px 10px; text-align: right; color: ${awaitColor};">${disk.w_await?.toFixed(1)}</td>
                  <td style="padding: 8px 10px; text-align: right; color: ${utilColor}; font-weight: bold;">${disk.util?.toFixed(1)}%</td>
                </tr>
              `
            }).join('')}
          </tbody>
        </table>
        <div style="margin-top: 10px; padding: 8px; font-size: 11px; color: #666; border-top: 1px solid #f0f0f0;">
          <div style="margin-bottom: 5px;">
            磁盘空间: ${data.disk.total || 'N/A'} (已用: ${data.disk.usage || 'N/A'}) | 显示 ${disks.length} 个磁盘
          </div>
          <div style="font-size: 10px; color: #999; line-height: 1.5;">
            <span style="color: #4CAF50;">正常</span>: 利用率&lt;60%, 等待&lt;20ms, 队列&lt;1 |
            <span style="color: #FF9800;">警告</span>: 利用率60-80%, 等待20-50ms, 队列1-2 |
            <span style="color: #f44336;">瓶颈</span>: 利用率&gt;80%, 等待&gt;50ms, 队列&gt;2
          </div>
        </div>
      </div>
    `
  } else if (data.disk?.usage) {
    diskContent.value = `
      <div style="padding: 20px;">
        <div style="font-size: 48px; font-weight: bold; color: #FF9800; margin-bottom: 20px;">
          ${data.disk.usage}
        </div>
        <div style="color: #666;">
          <div>总计: ${data.disk.total}</div>
          <div>已用: ${data.disk.used}</div>
          <div>可用: ${data.disk.free}</div>
        </div>
      </div>
    `
  }
}

// 开始/停止收集
const toggleCollect = () => {
  if (!activeHost.value) {
    showNotification('请先连接到服务器', 'error')
    return
  }

  // A collection remains bound to the host selected when it started.
  // Switching tabs must not stop or save another host's empty collection.
  const host = collectingHost.value || activeHost.value

  if (!collections.value[host]) {
    collections.value[host] = { data: [], startTime: null }
  }

  const collection = collections.value[host]

  if (!collectingHost.value) {
    // 开始收集
    collection.isCollecting = true
    collectingHost.value = host
    collection.data = []
    collection.startTime = new Date()
    // updateSystemInfo records each successfully returned monitoring sample.
    collection.interval = true
    if (connections.value[host]?.data) {
      collectCurrentData(host, connections.value[host].data)
    }
    isCollecting.value = true
    showNotification(`开始收集 ${host} 的数据...`, 'success')
  } else {
    // 停止收集
    collection.isCollecting = false
    collection.interval = null
    collectingHost.value = null
    isCollecting.value = false
    collectedDataCount.value = collection.data.length

    // 生成文件名
    const startTime = collection.startTime.toISOString().replace(/[:.]/g, '-').slice(0, -5)
    const endTime = new Date().toISOString().replace(/[:.]/g, '-').slice(0, -5)
    saveFilename.value = `${host}-iostat-cpu-${startTime}-${endTime}.txt`

    showSaveDialog.value = true
  }
}

// 更新收集按钮状态
const updateCollectButton = () => {
  isCollecting.value = Boolean(collectingHost.value)
}

// 收集当前数据
const collectCurrentData = (host, snapshot = null) => {
  const connection = connections.value[host]
  const collection = collections.value[host]
  const data = snapshot || connection?.data

  if (!connection || !data || !collection) return

  const timestamp = new Date().toLocaleString('zh-CN', {
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
    hour12: false
  }).replace(/\//g, '/')

  let dataBlock = timestamp + '\n'

  // CPU 信息：使用固定列宽，便于直接在终端或编辑器中查看。
  if (data.cpu?.numa_nodes && data.cpu.numa_nodes.length > 0) {
    const cpuColumns = [
      ['CPU', 6, false], ['%usr', 7], ['%sys', 7], ['%nice', 7],
      ['%idle', 7], ['%iowait', 8], ['%irq', 7], ['%soft', 7], ['%steal', 8]
    ]
    dataBlock += cpuColumns.map(([name, width, rightAligned = true]) =>
      (rightAligned ? name.padStart(width) : name.padEnd(width))
    ).join(' ') + '\n'

    data.cpu.numa_nodes.forEach(node => {
      if (node.cpus && node.cpus.length > 0) {
        node.cpus.forEach(cpu => {
          dataBlock += `${(`CPU${cpu.cpu}`).padEnd(6)} ` +
            `${formatMonitorNumber(cpu.us, 7, 1)} ` +
            `${formatMonitorNumber(cpu.sy, 7, 1)} ` +
            `${formatMonitorNumber(cpu.ni, 7, 1)} ` +
            `${formatMonitorNumber(cpu.id, 7, 1)} ` +
            `${formatMonitorNumber(cpu.wa, 8, 1)} ` +
            `${formatMonitorNumber(cpu.hi, 7, 1)} ` +
            `${formatMonitorNumber(cpu.si, 7, 1)} ` +
            `${formatMonitorNumber(cpu.st, 8, 1)}\n`
        })
      }
    })
  }

  // 磁盘信息
  if (data.disk?.iostat && data.disk.iostat.length > 0) {
    const iostatColumns = [
      ['Device', 16, false], ['r/s', 8], ['rMB/s', 9], ['rrqm/s', 9],
      ['%rrqm', 7], ['r_await', 7], ['rareq-sz', 9], ['w/s', 8],
      ['wMB/s', 9], ['wrqm/s', 9], ['%wrqm', 7], ['w_await', 7],
      ['wareq-sz', 9], ['d/s', 8], ['dMB/s', 9], ['drqm/s', 9],
      ['%drqm', 7], ['d_await', 7], ['dareq-sz', 9], ['f/s', 8],
      ['f_await', 7], ['aqu-sz', 7], ['%util', 6]
    ]
    dataBlock += iostatColumns.map(([name, width, rightAligned = true]) =>
      (rightAligned ? name.padStart(width) : name.padEnd(width))
    ).join(' ') + '\n'

    data.disk.iostat.forEach(disk => {
      const line = `${String(disk.device || '').slice(0, 16).padEnd(16)} ` +
        `${formatIostatNumber(disk.r_s, 8)} ` +
        `${formatIostatNumber(disk.rMB_s, 9)} ` +
        `${formatIostatNumber(disk.rrqm_s, 9)} ` +
        `${formatIostatNumber(0, 7)} ` +
        `${formatIostatNumber(disk.r_await, 7)} ` +
        `${formatIostatNumber(0, 9)} ` +
        `${formatIostatNumber(disk.w_s, 8)} ` +
        `${formatIostatNumber(disk.wMB_s, 9)} ` +
        `${formatIostatNumber(disk.wrqm_s, 9)} ` +
        `${formatIostatNumber(0, 7)} ` +
        `${formatIostatNumber(disk.w_await, 7)} ` +
        `${formatIostatNumber(0, 9)} ` +
        `${formatIostatNumber(0, 8)} ` +
        `${formatIostatNumber(0, 9)} ` +
        `${formatIostatNumber(0, 9)} ` +
        `${formatIostatNumber(0, 7)} ` +
        `${formatIostatNumber(0, 7)} ` +
        `${formatIostatNumber(0, 9)} ` +
        `${formatIostatNumber(0, 8)} ` +
        `${formatIostatNumber(0, 7)} ` +
        `${formatIostatNumber(disk.avgqu_sz, 7)} ` +
        `${formatIostatNumber(disk.util, 6)}
`
      dataBlock += line
    })
  }

  collection.data.push(dataBlock)
}

// 监控导出使用固定宽度字段，缺失数据统一以 0 补齐。
const formatMonitorNumber = (value, width, digits = 2) => {
  const number = Number(value)
  const formatted = Number.isFinite(number) ? number.toFixed(digits) : (0).toFixed(digits)
  return formatted.padStart(width)
}

const formatIostatNumber = formatMonitorNumber

// 保存到本地
const saveToLocal = () => {
  const host = activeHost.value
  const collection = collections.value[host]

  if (!collection || collection.data.length === 0) {
    showNotification('没有收集到数据', 'error')
    return
  }

  try {
    const content = collection.data.join('\n')
    const blob = new Blob([content], { type: 'text/plain;charset=utf-8' })
    const url = URL.createObjectURL(blob)
    const filename = saveFilename.value || `${host}-data.txt`
    const link = document.createElement('a')

    // Attach the link and delay URL cleanup for browser compatibility.
    link.href = url
    link.download = filename
    link.style.display = 'none'
    document.body.appendChild(link)
    link.click()
    document.body.removeChild(link)
    setTimeout(() => URL.revokeObjectURL(url), 1000)

    collection.data = []
    showSaveDialog.value = false
    showNotification(`数据已保存到本地: ${filename}`, 'success')
  } catch (error) {
    console.error('保存监控日志失败:', error)
    showNotification(`保存失败: ${error.message}`, 'error')
  }
}

// 丢弃数据
const discardData = () => {
  const host = activeHost.value
  if (collections.value[host]) {
    collections.value[host].data = []
  }
  showSaveDialog.value = false
  showNotification('数据已丢弃', 'success')
}

// 拖拽相关
const onDragStart = (event, host) => {
  draggingTab.value = host
  event.dataTransfer.effectAllowed = 'move'
  event.dataTransfer.setData('text/plain', host)
}

const onDragEnd = () => {
  draggingTab.value = null
  dragOverTab.value = null
}

const onDragOver = (event, host) => {
  event.preventDefault()
  if (draggingTab.value && draggingTab.value !== host) {
    dragOverTab.value = host
  }
}

const onDragLeave = () => {
  dragOverTab.value = null
}

const onDrop = (event, targetHost) => {
  event.preventDefault()
  const draggedHost = event.dataTransfer.getData('text/plain')

  if (draggedHost && draggedHost !== targetHost) {
    const tabsContainer = document.getElementById('tabs-container')
    const allTabs = Array.from(tabsContainer.querySelectorAll('.tab'))
    const draggedIndex = allTabs.findIndex(t => t.getAttribute('data-host') === draggedHost)
    const targetIndex = allTabs.findIndex(t => t.getAttribute('data-host') === targetHost)

    // 交换位置
    const temp = connections.value[draggedHost]
    delete connections.value[draggedHost]
    connections.value[draggedHost] = temp

    if (draggedIndex < targetIndex) {
      tabsContainer.insertBefore(
        tabsContainer.querySelector(`[data-host="${draggedHost}"]`),
        tabsContainer.querySelector(`[data-host="${targetHost}"]`).nextSibling
      )
    } else {
      tabsContainer.insertBefore(
        tabsContainer.querySelector(`[data-host="${draggedHost}"]`),
        tabsContainer.querySelector(`[data-host="${targetHost}"]`)
      )
    }
  }

  dragOverTab.value = null
}

// 保存连接历史到 localStorage
const saveConnectionHistory = (serverStartTime = null) => {
  const history = []
  const collectingHosts = []

  for (const host in connections.value) {
    if (connections.value[host]) {
      history.push({
        host: connections.value[host].host,
        username: connections.value[host].username,
        password: connections.value[host].password
      })

      // 保存收集状态
      if (collections.value[host]?.isCollecting) {
        collectingHosts.push(host)
      }
    }
  }

  if (history.length > 0) {
    localStorage.setItem('monitor_connection_history', JSON.stringify(history))
    localStorage.setItem('monitor_collecting_hosts', JSON.stringify(collectingHosts))
    if (serverStartTime !== null) {
      localStorage.setItem('monitor_server_start_time', String(serverStartTime))
    }
  } else {
    localStorage.removeItem('monitor_connection_history')
    localStorage.removeItem('monitor_collecting_hosts')
    localStorage.removeItem('monitor_server_start_time')
  }
}

// 检查是否需要清除历史记录（后端重启过）
const checkServerRestart = async () => {
  try {
    const response = await api.get('/perf/monitor/server-info')
    const currentServerStartTime = response.server_start_time
    const savedServerStartTime = localStorage.getItem('monitor_server_start_time')

    // 如果后端重启过（时间不一致），清除历史
    if (savedServerStartTime && savedServerStartTime !== String(currentServerStartTime)) {
      localStorage.removeItem('monitor_connection_history')
      localStorage.removeItem('monitor_collecting_hosts')
      localStorage.removeItem('monitor_server_start_time')
      return false
    }
    return true
  } catch (error) {
    return false
  }
}

// 从历史记录加载并询问用户是否重连
const promptReconnect = async () => {
  // 先检查后端是否重启过
  const isValid = await checkServerRestart()
  if (!isValid) return

  const saved = localStorage.getItem('monitor_connection_history')
  if (!saved) return

  try {
    const history = JSON.parse(saved)
    if (history.length === 0) return

    // This is now handled by ReconnectDialog component
  } catch (error) {
    console.error('加载连接历史失败:', error)
    localStorage.removeItem('monitor_connection_history')
  }
}

usePageStatePersistence('monitor_ui_state', () => ({
  serverAddress: serverAddress.value,
  username: username.value,
  port: port.value,
  password: password.value,
  authMethod: authMethod.value,
  activeHost: activeHost.value,
  excludeSystemDisk: excludeSystemDisk.value,
  showSaveDialog: showSaveDialog.value,
  collectedDataCount: collectedDataCount.value,
  saveFilename: saveFilename.value,
  cpuContent: cpuContent.value,
  diskContent: diskContent.value
}), {
  hydrate: (saved) => {
    serverAddress.value = saved.serverAddress || ''
    username.value = saved.username || 'root'
    port.value = saved.port ?? 22
    password.value = saved.password || ''
    authMethod.value = saved.authMethod || 'password'
    activeHost.value = saved.activeHost || null
    excludeSystemDisk.value = saved.excludeSystemDisk ?? true
    showSaveDialog.value = Boolean(saved.showSaveDialog)
    collectedDataCount.value = saved.collectedDataCount || 0
    saveFilename.value = saved.saveFilename || ''
    cpuContent.value = saved.cpuContent || ''
    diskContent.value = saved.diskContent || ''
  }
})

// 页面加载时初始化
onMounted(() => {
  updateTime()
  timeInterval = setInterval(updateTime, 1000)
  promptReconnect()
})

// 清理
onUnmounted(() => {
  if (timeInterval) clearInterval(timeInterval)

  Object.values(monitoringIntervals.value).forEach(interval => {
    if (interval) clearInterval(interval)
  })

  Object.values(collectionIntervals.value).forEach(interval => {
    if (interval) clearInterval(interval)
  })

  // 断开所有连接
  Object.keys(connections.value).forEach(host => {
    api.post('/perf/monitor/disconnect', {
      connection_id: connections.value[host].connection_id
    }).catch(() => {})
  })
})
</script>

<style scoped>
.monitor-page {
  width: 100%;
  min-height: 100vh;
  padding: 20px;
  background: linear-gradient(135deg, #6B5DD3 0%, #8B7FE8 50%, #9B8FF8 100%);
  position: relative;
  display: flex;
  flex-direction: column;
}

.monitor-page::before {
  content: '';
  position: fixed;
  top: 0;
  left: 0;
  right: 0;
  bottom: 0;
  background: repeating-linear-gradient(
    45deg,
    transparent,
    transparent 35px,
    rgba(255, 255, 255, 0.05) 35px,
    rgba(255, 255, 255, 0.05) 70px
  );
  pointer-events: none;
  z-index: 0;
}

.monitor-page > * {
  position: relative;
  z-index: 1;
}

/* 连接表单 - 改用header样式与VDBench一致 */
.header {
  background: white;
  padding: 15px 20px;
  border-radius: 12px;
  box-shadow: 0 4px 6px rgba(0, 0, 0, 0.1);
  margin-bottom: 20px;
  display: flex;
  justify-content: space-between;
  align-items: center;
  flex-wrap: wrap;
  gap: 15px;
}

.header h1 {
  font-size: 24px;
  color: #6B5DD3;
  margin: 0;
}

.back-btn {
  color: #6B5DD3;
  text-decoration: none;
  font-size: 24px;
  padding: 8px 12px;
  background: rgba(107, 93, 211, 0.1);
  border-radius: 8px;
  transition: all 0.3s;
}

.back-btn:hover {
  background: rgba(107, 93, 211, 0.2);
  transform: scale(1.1);
}

.header-controls {
  display: flex;
  align-items: center;
  gap: 15px;
  flex-wrap: wrap;
}

.header-controls label {
  font-size: 13px;
  color: #666;
}

.header-controls input {
  padding: 8px 12px;
  border: 1px solid #e0e0e0;
  border-radius: 6px;
  font-size: 14px;
  outline: none;
  background: white;
  width: 150px;
}

.header-controls input:focus {
  border-color: #6B5DD3;
}

#connect-btn {
  padding: 7px 20px;
  width: 86px;
  background: #6B5DD3;
  color: white;
  border: none;
  border-radius: 6px;
  font-size: 14px;
  font-weight: 600;
  cursor: pointer;
  transition: all 0.3s;
}

#connect-btn:hover:not(:disabled) {
  transform: translateY(-2px);
  box-shadow: 0 4px 12px rgba(107, 93, 211, 0.3);
}

#connect-btn:disabled {
  background: #f0eef8 !important;
  color: #9b8ff8 !important;
  border: 1px solid #e0ddf0 !important;
  cursor: not-allowed;
}

/* 标签栏 */
.tabs-bar {
  background: rgba(255, 255, 255, 0.95);
  padding: 10px 20px;
  display: flex;
  align-items: center;
  gap: 10px;
  box-shadow: 0 4px 6px rgba(0, 0, 0, 0.1);
  overflow-x: auto;
  min-height: 50px;
  border-radius: 12px;
  margin-bottom: 20px;
  justify-content: space-between;
}

.tabs-container {
  display: flex;
  align-items: center;
  gap: 10px;
  flex: 1;
  overflow-x: auto;
}

.tabs-info {
  display: flex;
  align-items: center;
  gap: 15px;
  margin-left: auto;
}

.current-time {
  font-size: 13px;
  color: #667eea;
  font-weight: 500;
  white-space: nowrap;
}

.collect-btn {
  padding: 7px 25px 7px 15px;
  width: 86px;
  background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
  color: white;
  border: none;
  border-radius: 6px;
  font-size: 14px;
  font-weight: 600;
  cursor: pointer;
  transition: all 0.3s;
  white-space: nowrap;
  text-align: center;
}

.collect-btn:hover:not(:disabled) {
  transform: translateY(-1px);
  box-shadow: 0 2px 8px rgba(102, 126, 234, 0.4);
}

.collect-btn:disabled {
  background: #f0eef8 !important;
  color: #9b8ff8 !important;
  border: 1px solid #e0ddf0 !important;
  cursor: not-allowed;
}

.collect-btn.collecting {
  background: linear-gradient(135deg, #f44336 0%, #e91e63 100%);
  animation: pulse 1.5s ease-in-out infinite;
}

@keyframes pulse {
  0%, 100% { opacity: 1; }
  50% { opacity: 0.7; }
}

.tabs-bar-empty {
  color: rgba(0, 0, 0, 0.5);
  font-size: 14px;
  font-style: italic;
}

.tabs-bar::-webkit-scrollbar {
  height: 6px;
}

.tabs-bar::-webkit-scrollbar-track {
  background: rgba(255, 255, 255, 0.1);
}

.tabs-bar::-webkit-scrollbar-thumb {
  background: rgba(255, 255, 255, 0.3);
  border-radius: 3px;
}

.tab {
  padding: 8px 20px;
  background: rgba(0, 0, 0, 0.05);
  color: rgba(0, 0, 0, 0.7);
  border: none;
  border-radius: 6px;
  font-size: 14px;
  cursor: move;
  transition: all 0.3s;
  white-space: nowrap;
  display: flex;
  align-items: center;
  gap: 8px;
  user-select: none;
}

.tab.dragging {
  opacity: 0.5;
}

.tab.drag-over {
  border-left: 3px solid #6B5DD3;
}

.tab:hover {
  background: rgba(0, 0, 0, 0.1);
  color: #333;
}

.tab.active {
  background: linear-gradient(135deg, #6B5DD3 0%, #8B7FE8 100%);
  color: white;
}

.tab .status-icon {
  font-size: 12px;
  display: inline-block;
}

.tab .close-btn {
  margin-left: 8px;
  font-size: 16px;
  opacity: 0.7;
  transition: opacity 0.3s;
  cursor: pointer;
}

.tab .close-btn:hover {
  opacity: 1;
}

/* 监控面板 */
.monitor-container {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 20px;
  flex: 1;
  min-height: 0;
}

.monitor-panel {
  background: white;
  border-radius: 12px;
  padding: 20px;
  box-shadow: 0 4px 6px rgba(0, 0, 0, 0.1);
  display: flex;
  flex-direction: column;
  overflow: hidden;
  flex: 1;
  min-height: 0;
}

.panel-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 15px;
  padding-bottom: 10px;
  border-bottom: 2px solid #f0f0f0;
}

.panel-title {
  color: #333;
  font-size: 18px;
  font-weight: 600;
  display: flex;
  align-items: center;
  gap: 10px;
}

.panel-icon {
  font-size: 24px;
}

.panel-actions {
  display: flex;
  gap: 8px;
}

.panel-btn {
  padding: 6px 12px;
  background: rgba(102, 126, 234, 0.1);
  color: #667eea;
  border: 1px solid rgba(102, 126, 234, 0.2);
  border-radius: 6px;
  font-size: 12px;
  cursor: pointer;
  transition: all 0.3s;
}

.panel-btn:hover {
  background: rgba(102, 126, 234, 0.2);
}

.checkbox-label {
  display: flex;
  align-items: center;
  gap: 5px;
  color: #667eea;
  font-size: 12px;
  cursor: pointer;
}

.checkbox-label input {
  cursor: pointer;
}

.panel-content {
  flex: 1;
  overflow: auto;
  color: #333;
  font-size: 14px;
  line-height: 1.6;
}

.panel-content::-webkit-scrollbar {
  width: 6px;
}

.panel-content::-webkit-scrollbar-track {
  background: rgba(0, 0, 0, 0.05);
}

.panel-content::-webkit-scrollbar-thumb {
  background: rgba(0, 0, 0, 0.2);
  border-radius: 3px;
}

.placeholder {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  height: 100%;
  color: rgba(0, 0, 0, 0.4);
  text-align: center;
}

.placeholder-icon {
  font-size: 48px;
  margin-bottom: 15px;
  opacity: 0.5;
}

/* 通知 */
.notification {
  position: fixed;
  top: 80px;
  right: 20px;
  padding: 15px 20px;
  border-radius: 8px;
  color: white;
  font-size: 14px;
  box-shadow: 0 4px 12px rgba(0, 0, 0, 0.3);
  z-index: 1000;
}

.notification.success {
  background: linear-gradient(135deg, #4CAF50 0%, #45a049 100%);
}

.notification.error {
  background: linear-gradient(135deg, #f44336 0%, #da190b 100%);
}

.notification.info {
  background: linear-gradient(135deg, #2196F3 0%, #0b7dda 100%);
}

.slide-enter-active,
.slide-leave-active {
  transition: all 0.3s ease;
}

.slide-enter-from,
.slide-leave-to {
  transform: translateX(400px);
  opacity: 0;
}

/* 对话框 */
.dialog-overlay {
  position: fixed;
  top: 0;
  left: 0;
  right: 0;
  bottom: 0;
  background: rgba(0, 0, 0, 0.5);
  z-index: 9999;
  display: flex;
  align-items: center;
  justify-content: center;
}

.dialog {
  background: white;
  padding: 30px;
  border-radius: 12px;
  box-shadow: 0 10px 40px rgba(0, 0, 0, 0.3);
  min-width: 400px;
}

.dialog h3 {
  margin: 0 0 20px 0;
  color: #333;
}

.dialog p {
  color: #666;
  margin-bottom: 20px;
}

.form-group {
  margin-bottom: 15px;
}

.form-group label {
  display: block;
  margin-bottom: 5px;
  color: #666;
  font-size: 14px;
}

.form-group input {
  width: 100%;
  padding: 8px;
  border: 1px solid #ddd;
  border-radius: 6px;
  font-size: 14px;
}

.dialog-actions {
  display: flex;
  gap: 10px;
  margin-top: 20px;
}

@media (max-width: 1200px) {
  .monitor-container {
    grid-template-columns: 1fr;
    grid-template-rows: repeat(2, 1fr);
  }
}

@media (max-width: 768px) {
  .connection-bar {
    flex-wrap: wrap;
  }

  .connection-bar input[type="text"],
  .connection-bar input[type="text"] {
    width: 140px;
  }
}
</style>
