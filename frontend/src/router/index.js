import { createRouter, createWebHistory } from 'vue-router'

const routes = [
  {
    path: '/',
    name: 'Home',
    component: () => import('@/views/Home.vue')
  },
  // Performance Tools
  {
    path: '/perf/vdbench',
    name: 'VDBench',
    component: () => import('@/apps/performance/VDBench.vue')
  },
  {
    path: '/perf/monitor',
    name: 'Monitor',
    component: () => import('@/apps/performance/Monitor.vue')
  },
  {
    path: '/perf/fio',
    name: 'FIOTest',
    component: () => import('@/apps/performance/FIOTest.vue')
  },
  {
    path: '/perf/cosbench',
    name: 'CosBenchGenerator',
    component: () => import('@/apps/performance/CosBenchGenerator.vue')
  },
  // Network Tools
  {
    path: '/network/ping',
    name: 'PingScan',
    component: () => import('@/apps/network/PingScan.vue')
  },
  {
    path: '/network/bond',
    name: 'BondConfig',
    component: () => import('@/apps/network/BondConfig.vue')
  },
  {
    path: '/network/iperf3',
    name: 'NetworkReliabilityTest',
    component: () => import('@/apps/network/NetworkReliabilityTest_new.vue')
  },
  // System Tools
  {
    path: '/system/ansible',
    name: 'Ansible',
    component: () => import('@/apps/system/Ansible.vue')
  },
  {
    path: '/system/hardware-inspection',
    name: 'HardwareInspection',
    component: () => import('@/apps/system/HardwareInspection.vue')
  },
  {
    path: '/system/init',
    name: 'SystemInit',
    component: () => import('@/apps/system/SystemInit.vue')
  },
  {
    path: '/system/node-precheck',
    name: 'NodePrecheck',
    component: () => import('@/apps/system/NodePrecheck.vue')
  },
]

const router = createRouter({
  history: createWebHistory(),
  routes
})

export default router
