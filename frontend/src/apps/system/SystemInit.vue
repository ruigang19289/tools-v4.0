<template>
  <div class="init-page">
    <PageHeader
      icon="⚙️"
      title="系统初始化"
    />

    <!-- Main Content -->
    <div class="main-content">
      <!-- First Column: Host Config & Security -->
      <div class="left-panel">
        <!-- Host Config -->
        <div class="section">
          <h2 class="section-title">主机配置</h2>

          <div class="form-group">
            <label>远程主机:</label>
            <textarea
              v-model="hostsText"
              rows="3"
              placeholder="192.168.1.1&#10;192.168.1.1-10&#10;192.168.1.0/24"
            ></textarea>
            <small>支持单个IP、IP段(192.168.1.1-10)、CIDR(192.168.1.0/24)</small>
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
            <input type="password" v-model="password" placeholder="******">
          </div>
        </div>

        <!-- Security Config -->
        <div class="section">
          <h2 class="section-title">安全加固</h2>
          <div class="checkbox-grid">
            <label class="checkbox-item">
              <input type="checkbox" v-model="config.hardenEtcd">
              <span>Etcd (2379, 2380)</span>
            </label>
            <label class="checkbox-item">
              <input type="checkbox" v-model="config.hardenPostgresql">
              <span>PostgreSQL (5432, 5433)</span>
            </label>
            <label class="checkbox-item">
              <input type="checkbox" v-model="config.hardenElasticsearch">
              <span>Elasticsearch (9200)</span>
            </label>
            <label class="checkbox-item">
              <input type="checkbox" v-model="config.hardenChronyd">
              <span>Chronyd (123)</span>
            </label>
          </div>
          <div class="form-group">
            <label>自定义封禁端口:</label>
            <input type="text" v-model="config.blockPorts" placeholder="例如: 80,443,8080">
            <small>多个端口用逗号分隔</small>
          </div>
          <div class="form-group">
            <label>管理网络 CIDR:</label>
            <input type="text" v-model="config.managementCidr" placeholder="10.255.0.0/24">
          </div>
        </div>
      </div>

      <!-- Second Column: Hostname & NTP & Actions -->
      <div class="middle-panel">
        <!-- Hostname Config -->
        <div class="section">
          <h2 class="section-title">主机名配置</h2>
          <div class="form-group">
            <label>主机名前缀:</label>
            <input
              type="text"
              v-model="hostnamePrefix"
              placeholder="node"
            >
            <small>基于IP从小到大排序自动生成: node01, node02, ...</small>
          </div>
        </div>

        <!-- NTP Config -->
        <div class="section">
          <h2 class="section-title">NTP 配置</h2>
          <div class="form-group">
            <label>NTP 服务器:</label>
            <textarea
              v-model="config.ntpServers"
              rows="2"
              placeholder="ntp.aliyun.com&#10;ntp1.aliyun.com"
            ></textarea>
            <small>每行一个服务器，第一个为首选</small>
          </div>
        </div>

        <!-- Actions -->
        <div class="section actions-section">
          <button
            class="btn btn-primary btn-full"
            @click="executeFullInit"
            :disabled="!canStart || isRunning"
          >
            {{ isRunning ? '执行中...' : '全部初始化（不含安全加固）' }}
          </button>

          <div class="quick-actions">
            <button class="btn btn-secondary" @click="modifyHostnames" :disabled="!canStartSingle">
              修改主机名
            </button>
            <button class="btn btn-secondary" @click="configureNtpSync" :disabled="!canStartSingle">
              时钟同步
            </button>
            <button class="btn btn-secondary" @click="configureSshPasswordless" :disabled="!canStartSingle">
              SSH免密
            </button>
            <button class="btn btn-secondary" @click="configureFirewall" :disabled="!canStartSingle">
              防火墙
            </button>
            <button class="btn btn-secondary" @click="applySecurityHardening" :disabled="!canStartSingle">
              安全加固
            </button>
          </div>
        </div>
      </div>

      <!-- Third Column: Terminal Output -->
      <div class="right-panel">
        <!-- Terminal Output -->
        <div class="section terminal-section">
          <h2 class="section-title">终端输出</h2>
          <div class="terminal-window">
            <div v-if="logs.length === 0" class="terminal-empty">
              等待执行命令...
            </div>
            <div v-for="(log, idx) in logs" :key="idx" :class="['log-line', log.type]">
              <span class="time">[{{ log.time }}]</span>
              <span class="msg">{{ log.message }}</span>
            </div>
          </div>
        </div>

        <!-- Results -->
        <div v-if="results.length > 0" class="section results-section">
          <h2 class="section-title">执行结果</h2>
          <div class="results-list">
            <div v-for="(result, idx) in results" :key="idx" :class="['result-item', result.success ? 'success' : 'error']">
              {{ result.ip }}：{{ result.success ? result.taskName + '已完成' : '执行失败' }}
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
import { ref, reactive, computed, nextTick, watch } from 'vue'
import PageHeader from '@/components/common/PageHeader.vue'
import { usePageStatePersistence } from '@/apps/common/usePageStatePersistence'

