<template>
  <div class="ansible-page">
    <PageHeader
      icon="📡"
      title="Ansible 管理平台"
    />

    <div class="main-content">
      <!-- 左侧：主机配置 -->
      <div class="left-panel">
        <div class="section">
          <h2 class="section-title">主机配置</h2>
          <div class="form-group">
            <label>主机列表 (每行一个IP或范围):</label>
            <textarea
              v-model="hostsText"
              rows="4"
              placeholder="192.168.1.1&#10;192.168.1.2&#10;192.168.1.10-20"
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
            <select v-model="authMethod" class="form-select">
              <option value="password">密码认证</option>
              <option value="ssh_key">SSH 私钥认证（容器挂载）</option>
            </select>
          </div>
          <div v-if="authMethod === 'password'" class="form-group">
            <label>密码:</label>
            <input type="password" v-model="password" placeholder="******">
          </div>
          <div v-else class="auth-tip">
            使用容器内挂载的私钥。路径由服务端 <code>TOOLS_ANSIBLE_PRIVATE_KEY_PATH</code> 配置，网页不会接收或保存私钥。
          </div>
          <button class="btn btn-primary btn-full" @click="validateHosts" :disabled="!canValidate">
            验证连接
          </button>
        </div>

        <!-- 已验证主机列表 -->
        <div class="section" v-if="validatedHosts.length > 0">
          <h2 class="section-title">已验证主机 ({{ validatedHosts.length }})</h2>
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
          <div class="btn-group">
            <button class="btn btn-secondary btn-compact" @click="selectAll">全选</button>
            <button class="btn btn-secondary btn-compact" @click="clearSelection">清空</button>
          </div>
        </div>
      </div>

      <!-- 右侧：功能面板 -->
      <div class="right-panel">
        <!-- 功能标签页 -->
        <div class="tabs">
          <button
            :class="['tab-btn', { active: activeTab === 'command' }]"
            @click="activeTab = 'command'"
          >
            命令执行
          </button>
          <button
            :class="['tab-btn', { active: activeTab === 'file' }]"
            @click="activeTab = 'file'"
          >
            文件分发
          </button>
          <button
            :class="['tab-btn', { active: activeTab === 'playbook' }]"
            @click="activeTab = 'playbook'"
          >
            Playbook
          </button>
          <button
            :class="['tab-btn', { active: activeTab === 'modules' }]"
            @click="activeTab = 'modules'"
          >
            模块
          </button>
        </div>

        <!-- 命令执行面板 -->
        <div v-if="activeTab === 'command'" class="tab-content">
          <div class="section">
            <h2 class="section-title">批量命令执行</h2>
            <div class="form-group">
              <label>选择模块:</label>
              <select v-model="commandModule" class="form-select">
                <option value="shell">Shell 命令</option>
                <option value="command">Command 命令</option>
                <option value="ping">Ping 测试</option>
                <option value="setup">收集主机信息</option>
                <option value="yum">Yum 包管理</option>
                <option value="apt">Apt 包管理</option>
                <option value="copy">Copy 文件复制</option>
                <option value="file">File 文件管理</option>
                <option value="service">Service 服务管理</option>
                <option value="systemd">Systemd 系统服务</option>
                <option value="selinux">SELinux 管理</option>
                <option value="firewalld">Firewalld 防火墙</option>
                <option value="template">Template 模板文件</option>
                <option value="sync">Sync 目录同步</option>
                <option value="cron">Cron 定时任务</option>
              </select>
            </div>
            <div class="form-group">
              <label>命令:</label>
              <textarea
                v-model="command"
                rows="4"
                placeholder="输入要执行的命令..."
              ></textarea>
            </div>
            <button
              class="btn btn-primary"
              @click="executeCommand"
              :disabled="!canExecute || executing"
            >
              {{ executing ? '执行中...' : '执行命令' }}
            </button>
          </div>
        </div>

        <!-- 文件分发面板 -->
        <div v-if="activeTab === 'file'" class="tab-content">
          <div class="section">
            <h2 class="section-title">文件分发</h2>
            <div class="form-group">
              <label>分发方式:</label>
              <select v-model="fileAction" class="form-select">
                <option value="push">推送文件</option>
                <option value="pull">拉取文件</option>
              </select>
            </div>
            <div class="form-group">
              <label>源路径:</label>
              <input type="text" v-model="sourcePath" :placeholder="fileAction === 'push' ? '/path/to/local/file' : '/path/on/remote'">
            </div>
            <div class="form-group">
              <label>目标路径:</label>
              <input type="text" v-model="destPath" placeholder="/path/on/remote">
            </div>
            <div class="form-group">
              <label>
                <input type="checkbox" v-model="fileBackup"> 目标文件备份
              </label>
            </div>
            <button
              class="btn btn-primary"
              @click="transferFile"
              :disabled="!canTransfer || transferring"
            >
              {{ transferring ? '传输中...' : '开始传输' }}
            </button>
          </div>
        </div>

        <!-- Playbook面板 -->
        <div v-if="activeTab === 'playbook'" class="tab-content">
          <div class="section">
            <h2 class="section-title">Playbook 执行</h2>
            <div class="form-group">
              <label>Playbook 内容 (YAML):</label>
              <textarea
                v-model="playbookContent"
                rows="15"
                placeholder="- hosts: all&#10;  tasks:&#10;    - name: Ensure nginx is installed&#10;      yum:&#10;        name: nginx&#10;        state: present"
                class="code-editor"
              ></textarea>
            </div>
            <div class="btn-group">
              <button
                class="btn btn-primary"
                @click="runPlaybook"
                :disabled="!canRunPlaybook || runningPlaybook"
              >
                {{ runningPlaybook ? '执行中...' : '执行 Playbook' }}
              </button>
              <button class="btn btn-secondary" @click="validatePlaybook">
                验证语法
              </button>
            </div>
          </div>
        </div>

        <!-- 模块面板 -->
        <div v-if="activeTab === 'modules'" class="tab-content">
          <div class="section">
            <h2 class="section-title">常用模块</h2>
            <div class="modules-grid">
              <button
                v-for="mod in commonModules"
                :key="mod.name"
                class="module-btn"
                @click="useModule(mod)"
              >
                <span class="module-name">{{ mod.name }}</span>
                <span class="module-desc">{{ mod.desc }}</span>
              </button>
            </div>
          </div>
        </div>

        <!-- 执行结果 -->
        <div class="section terminal-section">
          <div class="terminal-header">
            <h2 class="section-title">执行结果</h2>
            <div class="terminal-controls">
              <button class="btn btn-compact" @click="clearOutput">清空</button>
              <button class="btn btn-compact" @click="downloadOutput">下载</button>
            </div>
          </div>
          <div class="terminal-window" ref="terminalWindow">
            <div v-for="(line, idx) in output" :key="idx" class="log-line">
              <span class="time">{{ line.time }}</span>
              <span :class="['message', line.type]">{{ line.message }}</span>
            </div>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, reactive, computed, nextTick, watch } from 'vue'
