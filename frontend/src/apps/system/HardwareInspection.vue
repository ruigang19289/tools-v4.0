<template>
  <div class="page"><PageHeader icon="🧰" title="硬件巡检" />
    <main><section><h2>主机与认证</h2><textarea v-model="hostsText" rows="5" placeholder="每行一个 IP，或 10.3.11.61-63"></textarea>
    <div class="row auth-row"><input v-model="username" placeholder="用户名"/><input v-model.number="port" type="number"/><select v-model="authMethod"><option value="password">密码</option><option value="key">服务器 SSH 密钥</option></select><input v-if="authMethod==='password'" v-model="password" type="password" placeholder="密码"/><div v-else class="key-tip">使用容器挂载的 SSH 私钥</div></div>
    <button @click="inspect" :disabled="running">{{ running ? '核对中...' : '核对硬件配置' }}</button></section>
    <section v-if="comparison.overview.length"><h2>巡检摘要</h2><ul><li v-for="line in comparison.overview" :key="line">{{line}}</li></ul></section>
    <section v-if="comparison.same.length"><h2>节点相同配置</h2><table><tr><th>项目</th><th>一致值</th></tr><tr v-for="item in comparison.same" :key="item.name"><td>{{item.name}}</td><td>{{item.value}}</td></tr></table></section>
    <section v-if="comparison.different.length"><h2>节点差异</h2><table><tr><th>项目</th><th v-for="host in resultHosts" :key="host">{{host}}</th></tr><tr v-for="item in comparison.different" :key="item.name"><td>{{item.name}}</td><td v-for="host in resultHosts" :key="host">{{item.values[host] || '未采集'}}</td></tr></table></section>
    <section v-if="comparison.nvme.length"><h2>NVMe PCIe 设备对照</h2><table><tr><th>设备</th><th v-for="host in resultHosts" :key="host">{{host}}</th><th>结论</th></tr><tr v-for="item in comparison.nvme" :key="item.name"><td>{{item.name}}</td><td v-for="host in resultHosts" :key="host" class="device-cell"><span v-for="line in item.values[host].split('\n')" :key="line" :class="{'diff-field': isDifferingField(line, item.differing_fields)}">{{line}}</span></td><td :class="item.same ? 'ok' : 'warn'">{{item.same ? '相同' : '差异'}}</td></tr></table></section>
    <section v-if="comparison.nics.length"><h2>物理网卡驱动/固件对照</h2><table><tr><th>接口</th><th v-for="host in resultHosts" :key="host">{{host}}</th><th>结论</th></tr><tr v-for="item in comparison.nics" :key="item.name"><td>{{item.name}}</td><td v-for="host in resultHosts" :key="host" class="device-cell"><span v-for="line in item.values[host].split('\n')" :key="line" :class="{'diff-field': isDifferingField(line, item.differing_fields)}">{{line}}</span></td><td :class="item.same ? 'ok' : 'warn'">{{item.same ? '相同' : '差异'}}</td></tr></table></section>
    <section v-if="comparison.bandwidth.length"><h2>PCIe 链路理论带宽对照</h2><p class="hint">格式：实际 PCIe 链路（速率/通道）/ 单向理论带宽；仅用于硬件链路核对，不等同于 fio 或 iperf3 实测带宽。</p><table><tr><th>设备</th><th v-for="host in resultHosts" :key="host">{{host}}</th><th>结论</th></tr><tr v-for="item in comparison.bandwidth" :key="item.name"><td>{{item.name}}</td><td v-for="host in resultHosts" :key="host">{{item.values[host]}}</td><td :class="item.same ? 'ok' : 'warn'">{{item.same ? '相同' : '差异'}}</td></tr></table></section>
    <section v-if="comparison.bandwidth.length" class="pcie-reference"><h2>PCIe 标准单向理论带宽参考</h2><p class="hint">有效负载理论值，采用 128b/130b 编码的 PCIe 3.0 及以上版本；不等同于应用实测吞吐。</p><table><tr><th>PCIe 版本</th><th>链路速率</th><th>x1</th><th>x4</th><th>x8</th><th>x16</th></tr><tr><td>PCIe 3.0</td><td>8 GT/s</td><td>0.98 GB/s</td><td>3.94 GB/s</td><td>7.88 GB/s</td><td>15.75 GB/s</td></tr><tr><td>PCIe 4.0</td><td>16 GT/s</td><td>1.97 GB/s</td><td>7.88 GB/s</td><td>15.75 GB/s</td><td>31.51 GB/s</td></tr><tr><td>PCIe 5.0</td><td>32 GT/s</td><td>3.94 GB/s</td><td>15.75 GB/s</td><td>31.51 GB/s</td><td>63.02 GB/s</td></tr></table></section>
    <section v-for="item in results" :key="item.host"><h2>{{item.host}} <small>{{item.status}}</small></h2><pre>{{item.report || item.error}}</pre></section>
  </main></div>