const API_BASE = `/api/v1/system/system-init`

// Form data
const hostsText = ref('')
const port = ref(22)
const username = ref('root')
const password = ref('')
const authMethod = ref('password')
const hostnamePrefix = ref('node')

// Config
const config = reactive({
  ntpServers: 'ntp.aliyun.com',
  managementCidr: '',
  hardenEtcd: true,
  hardenPostgresql: true,
  hardenElasticsearch: true,
  hardenChronyd: false,
  blockPorts: ''
})

// State
const isRunning = ref(false)
const logs = ref([])
const results = ref([])

// Notification
const notification = reactive({
  show: false,
  message: '',
  type: 'info'
})

// Computed
const canStart = computed(() => {
  return hostsText.value.trim() && username.value.trim() && (authMethod.value === 'key' || password.value) && hostnamePrefix.value.trim()
})

const canStartSingle = computed(() => {
  return hostsText.value.trim() && username.value.trim() && (authMethod.value === 'key' || password.value)
})

// Methods
const parseIPRange = (input) => {
  const ips = []
  const lines = input.split('\n').map(l => l.trim()).filter(l => l)
  
  for (const line of lines) {
    // CIDR 格式: 192.168.1.0/24
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
    
    // IP范围格式: 192.168.1.1-192.168.1.10 或 192.168.1.1-10
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
    
    // 单个IP
    ips.push(line)
  }
  
  return ips
}

const getValidHosts = () => {
  const allIPs = parseIPRange(hostsText.value)
  return allIPs.map(ip => ({
    ip,
    username: username.value || 'root',
    auth_method: authMethod.value,
    password: password.value,
    port: port.value
  }))
}

const inferManagementCidr = () => {
  const allIPs = parseIPRange(hostsText.value)
  if (allIPs.length === 0) return ''

  const firstIp = allIPs[0]
  const parts = firstIp.split('.')
  if (parts.length !== 4) return ''

  return `${parts[0]}.${parts[1]}.${parts[2]}.0/24`
}

watch(hostsText, () => {
  const inferredCidr = inferManagementCidr()
  if (inferredCidr) {
    config.managementCidr = inferredCidr
  }
})

watch(() => config.managementCidr, (value) => {
  if (!value || !value.trim()) {
    const inferredCidr = inferManagementCidr()
    if (inferredCidr) {
      config.managementCidr = inferredCidr
    }
  }
})

const initialManagementCidr = inferManagementCidr()
if (initialManagementCidr) {
  config.managementCidr = initialManagementCidr
}

if (!config.managementCidr) {
  config.managementCidr = '10.255.0.0/24'
}



const showNotification = (message, type = 'info') => {
  notification.message = message
  notification.type = type
  notification.show = true
  setTimeout(() => {
    notification.show = false
  }, 3000)
}

const addLog = (message, type = 'info') => {
  const now = new Date()
  const time = now.toLocaleTimeString()
  logs.value.push({ time, message, type })
  nextTick(() => {
    const terminal = document.querySelector('.terminal-window')
    if (terminal) terminal.scrollTop = terminal.scrollHeight
  })
}

// Execute full init
const executeFullInit = async () => {
  const hosts = getValidHosts()

  await runTask('全部初始化（不含安全加固）', '/full-init', {
    hostname_prefix: hostnamePrefix.value,
    ntp_servers: config.ntpServers,
    management_cidr: config.managementCidr,
    harden_etcd: config.hardenEtcd,
    harden_postgresql: config.hardenPostgresql,
    harden_elasticsearch: config.hardenElasticsearch
  }, hosts)
}