import PageHeader from '@/components/common/PageHeader.vue'
import { usePageStatePersistence } from '@/apps/common/usePageStatePersistence'

const API_BASE = '/api/v1/system/ansible'

// Form data
const hostsText = ref('')
const username = ref('root')
const port = ref(22)
const password = ref('')
const authMethod = ref('password')

// State
const validatedHosts = ref([])
const selectedHosts = ref([])
const activeTab = ref('command')
const executing = ref(false)
const transferring = ref(false)
const runningPlaybook = ref(false)
const output = ref([])
const terminalWindow = ref(null)

// Command tab
const commandModule = ref('shell')
const command = ref('')

// 命令模板
const templates = {
  shell: '# 输入要执行的 Shell 命令\n',
  command: '# 输入要执行的命令\n',
  yum: 'name=nginx state=present',
  apt: 'name=nginx state=present',
  copy: 'src=/path/to/local/file dest=/path/on/remote',
  file: 'path=/path/to/file mode=0644 state=file',
  service: 'name=nginx state=started',
  systemd: 'name=nginx state=started',
  selinux: 'state=disabled',
  firewalld: 'service=httpd state=enabled',
  template: 'src=template.j2 dest=/path/to/file',
  sync: 'src=/path/to/dest dest=/path/on/remote/',
  cron: 'name=backup minute="0" hour="2" job=/path/to/backup.sh'
}

