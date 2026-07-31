<template>
  <div class="home">
    <!-- 工具分类网格 -->
    <div class="home-content">
      <div class="tools-grid">
      <!-- 第一列：测试可视化工具 -->
      <div class="category">
        <div class="category-header">
          <div class="category-icon">📊</div>
          <div class="category-title">测试工具</div>
        </div>
        <ul class="tool-list">
          <li class="tool-item">
            <router-link
              to="/perf/vdbench"
              :class="['tool-link', { disabled: !modulesStatus.vdbench }]"
              :event="modulesStatus.vdbench ? 'click' : ''"
            >
              <span class="tool-name">VDBench 可视化</span>
              <span class="tool-badge">{{ modulesStatus.vdbench ? '可用' : '维护中' }}</span>
            </router-link>
          </li>
          <li class="tool-item">
            <router-link
              to="/perf/fio"
              :class="['tool-link', { disabled: !modulesStatus.fio }]"
              :event="modulesStatus.fio ? 'click' : ''"
            >
              <span class="tool-name">FIO 测试</span>
              <span class="tool-badge">{{ modulesStatus.fio ? '可用' : '维护中' }}</span>
            </router-link>
          </li>
          <li class="tool-item">
            <router-link
              to="/perf/cosbench"
              :class="['tool-link', { disabled: !modulesStatus.cosbench }]"
              :event="modulesStatus.cosbench ? 'click' : ''"
            >
              <span class="tool-name">CosBench 配置文件生成</span>
              <span class="tool-badge">{{ modulesStatus.cosbench ? '可用' : '维护中' }}</span>
            </router-link>
          </li>
        </ul>
      </div>

      <!-- 第二列：网络工具 -->
      <div class="category">
        <div class="category-header">
          <div class="category-icon">🌐</div>
          <div class="category-title">网络工具</div>
        </div>
        <ul class="tool-list">
          <li class="tool-item">
            <router-link
              to="/network/ping"
              :class="['tool-link', { disabled: !modulesStatus.ping }]"
              :event="modulesStatus.ping ? 'click' : ''"
            >
              <span class="tool-name">网段扫描</span>
              <span class="tool-badge">{{ modulesStatus.ping ? '可用' : '维护中' }}</span>
            </router-link>
          </li>
          <li class="tool-item">
            <router-link
              to="/network/bond"
              :class="['tool-link', { disabled: !modulesStatus.bond }]"
              :event="modulesStatus.bond ? 'click' : ''"
            >
              <span class="tool-name">网络聚合配置</span>
              <span class="tool-badge">{{ modulesStatus.bond ? '可用' : '维护中' }}</span>
            </router-link>
          </li>
          <li class="tool-item">
            <router-link
              to="/network/iperf3"
              :class="['tool-link', { disabled: !modulesStatus.iperf3 }]"
              :event="modulesStatus.iperf3 ? 'click' : ''"
            >
              <span class="tool-name">带宽测试</span>
              <span class="tool-badge">{{ modulesStatus.iperf3 ? '可用' : '维护中' }}</span>
            </router-link>
          </li>
        </ul>
      </div>

      <!-- 第三列：系统工具 -->
      <div class="category">
        <div class="category-header">
          <div class="category-icon">🛠️</div>
          <div class="category-title">系统工具</div>
        </div>
        <ul class="tool-list">
          <li class="tool-item">
            <router-link
              to="/perf/monitor"
              :class="['tool-link', { disabled: !modulesStatus.monitor }]"
              :event="modulesStatus.monitor ? 'click' : ''"
            >
              <span class="tool-name">系统监控</span>
              <span class="tool-badge">{{ modulesStatus.monitor ? '可用' : '维护中' }}</span>
            </router-link>
          </li>
          <li class="tool-item">
            <router-link
              to="/system/ansible"
              :class="['tool-link', { disabled: !modulesStatus.ansible }]"
              :event="modulesStatus.ansible ? 'click' : ''"
            >
              <span class="tool-name">Ansible 管理平台</span>
              <span class="tool-badge">{{ modulesStatus.ansible ? '可用' : '维护中' }}</span>
            </router-link>
          </li>
          <li class="tool-item"><router-link to="/system/hardware-inspection" :class="['tool-link', { disabled: !modulesStatus.hardware_inspection }]" :event="modulesStatus.hardware_inspection ? 'click' : ''"><span class="tool-name">硬件巡检</span><span class="tool-badge">{{ modulesStatus.hardware_inspection ? '可用' : '维护中' }}</span></router-link></li>
          <li class="tool-item">
            <router-link
              to="/system/init"
              :class="['tool-link', { disabled: !modulesStatus.system_init }]"
              :event="modulesStatus.system_init ? 'click' : ''"
            >
              <span class="tool-name">系统初始化</span>
              <span class="tool-badge">{{ modulesStatus.system_init ? '可用' : '维护中' }}</span>
            </router-link>
          </li>
        </ul>
      </div>
      </div>

      <div class="version-badge">Version {{ appVersion }}</div>
    </div>
  </div>