// Single operations
const modifyHostnames = async () => {
  const hosts = getValidHosts()

  await runTask('修改主机名', '/modify-hostnames', {
    hostname_prefix: hostnamePrefix.value
  }, hosts)
}

const configureNtpSync = async () => {
  if (!config.ntpServers || !config.ntpServers.trim()) {
    showNotification('请输入 NTP 服务器', 'error')
    return
  }
  await runTask('时钟同步', '/configure-ntp', { ntp_servers: config.ntpServers })
}

const configureSshPasswordless = async () => {
  await runTask('SSH免密', '/configure-ssh', {})
}

const configureFirewall = async () => {
  await runTask('防火墙配置', '/configure-firewall', {})
}

const applySecurityHardening = async () => {
  if (!config.managementCidr) {
    showNotification('请输入管理网络 CIDR', 'error')
    return
  }

  // 生成 iptables 命令并显示在终端
  logs.value = []
  const iptablesCommands = [] // 收集所有 iptables 命令

  addLog('===== 安全加固 iptables 命令 =====', 'info')
  addLog(`管理网络 CIDR: ${config.managementCidr}`, 'info')
  addLog('', 'info')

  // Etcd 端口
  if (config.hardenEtcd) {
    addLog('# ===== Etcd (2379-2380) =====', 'info')
    generateIptablesCommands(config.managementCidr, [2379, 2380], 'tcp', iptablesCommands)
    addLog('', 'info')
  }

  // PostgreSQL 端口
  if (config.hardenPostgresql) {
    addLog('# ===== PostgreSQL (5432-5433) =====', 'info')
    generateIptablesCommands(config.managementCidr, [5432, 5433], 'tcp', iptablesCommands)
    addLog('', 'info')
  }

  // Elasticsearch 端口
  if (config.hardenElasticsearch) {
    addLog('# ===== Elasticsearch (9200-9300) =====', 'info')
    generateIptablesCommands(config.managementCidr, [9200, 9300], 'tcp', iptablesCommands)
    addLog('', 'info')
  }

  // Chronyd 端口
  if (config.hardenChronyd) {
    addLog('# ===== Chronyd (123) - UDP =====', 'info')
    generateIptablesCommands(config.managementCidr, [123], 'udp', iptablesCommands)
    const conntrackCmd = 'iptables -I INPUT -m conntrack --ctstate ESTABLISHED,RELATED -j ACCEPT'
    addLog(conntrackCmd, 'success')
    iptablesCommands.push(conntrackCmd)
    addLog('', 'info')
  }

  // 自定义封禁端口
  if (config.blockPorts && config.blockPorts.trim()) {
    const ports = config.blockPorts.split(',').map(p => parseInt(p.trim())).filter(p => !isNaN(p))
    if (ports.length > 0) {
      addLog(`# ===== 自定义封禁端口 (${config.blockPorts}) =====`, 'info')
      generateIptablesCommands(config.managementCidr, ports, 'tcp', iptablesCommands)
      addLog('', 'info')
    }
  }

  addLog('', 'info')
  addLog('命令已生成，正在执行...', 'info')

  // 直接调用后端 API，不使用 runTask 以保留命令输出
  const validHosts = getValidHosts()
  if (validHosts.length === 0) {
    showNotification('请添加至少一台主机', 'error')
    return
  }

  isRunning.value = true
  results.value = []

  try {
    const response = await fetch(`${API_BASE}/security-hardening`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        hosts: validHosts,
        management_cidr: config.managementCidr,
        harden_etcd: config.hardenEtcd,
        harden_postgresql: config.hardenPostgresql,
        harden_elasticsearch: config.hardenElasticsearch,
        harden_chronyd: config.hardenChronyd,
        block_ports: config.blockPorts,
        iptables_commands: iptablesCommands
      })
    })

    const data = await response.json()

    if (data.status === 'success') {
      addLog('', 'info')
      addLog('===== 执行结果 =====', 'info')
      results.value = data.results.map(r => ({
        ip: r.ip,
        success: r.success,
        taskName: '安全加固'
      }))
      data.results.forEach(r => {
        const status = r.success ? '安全加固已完成' : '执行失败'
        addLog(`${r.ip}：${status}`, r.success ? 'success' : 'error')
      })
      showNotification('安全加固成功', 'success')
    } else {
      addLog(`失败: ${data.error}`, 'error')
      showNotification('安全加固失败', 'error')
    }
  } catch (error) {
    addLog(`错误: ${error.message}`, 'error')
    showNotification(`执行错误: ${error.message}`, 'error')
  } finally {
    isRunning.value = false
  }
}