// 监听模块变化，自动填充命令模板
watch(commandModule, (newModule) => {
  // 如果选择 yum 模块，检查系统是否支持
  if (newModule === 'yum') {
    // 在实际应用中，我们可以通过检测系统信息来判断是否支持 yum
    // 这里我们简单地显示一个警告信息
    addOutput('警告：yum 模块仅适用于 RedHat/CentOS 系统，Ubuntu 系统请使用 apt 模块', 'warning')
  }
  
  command.value = templates[newModule] || ''
})

// File transfer tab
const fileAction = ref('push')
const sourcePath = ref('')
const destPath = ref('')
const fileBackup = ref(true)

// Playbook tab
const playbookContent = ref(`# 测试用 Playbook 示例
- hosts: all
  gather_facts: false
  tasks:
    - name: 显示主机名
      shell: hostname
    
    - name: 显示系统信息
      shell: uname -a
    
    - name: 检查内存使用
      shell: free -h`)

// Common modules
const commonModules = [
  { name: 'shell', desc: '执行Shell命令' },
  { name: 'command', desc: '执行命令' },
  { name: 'yum', desc: '安装软件包' },
  { name: 'copy', desc: '复制文件' },
  { name: 'file', desc: '文件属性管理' },
  { name: 'service', desc: '服务管理' },
  { name: 'systemd', desc: '系统服务管理' },
  { name: 'selinux', desc: 'SELinux管理' },
  { name: 'firewalld', desc: '防火墙管理' },
  { name: 'template', desc: '模板文件' },
  { name: 'sync', desc: '目录同步' },
  { name: 'cron', desc: '定时任务' },
]

// 使用模块
const useModule = (mod) => {
  activeTab.value = 'command'
  commandModule.value = mod.name
  
  const templates = {
    shell: '# 输入要执行的 Shell 命令\n',
    command: '# 输入要执行的命令\n',
    yum: 'name: nginx\nstate: present',
    copy: 'src: /path/to/local/file\ndest: /path/on/remote',
    file: 'path: /path/to/file\nmode: 0644\nstate: file',
    service: 'name: nginx\nstate: started',
    systemd: 'name: nginx\nstate: started',
    selinux: 'state: disabled',
    firewalld: 'service: httpd\nstate: enabled',
    template: 'src: template.j2\ndest: /path/to/file',
    sync: 'src: /path/to/dest\ndest: /path/on/remote/',
    cron: 'name: backup\nminute: "0"\nhour: "2"\njob: /path/to/backup.sh'
  }
  
  command.value = templates[mod.name] || ''
}

// Computed
const canValidate = computed(() => {
  return hostsText.value.trim() && username.value.trim() && (authMethod.value === 'ssh_key' || password.value)
})

const canExecute = computed(() => {
  // ping 和 setup 模块不需要输入命令
  if (commandModule.value === 'ping' || commandModule.value === 'setup') {
    return selectedHosts.value.length > 0
  }
  return selectedHosts.value.length > 0 && command.value.trim()
})

const canTransfer = computed(() => {
  return selectedHosts.value.length > 0 && sourcePath.value.trim() && destPath.value.trim()
})

const canRunPlaybook = computed(() => {
  return selectedHosts.value.length > 0 && playbookContent.value.trim()
})

