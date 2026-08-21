from __future__ import print_function

import json
import time


def generate_disk_perf_html(results, models, output_path, aqu_sz_threshold=1000.0, cv_threshold=0.10):
    data = {
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "models": models,
        "nodes": results,
        "aqu_sz_threshold": aqu_sz_threshold,
        "cv_threshold": cv_threshold,
    }
    with open(output_path, "w") as fh:
        fh.write(build_disk_perf_html(data))


def build_disk_perf_html(data):
    payload = json.dumps(data, ensure_ascii=False)
    return '''<!doctype html>
<html>
<head>
<meta charset="utf-8">
<title>硬盘性能测试报告</title>
<style>
body { font-family: Arial, sans-serif; margin: 24px; color: #1f2933; background: #f6f8fb; }
h1, h2, h3 { margin: 12px 0; }
.card { background: white; border: 1px solid #d8dee9; border-radius: 8px; padding: 16px; margin: 16px 0; box-shadow: 0 1px 2px rgba(0,0,0,.04); }
details.card { display: block; }
summary { cursor: pointer; font-weight: bold; }
table { border-collapse: collapse; width: 100%; background: white; font-size: 13px; }
th, td { border: 1px solid #d8dee9; padding: 6px 8px; text-align: left; }
th { background: #edf2f7; }
.PASS { color: #0f7b35; font-weight: bold; }
.WARN { color: #a05a00; font-weight: bold; }
.FAIL, .ERROR { color: #b00020; font-weight: bold; }
.chart { width: 100%; height: 180px; border: 1px solid #d8dee9; background: #fff; margin: 8px 0 18px; }
.small { color: #66788a; font-size: 12px; }
.matrix-metric { display: block; color: #52616b; font-size: 11px; font-weight: normal; line-height: 1.45; white-space: nowrap; }
.grid { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; }
</style>
</head>
<body>
<h1>硬盘性能测试报告</h1>
<div class="card" id="summary"></div>
<div class="card"><h2>结果矩阵</h2><div id="matrix"></div></div>
<div id="details"></div>
<script>
var DATA = ''' + payload + ''';
function esc(s){ return String(s == null ? '' : s).replace(/[&<>]/g,function(c){return {'&':'&amp;','<':'&lt;','>':'&gt;'}[c];}); }
function fmt(v){ return (Math.round((v||0)*100)/100).toString(); }
function pct(v){ return (Math.round((v||0)*10000)/100).toString() + '%'; }
function modelName(m){ return {'4k_randread':'4K随机读','1m_read':'1M顺序读','4k_randwrite':'4K随机写','1m_write':'1M顺序写'}[m.id] || m.name || m.id; }
function metricLabel(k){ return {r_await_us:'读时延(us)',w_await_us:'写时延(us)',r_s:'读IOPS',w_s:'写IOPS',rMB_s:'读带宽(MB/s)',wMB_s:'写带宽(MB/s)',aqu_sz:'队列深度(aqu-sz)'}[k] || k; }
function translateError(e){
  if(!e) return '';
  return String(e)
    .replace(/performance_min=/g,'性能下限=')
    .replace(/performance=/g,'稳定阶段性能=')
    .replace(/latency_over=/g,'时延超阈值点数=')
    .replace(/latency_limit_us=/g,'时延阈值(us)=')
    .replace(/aqu_over=/g,'队列深度超阈值点数=')
    .replace(/cv_over=/g,'CV超阈值=')
    .replace(/fio failed rc=/g,'fio执行失败，返回码=')
    .replace(/no iostat data/g,'没有采集到iostat数据');
}

function performanceLabel(key){ return {r_s:'随机读IOPS',w_s:'随机写IOPS',rMB_s:'顺序读带宽(MB/s)',wMB_s:'顺序写带宽(MB/s)'}[key] || key; }
function performanceUnit(key){ return (key==='r_s' || key==='w_s') ? ' IOPS' : ' MB/s'; }
function fmtThousands(v){ return String(Math.round(v||0)).replace(/\B(?=(\d{3})+(?!\d))/g, ','); }
function summary(){
  var nodes = DATA.nodes || [], disks = 0, fail = 0, warn = 0, err = 0;
  nodes.forEach(function(n){ (n.disks||[]).forEach(function(d){ disks++; Object.keys(d.models||{}).forEach(function(mid){ var s=d.models[mid].status; if(s==='FAIL') fail++; else if(s==='WARN') warn++; else if(s==='ERROR') err++; }); }); });
  var p4=[], p5=[], lat=[];
  (DATA.models||[]).forEach(function(m){
    var lbl=performanceLabel(m.performance_metric), u=performanceUnit(m.performance_metric);
    p4.push(modelName(m)+' ≥'+fmtThousands(m.pcie4_min_performance)+u);
    p5.push(modelName(m)+' ≥'+fmtThousands(m.pcie5_min_performance)+u);
    if(m.pcie4_latency_us != null){
      lat.push(modelName(m)+'时延为 PCIe 4.0/5.0 的 '+m.pcie4_latency_us+'/'+m.pcie5_latency_us+' us');
    } else {
      lat.push(modelName(m)+'时延为 '+m.latency_us+' us');
    }
  });
  var aqu = DATA.aqu_sz_threshold || 1000, cv = DATA.cv_threshold || 0.10;
  var html = '<h2>总览</h2>'
    + '<p>生成时间：'+esc(DATA.generated_at)+'</p>'
    + '<p>节点数：'+nodes.length+'；硬盘数：'+disks+'；FAIL：'+fail+'；WARN：'+warn+'；ERROR：'+err+'</p>'
    + '<p>判定顺序：先检查稳定阶段性能是否达到对应 PCIe 代际下限，再检查时延、队列深度和波动率。稳定阶段内任意时延采样点达到阈值即失败；aqu-sz 必须 &lt; '+fmtThousands(aqu)+'；稳定阶段 IOPS/带宽 CV 必须 &lt; '+Math.round(cv*100)+'%。</p>'
    + '<p>PCIe 4.0：'+p4.join('、')+'。</p>'
    + '<p>PCIe 5.0：'+p5.join('、')+'。</p>'
    + '<p>未识别的 PCIe 代际按 PCIe 4.0 下限判断。</p>'
    + '<p>时延阈值：'+lat.join('；')+'。</p>';
  document.getElementById('summary').innerHTML = html;
}
function acceptanceLine(r){
  var key=r.performance_metric, actual=Number(r.performance_value||0), minimum=Number(r.performance_minimum||((r.thresholds||{})[key]||0));
  if(!key) return '性能验收数据缺失';
  return '验收指标：'+performanceLabel(key)+' 稳定阶段平均='+fmt(actual)+performanceUnit(key)+'，下限='+fmt(minimum)+performanceUnit(key);
}
function matrixMetric(v, label, unit){
  return '<span class="matrix-metric">'+label+': '+fmt(v)+(unit||'')+'</span>';
}
function matrixCell(r, m){
  var s=r.summary||{}, latency=s[m.latency_metric]||{}, throughputKey=m.rw.indexOf('read')>=0 ? (m.bs==='4K'?'r_s':'rMB_s') : (m.bs==='4K'?'w_s':'wMB_s');
  var throughput=s[throughputKey]||{};
  var html='<td class="'+esc(r.status||'')+'"><strong>'+esc(r.status||'-')+'</strong>';
  html+=matrixMetric(latency.stable_avg, '时延', ' us');
  html+=matrixMetric(latency.cv*100, '时延CV', '%');
  html+=matrixMetric(throughput.stable_avg, m.bs==='4K'?'IOPS':'带宽', m.bs==='4K'?'':' MB/s');
  html+=matrixMetric(throughput.cv*100, '指标CV', '%');
  return html+'</td>';
}
function matrix(){
  var html='<table><tr><th>节点</th><th>硬盘</th><th>PCIe版本</th>';
  DATA.models.forEach(function(m){ html+='<th>'+esc(modelName(m))+'</th>'; }); html+='</tr>';
  DATA.nodes.forEach(function(n){ (n.disks||[]).forEach(function(d){ html+='<tr><td>'+esc(n.ip)+'</td><td>'+esc(d.path)+'</td><td>'+esc(d.pcie_generation)+'</td>'; DATA.models.forEach(function(m){ html+=matrixCell((d.models||{})[m.id]||{}, m); }); html+='</tr>'; }); });
  html+='</table>'; document.getElementById('matrix').innerHTML=html;
}
function draw(svg, points, key, threshold){
  var w = svg.clientWidth || 600, h = svg.clientHeight || 180, pad = 28;
  while(svg.firstChild) svg.removeChild(svg.firstChild);
  if(!points || !points.length){ svg.innerHTML='<text x="20" y="40">无数据</text>'; return; }
  var vals = points.map(function(p){return Number(p[key]||0);});
  var max = Math.max.apply(null, vals.concat([threshold||0, 1]));
  function x(i){ return pad + (w-pad*2) * (i / Math.max(points.length-1,1)); }
  function y(v){ return h-pad - (h-pad*2) * (v / max); }
  var axis = document.createElementNS('http://www.w3.org/2000/svg','path'); axis.setAttribute('d','M'+pad+' '+pad+' L'+pad+' '+(h-pad)+' L'+(w-pad)+' '+(h-pad)); axis.setAttribute('stroke','#334e68'); axis.setAttribute('fill','none'); svg.appendChild(axis);
  var d=''; vals.forEach(function(v,i){ d += (i?' L':'M')+x(i)+' '+y(v); });
  var path=document.createElementNS('http://www.w3.org/2000/svg','path'); path.setAttribute('d',d); path.setAttribute('stroke','#2563eb'); path.setAttribute('fill','none'); path.setAttribute('stroke-width','2'); svg.appendChild(path);
  if(threshold){ var ty=y(threshold); var th=document.createElementNS('http://www.w3.org/2000/svg','path'); th.setAttribute('d','M'+pad+' '+ty+' L'+(w-pad)+' '+ty); th.setAttribute('stroke','#dc2626'); th.setAttribute('stroke-dasharray','5,4'); svg.appendChild(th); }
  var label=document.createElementNS('http://www.w3.org/2000/svg','text'); label.setAttribute('x',pad); label.setAttribute('y',16); label.textContent=metricLabel(key)+' 最大值='+fmt(max); svg.appendChild(label);
}
function statLine(r, key){
  var s=(r.summary||{})[key]||{};
  return metricLabel(key)+'：平均='+fmt(s.avg)+'，最大='+fmt(s.max)+'，稳定阶段平均='+fmt(s.stable_avg)+'，CV='+pct(s.cv)+'，极差比='+pct(s.range_ratio);
}
function chartSeries(r){ return r.series||[]; }
function chartThreshold(r, key){ return (r.thresholds||{})[key]; }
function diskStatus(d){
  var status='PASS';
  Object.keys(d.models||{}).forEach(function(mid){ var s=d.models[mid].status; if(s==='FAIL' || s==='ERROR') status=s; else if(s==='WARN' && status==='PASS') status='WARN'; });
  return status;
}
function statusRank(s){ return {'FAIL':0,'ERROR':1,'WARN':2,'PASS':3}[s] == null ? 4 : {'FAIL':0,'ERROR':1,'WARN':2,'PASS':3}[s]; }
function details(){
  var root=document.getElementById('details'), html='';
  DATA.nodes.forEach(function(n){
    html+='<div class="card"><h2>节点 '+esc(n.ip)+' <span class="'+esc(n.status)+'">'+esc(n.status)+'</span></h2>';
    (n.errors||[]).forEach(function(e){ html+='<p class="ERROR">'+esc(translateError(e))+'</p>'; });
    var disks=(n.disks||[]).slice().sort(function(a,b){ var sa=diskStatus(a), sb=diskStatus(b); if(statusRank(sa)!==statusRank(sb)) return statusRank(sa)-statusRank(sb); return String(a.path).localeCompare(String(b.path)); });
    disks.forEach(function(d){
      var ds=diskStatus(d), openAttr=(ds==='PASS'?'':' open');
      html+='<details class="card"'+openAttr+'><summary>'+esc(d.path)+' PCIe '+esc(d.pcie_generation)+' <span class="'+esc(ds)+'">'+esc(ds)+'</span></summary>';
      DATA.models.forEach(function(m){ var r=(d.models||{})[m.id]||{}; var latencyLimit=(r.thresholds||{})[m.latency_metric]; html+='<div class="card"><h3>'+esc(modelName(m))+' <span class="'+esc(r.status)+'">'+esc(r.status)+'</span></h3><p class="small">'+esc(acceptanceLine(r))+'</p><p class="small">'+esc(translateError(r.error||''))+'</p><p class="small">时延验收：'+esc(metricLabel(m.latency_metric))+' 阈值='+esc(fmt(latencyLimit))+' us；'+esc(statLine(r, m.latency_metric))+'；'+esc(statLine(r, 'aqu_sz'))+'</p><p class="small">'+esc(statLine(r, (m.rw.indexOf('read')>=0?'r_s':'w_s')))+'；'+esc(statLine(r, (m.rw.indexOf('read')>=0?'rMB_s':'wMB_s')))+'</p><div class="grid">'; ['r_await_us','w_await_us','rMB_s','wMB_s','r_s','w_s','aqu_sz'].forEach(function(k){ html+='<div><div class="small">'+metricLabel(k)+'</div><svg class="chart" data-node="'+esc(n.ip)+'" data-disk="'+esc(d.path)+'" data-model="'+esc(m.id)+'" data-metric="'+k+'"></svg></div>'; }); html+='</div></div>'; });
      html+='</details>';
    });
    html+='</div>';
  });
  root.innerHTML=html;
  Array.prototype.forEach.call(document.querySelectorAll('details[open] svg.chart'), function(svg){ var n=DATA.nodes.filter(function(x){return x.ip===svg.getAttribute('data-node');})[0]; var d=(n.disks||[]).filter(function(x){return x.path===svg.getAttribute('data-disk');})[0]; var m=(d.models||{})[svg.getAttribute('data-model')]||{}; var key=svg.getAttribute('data-metric'); draw(svg, chartSeries(m), key, chartThreshold(m, key)); });
  Array.prototype.forEach.call(document.querySelectorAll('details'), function(det){ det.addEventListener('toggle', function(){ if(!det.open) return; Array.prototype.forEach.call(det.querySelectorAll('svg.chart'), function(svg){ if(svg.getAttribute('data-drawn')==='1') return; var n=DATA.nodes.filter(function(x){return x.ip===svg.getAttribute('data-node');})[0]; var d=(n.disks||[]).filter(function(x){return x.path===svg.getAttribute('data-disk');})[0]; var m=(d.models||{})[svg.getAttribute('data-model')]||{}; var key=svg.getAttribute('data-metric'); draw(svg, chartSeries(m), key, chartThreshold(m, key)); svg.setAttribute('data-drawn','1'); }); }); });
}
summary(); matrix(); details();
</script>
</body>
</html>
'''