const generateIptablesCommands = (cidr, ports, protocol, commandsArray) => {
  ports.forEach(port => {
    addLog(`# 端口 ${port}`, 'info')

    const cmd1 = `iptables -I INPUT -s ${cidr} -p ${protocol} --dport ${port} -j ACCEPT`
    const cmd2 = `iptables -I INPUT -i lo -p ${protocol} --dport ${port} -j ACCEPT`
    const cmd3 = `iptables -A INPUT -p ${protocol} --dport ${port} -j DROP`

    addLog(cmd1, 'success')
    addLog(cmd2, 'success')
    addLog(cmd3, 'success')

    commandsArray.push(cmd1)
    commandsArray.push(cmd2)
    commandsArray.push(cmd3)
  })
}

// Run task
const runTask = async (taskName, endpoint, extraData, customHosts = null) => {
  const validHosts = customHosts || getValidHosts()
  if (validHosts.length === 0) {
    showNotification('请添加至少一台主机', 'error')
    return
  }

  isRunning.value = true
  results.value = []
  logs.value = []

  addLog(`开始${taskName}...`, 'info')
  addLog(`目标主机: ${validHosts.length} 台`, 'info')

  try {
    const response = await fetch(`${API_BASE}${endpoint}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        hosts: validHosts,
        ...extraData
      })
    })

    const data = await response.json()

    if (data.status === 'success') {
      // 简化的执行结果显示，添加任务名称
      results.value = data.results.map(r => ({
        ip: r.ip,
        success: r.success,
        taskName: taskName
      }))

      // 显示每个主机的详细日志
      addLog('', 'info')
      addLog('===== 执行详情 =====', 'info')
      data.results.forEach(r => {
        addLog(`\n[${r.hostname || r.ip}]`, 'info')
        if (r.logs && r.logs.length > 0) {
          r.logs.forEach(log => {
            if (log.includes('[OK]')) {
              addLog(log, 'success')
            } else if (log.includes('[WARNING]') || log.includes('[WARN]')) {
              addLog(log, 'warning')
            } else if (log.includes('[ERROR]') || log.includes('[FAIL]')) {
              addLog(log, 'error')
            } else {
              addLog(log, 'info')
            }
          })
        } else {
          addLog(r.message, r.success ? 'success' : 'error')
        }
      })

      addLog('', 'info')
      addLog(`${taskName}完成`, 'success')
      showNotification(`${taskName}成功`, 'success')
    } else {
      addLog(`失败: ${data.error}`, 'error')
      showNotification(`${taskName}失败`, 'error')
    }
  } catch (error) {
    addLog(`错误: ${error.message}`, 'error')
    showNotification(`执行错误: ${error.message}`, 'error')
  } finally {
    isRunning.value = false
  }
}

usePageStatePersistence('system_init_page_state', () => ({
  hostsText: hostsText.value,
  port: port.value,
  username: username.value,
  password: password.value,
  authMethod: authMethod.value,
  hostnamePrefix: hostnamePrefix.value,
  config: { ...config },
  logs: logs.value,
  results: results.value
}), {
  hydrate: (saved) => {
    hostsText.value = saved.hostsText || ''
    port.value = saved.port ?? 22
    username.value = saved.username || 'root'
    password.value = saved.password || ''
    authMethod.value = saved.authMethod || 'password'
    hostnamePrefix.value = saved.hostnamePrefix || 'node'
    Object.assign(config, {
      ntpServers: 'ntp.aliyun.com',
      managementCidr: '',
      hardenEtcd: true,
      hardenPostgresql: true,
      hardenElasticsearch: true,
      hardenChronyd: false,
      blockPorts: '',
      ...(saved.config || {})
    })
    logs.value = Array.isArray(saved.logs) ? saved.logs : []
    results.value = Array.isArray(saved.results) ? saved.results : []
    isRunning.value = false
  }
})
</script>

<style scoped>
.init-page {
  min-height: 100vh;
  padding: 20px;
  background: linear-gradient(135deg, #6B5DD3 0%, #8B7FE8 50%, #9B8FF8 100%);
  position: relative;
}

.init-page::before {
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

.init-page > * {
  position: relative;
  z-index: 1;
}

.main-content {
  display: grid;
  grid-template-columns: 320px 320px 1fr;
  gap: 20px;
}

.left-panel,
.middle-panel,
.right-panel {
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

.terminal-section .section-title,
.results-section .section-title {
  border-bottom: none;
}

/* Form */
.form-group {
  margin-bottom: 12px;
}

.form-group label {
  display: block;
  font-size: 12px;
  color: #666;
  margin-bottom: 4px;
}

.form-group input,
.form-group textarea {
  width: 100%;
  padding: 8px 12px;
  border: 1px solid #ddd;
  border-radius: 6px;
  font-size: 14px;
  font-family: monospace;
}

.form-group textarea {
  min-height: 60px;
  resize: vertical;
}

.form-group small {
  font-size: 11px;
  color: #999;
  margin-top: 4px;
  display: block;
}

.form-row {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 10px;
}

/* Checkbox */
.checkbox-grid {
  display: flex;
  flex-direction: column;
  gap: 10px;
  margin-bottom: 12px;
}

.checkbox-item {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 13px;
  cursor: pointer;
}

.checkbox-item input {
  width: 16px;
  height: 16px;
}

/* Buttons */
.btn {
  padding: 10px 20px;
  border: none;
  border-radius: 6px;
  font-size: 14px;
  font-weight: 600;
  cursor: pointer;
  transition: all 0.2s;
}

.btn-full {
  width: 100%;
}

.btn-primary {
  background: linear-gradient(135deg, #6B5DD3 0%, #8B7FE8 100%);
  color: white;
}

.btn-primary:hover:not(:disabled) {
  transform: translateY(-2px);
  box-shadow: 0 5px 15px rgba(107, 93, 211, 0.4);
}

.btn-primary:disabled {
  opacity: 0.6;
  cursor: not-allowed;
}

.btn-secondary {
  background: #e0e0e0;
  color: #333;
}

.btn-secondary:hover:not(:disabled) {
  background: #d0d0d0;
}

.btn-secondary:disabled {
  opacity: 0.6;
  cursor: not-allowed;
}

.quick-actions {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin-top: 10px;
}

.actions-section {
  display: flex;
  flex-direction: column;
  gap: 10px;
}

/* Terminal */
.terminal-section {
  display: flex;
  flex-direction: column;
  height: 430px;
}

.terminal-window {
  background: #1e1e1e;
  border-radius: 8px;
  padding: 15px;
  flex: 1;
  overflow-y: auto;
  font-family: 'Courier New', monospace;
  font-size: 13px;
  height: 100%;
}

.terminal-empty {
  color: #666;
  text-align: center;
  padding: 20px;
}

.log-line {
  padding: 4px 0;
  line-height: 1.6;
}

.log-line .time {
  color: #666;
  margin-right: 8px;
}

.log-line.info .msg {
  color: #2196F3;
}

.log-line.success .msg {
  color: #4CAF50;
}

.log-line.error .msg {
  color: #f44336;
}

.log-line.warning .msg {
  color: #ff9800;
}

/* Results */
.results-section {
  display: flex;
  flex-direction: column;
  height: 200px;
}

.results-list {
  flex: 1;
  overflow-y: auto;
  background: #1e1e1e;
  border-radius: 8px;
  padding: 15px;
  font-family: 'Courier New', monospace;
  font-size: 13px;
}

.result-item {
  padding: 4px 0;
  line-height: 1.6;
}

.result-item.success {
  color: #4CAF50;
}

.result-item.error {
  color: #f44336;
}

/* Notification */
.notification {
  position: fixed;
  top: 80px;
  right: 20px;
  padding: 12px 20px;
  border-radius: 8px;
  color: white;
  font-size: 14px;
  box-shadow: 0 4px 12px rgba(0, 0, 0, 0.3);
  z-index: 1000;
}

.notification.success {
  background: linear-gradient(135deg, #27ae60 0%, #2ecc71 100%);
}

.notification.error {
  background: linear-gradient(135deg, #c0392b 0%, #e74c3c 100%);
}

@media (max-width: 1024px) {
  .main-content {
    grid-template-columns: 1fr;
  }
}
</style>