// 验证 Playbook 语法
const validatePlaybook = () => {
  const content = playbookContent.value.trim()
  if (!content) {
    addOutput('请输入 Playbook 内容', 'error')
    return
  }
  
  const lines = content.split('\n')
  let hasHosts = false
  let hasTasks = false
  let taskCount = 0
  
  for (const line of lines) {
    const stripped = line.trim()
    if (stripped.includes('hosts:')) hasHosts = true
    if (stripped.includes('tasks:')) hasTasks = true
    if (stripped.startsWith('- name:')) taskCount++
  }
  
  const issues = []
  if (!hasHosts) issues.push('缺少 hosts 配置')
  if (!hasTasks) issues.push('缺少 tasks 定义')
  if (taskCount === 0) issues.push('没有定义任何任务')
  
  if (issues.length === 0) {
    addOutput(`✓ Playbook 语法验证通过 (${taskCount} 个任务)`, 'success')
  } else {
    addOutput('✗ Playbook 语法问题:', 'error')
    issues.forEach(issue => addOutput(`  - ${issue}`, 'error'))
  }
}

// Methods
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

const addOutput = (message, type = 'info') => {
  const now = new Date()
  const time = now.toLocaleTimeString()
  output.value.push({ time, message, type })
  nextTick(() => {
    if (terminalWindow.value) {
      terminalWindow.value.scrollTop = terminalWindow.value.scrollHeight
    }
  })
}

const parseJsonResponse = async (response) => {
  const text = await response.text()
  if (!text.trim()) {
    throw new Error(`接口返回空响应，HTTP ${response.status}`)
  }

  let data
  try {
    data = JSON.parse(text)
  } catch (e) {
    throw new Error(`接口返回非 JSON，HTTP ${response.status}: ${text.slice(0, 200)}`)
  }

  if (!response.ok) {
    throw new Error(data.error || data.message || `请求失败，HTTP ${response.status}`)
  }

  return data
}

const validateHosts = async () => {
  const ips = parseIPRange(hostsText.value)
  addOutput(`开始验证 ${ips.length} 个主机...`, 'info')
  
  validatedHosts.value = []
  
  for (const ip of ips) {
    try {
      const response = await fetch(`${API_BASE}/validate-hosts`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          hosts: [{
            ip,
            username: username.value,
            auth_method: authMethod.value,
            password: authMethod.value === 'password' ? password.value : '',
            port: port.value
          }]
        })
      })
      const data = await parseJsonResponse(response)
      
      validatedHosts.value.push({
        ip,
        status: data.results?.[0]?.status === 'success' ? 'success' : 'error'
      })
      
      addOutput(`${ip}: ${data.results?.[0]?.status === 'success' ? '连接成功' : data.results?.[0]?.message || '连接失败'}`, 
        data.results?.[0]?.status === 'success' ? 'success' : 'error')
    } catch (e) {
      validatedHosts.value.push({ ip, status: 'error' })
      addOutput(`${ip}: 验证失败 - ${e.message}`, 'error')
    }
  }
  
  // 验证完成后默认全选所有主机
  selectAll()
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
  selectedHosts.value = validatedHosts.value.map(h => h.ip)
}

const clearSelection = () => {
  selectedHosts.value = []
}

const executeCommand = async () => {
  executing.value = true
  addOutput(`[${commandModule.value}] 在 ${selectedHosts.value.length} 台主机执行命令...`, 'info')
  addOutput(`命令: ${command.value}`, 'info')
  
  try {
    const hostsWithCreds = selectedHosts.value.map(ip => ({
      ip,
      username: username.value,
      auth_method: authMethod.value,
      password: authMethod.value === 'password' ? password.value : '',
      port: port.value
    }))
    
    const response = await fetch(`${API_BASE}/execute`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        hosts: hostsWithCreds,
        module: commandModule.value,
        command: command.value
      })
    })
    const data = await parseJsonResponse(response)
    
    if (data.results) {
      for (const r of data.results) {
        addOutput(`\n=== ${r.ip} ===`, 'info')
        addOutput(r.output || r.error || '无输出', r.success ? 'success' : 'error')
      }
    }
    
    addOutput('\n命令执行完成', 'success')
  } catch (e) {
    addOutput(`执行失败: ${e.message}`, 'error')
  } finally {
    executing.value = false
  }
}