</template>

<script setup>
import { MODULES_STATUS } from '@/config/modules.js'
import { reactive } from 'vue'
import pkg from '../../package.json'

const modulesStatus = reactive(MODULES_STATUS)
const appVersion = pkg.version
</script>

<style scoped>
.home {
  min-height: 100vh;
  padding: 40px 20px;
  position: relative;
  display: flex;
  align-items: center;
  justify-content: center;
}

.home-content {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 18px;
}

.version-badge {
  padding: 8px 14px;
  border-radius: 999px;
  background: rgba(255, 255, 255, 0.16);
  color: #fff;
  font-size: 13px;
  font-weight: 600;
  letter-spacing: 0.04em;
  box-shadow: 0 8px 20px rgba(0, 0, 0, 0.12);
  backdrop-filter: blur(8px);
}

.version-badge:hover {
  background: rgba(255, 255, 255, 0.22);
}

.home::before {
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

.home > * {
  position: relative;
  z-index: 1;
}

.tools-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(350px, 1fr));
  gap: 30px;
  max-width: 1400px;
  margin: 0 auto;
  justify-content: center;
  justify-items: center;
}

.category {
  background: white;
  border-radius: 16px;
  padding: 30px;
  box-shadow: 0 10px 30px rgba(0, 0, 0, 0.2);
  transition: transform 0.3s ease, box-shadow 0.3s ease;
  width: 100%;
  max-width: 400px;
}

.category:hover {
  transform: translateY(-5px);
  box-shadow: 0 15px 40px rgba(0, 0, 0, 0.3);
}

.category-header {
  display: flex;
  align-items: center;
  margin-bottom: 25px;
  padding-bottom: 15px;
  border-bottom: 2px solid #f0f0f0;
}

.category-icon {
  font-size: 36px;
  margin-right: 15px;
}

.category-title {
  font-size: 24px;
  font-weight: 600;
  color: #333;
}

.tool-list {
  list-style: none;
}

.tool-item {
  margin-bottom: 15px;
}

.tool-link {
  display: flex;
  align-items: center;
  padding: 15px 20px;
  background: linear-gradient(135deg, #6B5DD3 0%, #8B7FE8 100%);
  color: white;
  text-decoration: none;
  border-radius: 10px;
  transition: all 0.3s ease;
  font-size: 16px;
  font-weight: 500;
  box-shadow: 0 4px 10px rgba(107, 93, 211, 0.3);
}

.tool-link:hover {
  transform: translateX(5px);
  box-shadow: 0 6px 15px rgba(107, 93, 211, 0.5);
}

.tool-link.disabled {
  background: linear-gradient(135deg, #bbb 0%, #999 100%);
  cursor: not-allowed;
  opacity: 0.6;
}

.tool-link.disabled:hover {
  transform: none;
  box-shadow: 0 4px 10px rgba(0, 0, 0, 0.1);
}

.tool-icon {
  font-size: 24px;
  margin-right: 12px;
}

.tool-name {
  flex: 1;
}

.tool-badge {
  background: rgba(255, 255, 255, 0.3);
  padding: 4px 12px;
  border-radius: 20px;
  font-size: 12px;
  font-weight: 600;
}

@media (max-width: 768px) {
  .tools-grid {
    grid-template-columns: 1fr;
  }
}
</style>