</template>
<script setup>
import { computed, ref } from 'vue'
import api from '@/api'
import PageHeader from '@/components/common/PageHeader.vue'
const hostsText=ref(''),username=ref('root'),port=ref(22),authMethod=ref('password'),password=ref(''),running=ref(false),results=ref([])
const resultHosts=computed(()=>results.value.filter(x=>x.status!=='error').map(x=>x.host))
const isDifferingField=(line, fields=[])=>fields.some(field=>line.startsWith(`${field}:`))
const comparison=ref({overview:[],same:[],different:[],nvme:[],nics:[],bandwidth:[]})
const hosts=computed(()=>{const out=[]; for(const item of hostsText.value.split(/[\n,]+/).map(x=>x.trim()).filter(Boolean)){const m=item.match(/^(\d+\.\d+\.\d+\.)(\d+)-(\d+)$/);if(m){for(let i=+m[2];i<=+m[3]&&i<=255;i++)out.push(m[1]+i)}else out.push(item)}return [...new Set(out)]})
async function inspect(){if(!hosts.value.length)return;running.value=true;try{const r=await api.post('/system/hardware-inspection/inspect',{hosts:hosts.value,username:username.value,port:port.value,auth_method:authMethod.value,password:password.value});results.value=r.results||[];comparison.value=r.comparison||{overview:[],same:[],different:[],nvme:[],nics:[],bandwidth:[]}}finally{running.value=false}}
</script><style scoped>
.page { min-height: 100vh; padding: 20px; background: linear-gradient(135deg, #6B5DD3 0%, #8B7FE8 50%, #9B8FF8 100%); position: relative; }
.page::before { content: ''; position: fixed; inset: 0; background: repeating-linear-gradient(45deg, transparent, transparent 35px, rgba(255,255,255,.05) 35px, rgba(255,255,255,.05) 70px); pointer-events: none; }
main { position: relative; z-index: 1; max-width: 1200px; margin: auto; }
section { background: rgba(255,255,255,.95); border-radius: 12px; padding: 20px; margin: 16px 0; box-shadow: 0 4px 6px rgba(0,0,0,.1); }
h2 { font-size: 18px; font-weight: 600; color: #333; margin: 0 0 15px; padding-bottom: 10px; border-bottom: 2px solid #f0f0f0; }
textarea, input, select { box-sizing: border-box; padding: 8px 12px; border: 1px solid #ddd; border-radius: 6px; font: 14px monospace; background: #fff; outline: none; }
textarea { width: 100%; min-height: 78px; resize: vertical; }
textarea:focus, input:focus, select:focus { border-color: #6B5DD3; box-shadow: 0 0 0 2px rgba(107,93,211,.12); }
.row { display: grid; grid-template-columns: 1fr 1fr; gap: 10px; margin: 12px 0; }
.auth-row { grid-template-columns: 1.2fr .55fr 1fr 1.2fr; }
.key-tip { display: flex; align-items: center; padding: 8px 12px; border-radius: 6px; background: #f3f1ff; color: #5f54b7; font-size: 13px; }
button { width: 100%; padding: 10px 20px; border: none; border-radius: 6px; font-size: 14px; font-weight: 600; cursor: pointer; color: #fff; background: linear-gradient(135deg, #6B5DD3 0%, #8B7FE8 100%); transition: all .2s; }
button:hover:not(:disabled) { transform: translateY(-2px); box-shadow: 0 5px 15px rgba(107,93,211,.4); }
button:disabled { opacity: .6; cursor: not-allowed; }
ul { margin: 0; padding-left: 20px; color: #444; line-height: 1.8; }
table { width: 100%; border-collapse: collapse; overflow: hidden; border-radius: 6px; }
th, td { border: 1px solid #e5e5e5; padding: 9px 10px; text-align: left; vertical-align: top; white-space: pre-wrap; word-break: break-word; }
th { background: #f3f1ff; color: #4f46a5; font-weight: 600; } tr:nth-child(even) td { background: #fcfcff; }
pre { white-space: pre; overflow: auto; background: #1e1e1e; color: #d9e3f0; padding: 16px; border-radius: 8px; font: 12px/1.5 monospace; }
.hint { color: #64748b; margin: -4px 0 12px; font-size: 13px; } .device-cell { min-width: 190px; line-height: 1.55; font-family: monospace; } .device-cell span { display: block; padding: 1px 3px; margin: 0 -3px; border-radius: 3px; } .device-cell .diff-field { background: #fff3cd; color: #7c4a03; } .ok { color: #15803d; font-weight: 700; } .warn { color: #b45309; font-weight: 700; } small { color: #6B5DD3; font-weight: 600; }
@media (max-width: 700px) { .page { padding: 12px; } .row { grid-template-columns: 1fr; } section { padding: 15px; } }
</style>