const transferFile = async () => {
  transferring.value = true
  addOutput(`开始文件传输到 ${selectedHosts.value.length} 台主机...`, 'info')
  
  try {
    const hostsWithCreds = selectedHosts.value.map(ip => ({
      ip,
      username: username.value,
      auth_method: authMethod.value,
      password: authMethod.value === 'password' ? password.value : '',
      port: port.value
    }))
    
    const response = await fetch(`${API_BASE}/file-transfer`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        hosts: hostsWithCreds,
        action: fileAction.value,
        source: sourcePath.value,
        dest: destPath.value,
        backup: fileBackup.value
      })
    })
    const data = await parseJsonResponse(response)
    
    if (data.results) {
      for (const r of data.results) {
        addOutput(`${r.ip}: ${r.success ? '传输成功' : r.error}`, r.success ? 'success' : 'error')
      }
    }
  } catch (e) {
    addOutput(`传输失败: ${e.message}`, 'error')
  } finally {
    transferring.value = false
  }
}

const runPlaybook = async () => {
  runningPlaybook.value = true
  addOutput('开始执行 Playbook...', 'info')

  try {
    const hostsWithCreds = selectedHosts.value.map(ip => ({
      ip,
      username: username.value,
      auth_method: authMethod.value,
      password: authMethod.value === 'password' ? password.value : '',
      port: port.value
    }))

    const response = await fetch(`${API_BASE}/playbook`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        hosts: hostsWithCreds,
        playbook: playbookContent.value
      })
    })
    const data = await parseJsonResponse(response)

    // 显示每个主机的执行结果
    if (data.results) {
      for (const r of data.results) {
        addOutput(`\n=== ${r.ip} ===`, 'info')
        if (r.error) {
          addOutput(`错误: ${r.error}`, 'error')
        } else if (r.output) {
          addOutput(r.output, r.success ? 'success' : 'error')
        }
        addOutput(r.success ? '主机执行成功' : '主机执行失败', r.success ? 'success' : 'error')
      }
    } else {
      addOutput(data.output || 'Playbook 执行完成', 'success')
    }
  } catch (e) {
    addOutput(`执行失败: ${e.message}`, 'error')
  } finally {
    runningPlaybook.value = false
  }
}

const clearOutput = () => {
  output.value = []
}

const downloadOutput = () => {
  if (output.value.length === 0) {
    addOutput('没有可下载的输出', 'error')
    return
  }

  const content = output.value
    .map(line => `${line.time} ${line.message}`)
    .join('\n')
  const blob = new Blob([content], { type: 'text/plain;charset=utf-8' })
  const url = URL.createObjectURL(blob)
  const link = document.createElement('a')
  link.href = url
  link.download = `ansible_output_${new Date().toISOString().replace(/[:.]/g, '-')}.txt`
  document.body.appendChild(link)
  link.click()
  document.body.removeChild(link)
  URL.revokeObjectURL(url)
}

usePageStatePersistence('ansible_page_state', () => ({
  hostsText: hostsText.value,
  username: username.value,
  port: port.value,
  password: password.value,
  authMethod: authMethod.value,
  validatedHosts: validatedHosts.value,
  selectedHosts: selectedHosts.value,
  activeTab: activeTab.value,
  commandModule: commandModule.value,
  command: command.value,
  fileAction: fileAction.value,
  sourcePath: sourcePath.value,
  destPath: destPath.value,
  fileBackup: fileBackup.value,
  playbookContent: playbookContent.value,
  output: output.value
}), {
  hydrate: (saved) => {
    hostsText.value = saved.hostsText || ''
    username.value = saved.username || 'root'
    port.value = saved.port ?? 22
    password.value = saved.password || ''
    authMethod.value = saved.authMethod === 'ssh_key' ? 'ssh_key' : 'password'
    validatedHosts.value = Array.isArray(saved.validatedHosts) ? saved.validatedHosts : []
    selectedHosts.value = Array.isArray(saved.selectedHosts) ? saved.selectedHosts : []
    activeTab.value = saved.activeTab || 'command'
    commandModule.value = saved.commandModule || 'shell'
    command.value = saved.command || ''
    fileAction.value = saved.fileAction || 'push'
    sourcePath.value = saved.sourcePath || ''
    destPath.value = saved.destPath || ''
    fileBackup.value = saved.fileBackup ?? true
    playbookContent.value = saved.playbookContent || playbookContent.value
    output.value = Array.isArray(saved.output) ? saved.output : []
  }
})
</script>

<style scoped>
.ansible-page {
  min-height: 100vh;
  padding: 20px;
  background: linear-gradient(135deg, #6B5DD3 0%, #8B7FE8 50%, #9B8FF8 100%);
}

.main-content {
  display: grid;
  grid-template-columns: 320px 1fr;
  gap: 20px;
  margin-top: 20px;
}

.left-panel, .right-panel {
  background: white;
  border-radius: 12px;
  padding: 20px;
  box-shadow: 0 4px 6px rgba(0,0,0,0.1);
}

.section {
  margin-bottom: 20px;
}

.section-title {
  font-size: 16px;
  font-weight: 600;
  margin-bottom: 12px;
  color: #333;
}

.auth-tip {
  margin: -2px 0 12px;
  padding: 8px 10px;
  border-radius: 6px;
  background: #f3f1ff;
  color: #5748a8;
  font-size: 12px;
  line-height: 1.5;
}

.auth-tip code {
  word-break: break-all;
}

.form-group {
  margin-bottom: 12px;
}

.form-group label {
  display: block;
  font-size: 13px;
  color: #666;
  margin-bottom: 4px;
}

.form-group input[type="checkbox"] {
  width: auto;
  margin-right: 8px;
}

.form-group input,
.form-group textarea,
.form-group select {
  width: 100%;
  padding: 8px 12px;
  border: 1px solid #ddd;
  border-radius: 6px;
  font-size: 13px;
}

.form-group input:focus,
.form-group textarea:focus,
.form-group select:focus {
  outline: none;
  border-color: #6B5DD3;
}

.form-row {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 10px;
}

.host-list {
  max-height: 200px;
  overflow-y: auto;
  border: 1px solid #eee;
  border-radius: 6px;
  margin-bottom: 10px;
}

.host-item {
  display: flex;
  justify-content: space-between;
  padding: 8px 12px;
  cursor: pointer;
  border-bottom: 1px solid #eee;
}

.host-item:last-child {
  border-bottom: none;
}

.host-item.selected {
  background: #e8f5e9;
}

.host-status.success {
  color: #4CAF50;
}

.host-status.error {
  color: #f44336;
}

.tabs {
  display: flex;
  border-bottom: 2px solid #eee;
  margin-bottom: 20px;
}

.tab-btn {
  padding: 10px 20px;
  background: none;
  border: none;
  cursor: pointer;
  font-size: 14px;
  color: #666;
  border-bottom: 2px solid transparent;
  margin-bottom: -2px;
}

.tab-btn.active {
  color: #6B5DD3;
  border-bottom-color: #6B5DD3;
}

.code-editor {
  font-family: 'Courier New', monospace;
  font-size: 12px;
  background: #f5f5f5;
}

.modules-grid {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: 10px;
}

.module-btn {
  padding: 12px;
  background: #f5f5f5;
  border: 1px solid #ddd;
  border-radius: 6px;
  cursor: pointer;
  text-align: left;
}

.module-btn:hover {
  background: #f3e5f5;
  border-color: #6B5DD3;
}

.module-name {
  display: block;
  font-weight: 600;
  color: #333;
}

.module-desc {
  display: block;
  font-size: 11px;
  color: #999;
}

.terminal-section {
  margin-top: 20px;
}

.terminal-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 10px;
}

.terminal-controls {
  display: flex;
  gap: 10px;
}

.terminal-window {
  background: #1e1e1e;
  border-radius: 8px;
  padding: 15px;
  height: 300px;
  overflow-y: auto;
  font-family: 'Courier New', monospace;
  font-size: 12px;
}

.log-line {
  padding: 2px 0;
}

.log-line .time {
  color: #666;
  margin-right: 8px;
}

.log-line .message {
  color: #fff;
}

.log-line .message.success {
  color: #4CAF50;
}

.log-line .message.error {
  color: #f44336;
}

.btn-group {
  display: flex;
  gap: 10px;
  margin-top: 12px;
}
</style>
